# core/services/digest_email.py
"""Weekly operator digest — composition and send orchestration.

The dashboard family (``/staff/dashboard/*``) is reactive; this module
makes Castor proactive. ``send_digest()`` is the single entry point used
by both the ``send_weekly_digest`` management command (run from cron) and
the "Send digest now" admin action. Everything else is pure rendering so
it's cheap to test without touching the SMTP relay.

The orchestration logic intentionally mirrors ``beta.views`` —
fail-silently, log-and-swallow, respect ``beta.throttle`` for the Brevo
daily cap. A failed digest send must never raise out of the cron job.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from beta.throttle import bump_daily_count, today_send_count
from core.models import WeeklyDigestConfig
from core.services import usage_analytics

logger = logging.getLogger(__name__)


@dataclass
class DigestResult:
    """Return value of :func:`send_digest`.

    ``status`` is a :class:`WeeklyDigestConfig.Status` value. ``log`` is a
    short human-readable string suitable for the audit trail and the
    admin's "last send" panel.
    """

    status: str
    log: str


# Match the spec from the plan — pure helpers below the orchestrator.


def build_digest_context(week_ending: date | None = None) -> dict:
    """Compose every value the email template needs.

    Pulls from :mod:`core.services.usage_analytics` — the same helpers the
    staff dashboard renders. Returns a flat dict whose keys mirror the
    sections in ``weekly_digest.html``. Respects the ``include_*`` flags
    on :class:`WeeklyDigestConfig` so a section the operator turned off
    isn't computed at all (saves a few queries on every send).
    """
    cfg = WeeklyDigestConfig.load()
    week_ending = week_ending or timezone.now().date()
    site_url = getattr(settings, "SITE_URL", "https://castoriq.io")

    ctx: dict = {
        "week_ending": week_ending.isoformat(),
        "site_url": site_url.rstrip("/"),
        "dashboard_url": f"{site_url.rstrip('/')}/staff/dashboard/overview/",
        "admin_url": f"{site_url.rstrip('/')}/admin/core/weeklydigestconfig/",
        "sections": [],
    }

    if cfg.include_investor_kpis:
        kpis = usage_analytics.investor_kpis()
        ctx["sections"].append(
            {
                "key": "investor",
                "heading": "Investor KPIs",
                "lines": _investor_lines(kpis),
            }
        )

    if cfg.include_cost_summary:
        kpis7 = usage_analytics.kpis(window_days=7)
        kpis_prev = usage_analytics.kpis(window_days=14)  # 14d − 7d = prior 7d
        top_users = usage_analytics.top_users_by_cost(window_days=7, limit=1)
        mix = usage_analytics.provider_mix_summary(window_days=7)
        ctx["sections"].append(
            {
                "key": "cost",
                "heading": "Cost (last 7 days)",
                "lines": _cost_lines(kpis7, kpis_prev, top_users, mix),
            }
        )

    if cfg.include_reliability_summary:
        rel = usage_analytics.kpis_for_reliability(window_days=7)
        backlog = usage_analytics.unresolved_error_backlog()
        ctx["sections"].append(
            {
                "key": "reliability",
                "heading": "Reliability (last 7 days)",
                "lines": _reliability_lines(rel, backlog),
            }
        )

    if cfg.include_engagement_summary:
        eng = usage_analytics.engagement_kpis(window_days=7)
        cohort = usage_analytics.cohort_retention_grid(weeks=8)
        ctx["sections"].append(
            {
                "key": "engagement",
                "heading": "Engagement",
                "lines": _engagement_lines(eng, cohort),
            }
        )

    if cfg.include_top_users_table:
        ctx["top_users"] = usage_analytics.top_users_by_cost(window_days=7, limit=10)

    return ctx


def render_digest_html(context: dict) -> str:
    """Render the HTML body — 560 px inline-CSS, light theme only."""
    return render_to_string("core/emails/weekly_digest.html", context)


def render_digest_text(context: dict) -> str:
    """Render the plain-text body — fallback for clients without HTML view."""
    return render_to_string("core/emails/weekly_digest.txt", context)


def send_digest(*, force: bool = False, dry_run: bool = False) -> DigestResult:
    """Send the weekly digest. Orchestrates config gates → render → SMTP.

    ``force=True`` ignores ``enabled`` and ``send_day_of_week`` checks
    (used by the admin action and the ``--force`` CLI flag).
    ``dry_run=True`` writes the rendered HTML to ``/tmp`` and skips the
    SMTP call entirely — useful for previewing without burning the cap.

    All branches update :class:`WeeklyDigestConfig.last_*` so the admin
    audit panel always reflects the most recent attempt.
    """
    cfg = WeeklyDigestConfig.load()
    Status = WeeklyDigestConfig.Status

    if not force and not cfg.enabled:
        return _record(cfg, Status.SKIPPED_DISABLED, "Digest disabled in admin.")

    today_dow = timezone.now().weekday()
    if not force and today_dow != cfg.send_day_of_week:
        return _record(
            cfg,
            Status.SKIPPED_WRONG_DAY,
            f"Today is dow={today_dow}; configured send day is {cfg.send_day_of_week}.",
        )

    recipients = [r for r in (cfg.recipients or []) if isinstance(r, str) and r.strip()]
    if not recipients:
        return _record(cfg, Status.SKIPPED_NO_RECIPIENTS, "No recipients configured.")

    cap = getattr(settings, "BETA_DAILY_TOTAL_CAP", 290)
    sent_today = today_send_count()
    if sent_today + len(recipients) > cap:
        return _record(
            cfg,
            Status.SKIPPED_QUOTA,
            f"Brevo cap: {sent_today} sent today, {len(recipients)} would push past {cap}.",
        )

    context = build_digest_context(week_ending=timezone.now().date())
    html_body = render_digest_html(context)
    text_body = render_digest_text(context)

    if dry_run:
        # Write to a stable per-day filename so repeated dry-runs don't
        # litter /tmp with timestamped duplicates.
        path = Path(tempfile.gettempdir()) / f"castor-digest-{context['week_ending']}.html"
        path.write_text(html_body, encoding="utf-8")
        return _record(cfg, Status.SUCCESS, f"Dry run — wrote {path}")

    subject = f"Castor weekly digest — week ending {context['week_ending']}"
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipients[0]],
            bcc=recipients[1:] or None,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.warning("Weekly digest send failed: %s", exc)
        return _record(cfg, Status.FAILED, f"{type(exc).__name__}: {exc}")

    bump_daily_count(len(recipients))
    return _record(
        cfg,
        Status.SUCCESS,
        f"Sent to {len(recipients)} recipient(s): {recipients[0]}"
        + (f" + {len(recipients) - 1} bcc" if len(recipients) > 1 else ""),
    )


# ── helpers ─────────────────────────────────────────────────────────────────


def _record(cfg: WeeklyDigestConfig, status: str, log: str) -> DigestResult:
    """Persist the attempt to the audit fields and return the result."""
    cfg.last_sent_at = timezone.now()
    cfg.last_send_status = status
    cfg.last_send_log = (log or "")[:2000]
    cfg.save(update_fields=["last_sent_at", "last_send_status", "last_send_log", "updated_at"])
    return DigestResult(status=status, log=log)


def _fmt_or_dash(value, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value}{suffix}"


def _investor_lines(kpis: dict) -> list[str]:
    return [
        f"MAU (30d): {kpis['mau_30d']}",
        f"W4 retention (avg of {kpis['cohorts_with_w4']} cohorts): "
        f"{_fmt_or_dash(kpis['avg_w4_retention_pct'], '%')}",
        f"Entities processed (all-time): {kpis['total_entities_processed']}",
        f"Ollama-local share: {_fmt_or_dash(kpis['local_token_share_pct'], '%')}",
    ]


def _cost_lines(kpis7: dict, kpis_prev: dict, top_users: list, mix: dict) -> list[str]:
    this_week = float(kpis7["cost_usd"] or 0)
    last_two_weeks = float(kpis_prev["cost_usd"] or 0)
    prior_week = max(0.0, last_two_weeks - this_week)
    delta = this_week - prior_week
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "·")
    top_line = (
        f"Top spender: {top_users[0]['username']} (${top_users[0]['cost_usd']:.4f})"
        if top_users
        else "Top spender: —"
    )
    return [
        f"Cost this week: ${this_week:.4f} ({arrow} ${abs(delta):.4f} vs prior week)",
        top_line,
        f"Ollama-local share: {_fmt_or_dash(mix['local_pct'], '%')} "
        f"({mix['local_tokens']:,} of {mix['total_tokens']:,} tokens)",
    ]


def _reliability_lines(rel: dict, backlog: dict) -> list[str]:
    return [
        f"Success %: {_fmt_or_dash(rel['success_pct'], '%')} (of {rel['calls']} calls)",
        f"p95 latency: Ask {_fmt_or_dash(rel['p95_latency_ask_ms'], ' ms')} · "
        f"Modify {_fmt_or_dash(rel['p95_latency_modify_ms'], ' ms')}",
        f"Errors this week: {rel['errors']} · Open backlog: {backlog['total']} unresolved",
    ]


def _engagement_lines(eng: dict, cohort: dict) -> list[str]:
    w4_pcts = [row[4] for row in cohort["grid"] if len(row) > 4 and row[4] is not None]
    avg_w4 = round(sum(w4_pcts) / len(w4_pcts), 1) if w4_pcts else None
    return [
        f"DAU / WAU / MAU: {eng['dau']} / {eng['wau']} / {eng['mau']} "
        f"(stickiness {_fmt_or_dash(eng['stickiness_pct'], '%')})",
        f"Power users (Modify): {_fmt_or_dash(eng['proposal_generators_pct'], '%')} of active",
        f"W4 retention: {_fmt_or_dash(avg_w4, '%')} across {len(w4_pcts)} mature cohort(s)",
    ]


# Re-export so admin.py and the management command have a single import surface.
__all__ = [
    "DigestResult",
    "build_digest_context",
    "render_digest_html",
    "render_digest_text",
    "send_digest",
]
