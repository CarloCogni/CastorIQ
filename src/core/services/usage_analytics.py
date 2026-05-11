# core/services/usage_analytics.py
"""Aggregate queries for the staff-only usage dashboard.

Pure read; no side effects. Each function is keyed off a window in days so
the same helper can drive both the 24h and 7d KPI strips. All queries hit
existing indexes on ``LLMCallLog`` (``[user, -created_at]``,
``[provider, -created_at]``, plus the default on ``created_at``).

This module is the single source of truth for dashboard aggregations — every
tab (Overview, Cost, Reliability, Engagement, Quality, Security, Investor)
calls helpers from here. Views stay thin per the Castor service-layer rule.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import (
    Count,
    DecimalField,
    F,
    Max,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDay
from django.utils import timezone

from core.models import ErrorLog, LLMCallLog, UserTokenBudget

User = get_user_model()


def _since(days: int) -> datetime:
    """UTC cutoff ``days`` ago, used as the ``created_at__gte`` lower bound."""
    return timezone.now() - timedelta(days=days)


def _percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile on a pre-sorted list of ints. None when empty.

    Falls back to a Python implementation rather than depending on the
    Postgres ``percentile_cont`` aggregate — keeps the helper portable for
    SQLite-backed test runs and avoids an ORM-level migration to register
    ``Percentile`` as an aggregate.
    """
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = max(0, min(len(values) - 1, math.ceil(q * len(values)) - 1))
    return values[rank]


def kpis(window_days: int) -> dict[str, Any]:
    """Six-tile KPI strip values.

    Returns: ``calls``, ``tokens``, ``cost_usd``, ``active_users``,
    ``errors``, ``p95_latency_ms``.
    """
    since = _since(window_days)
    qs = LLMCallLog.objects.filter(created_at__gte=since)

    aggs = qs.aggregate(
        calls=Count("id"),
        tokens=Coalesce(
            Sum(F("tokens_in") + F("tokens_out")),
            Value(0),
        ),
        cost_usd=Coalesce(
            Sum("estimated_cost_usd"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=6),
        ),
        active_users=Count("user", distinct=True),
        llm_errors=Count("id", filter=~_succeeded()),
    )

    error_log_count = ErrorLog.objects.filter(created_at__gte=since).count()

    latencies = list(
        qs.exclude(latency_ms__isnull=True).values_list("latency_ms", flat=True)
    )

    return {
        "window_days": window_days,
        "calls": aggs["calls"] or 0,
        "tokens": int(aggs["tokens"] or 0),
        "cost_usd": Decimal(aggs["cost_usd"] or 0),
        "active_users": aggs["active_users"] or 0,
        "errors": (aggs["llm_errors"] or 0) + error_log_count,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def tokens_per_day(window_days: int) -> dict[str, Any]:
    """Tokens summed per day, split by provider, for stacked-area chart.

    Returns ``{"labels": ["YYYY-MM-DD", ...], "providers": ["ollama", ...],
    "series": {provider: [int, int, ...]}}`` aligned to ``labels``.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDay("created_at"))
        .values("day", "provider")
        .annotate(total=Sum(F("tokens_in") + F("tokens_out")))
        .order_by("day", "provider")
    )
    return _bucket_by_day_and_provider(rows, window_days, value_key="total", as_int=True)


def cost_per_day(window_days: int) -> dict[str, Any]:
    """USD cost per day, split by provider, for stacked-area chart with budget line."""
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDay("created_at"))
        .values("day", "provider")
        .annotate(total=Sum("estimated_cost_usd"))
        .order_by("day", "provider")
    )
    return _bucket_by_day_and_provider(rows, window_days, value_key="total", as_int=False)


def top_users_by_cost(window_days: int, limit: int = 10) -> list[dict[str, Any]]:
    """Top N users by 7-day cost, with calls/tokens/blocked status for the table."""
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since, user__isnull=False)
        .values("user_id", "user__username")
        .annotate(
            calls=Count("id"),
            tokens=Sum(F("tokens_in") + F("tokens_out")),
            cost_usd=Sum("estimated_cost_usd"),
        )
        .order_by("-cost_usd")[:limit]
    )

    blocked_ids = set(
        UserTokenBudget.objects.filter(
            user_id__in=[r["user_id"] for r in rows], hard_blocked=True
        ).values_list("user_id", flat=True)
    )

    return [
        {
            "user_id": r["user_id"],
            "username": r["user__username"],
            "calls": r["calls"] or 0,
            "tokens": int(r["tokens"] or 0),
            "cost_usd": Decimal(r["cost_usd"] or 0),
            "hard_blocked": r["user_id"] in blocked_ids,
        }
        for r in rows
    ]


# ── Tab 2: Cost & Usage Drill-down ──────────────────────────────────────────


def cost_per_day_by_purpose(window_days: int) -> dict[str, Any]:
    """USD cost per day, split by ``purpose`` (Ask vs Modify).

    Same shape as ``cost_per_day`` but pivoted on Ask/Modify rather than
    provider. Drives the "where does the money go — RAG or write-back?"
    chart on the Cost tab.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDay("created_at"))
        .values("day", "purpose")
        .annotate(total=Sum("estimated_cost_usd"))
        .order_by("day", "purpose")
    )
    return _bucket_by_day_and_category(
        rows,
        window_days,
        value_key="total",
        as_int=False,
        category_key="purpose",
    )


def tokens_per_day_local_vs_paid(window_days: int) -> dict[str, Any]:
    """Tokens per day, with providers collapsed into ``local`` vs ``paid``.

    Local = Ollama. Paid = anything else (Anthropic, Groq, future cloud
    providers). The Ollama-vs-paid mix is the gross-margin frame: any drift
    of traffic from local toward paid silently erodes Castor's economics.
    """
    since = _since(window_days)
    rows = list(
        LLMCallLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDay("created_at"))
        .values("day", "provider")
        .annotate(total=Sum(F("tokens_in") + F("tokens_out")))
        .order_by("day", "provider")
    )
    # Collapse provider → class in Python so the SQL stays a single GROUP BY.
    for r in rows:
        r["category"] = "local" if (r["provider"] or "").lower() == "ollama" else "paid"
    return _bucket_by_day_and_category(
        rows,
        window_days,
        value_key="total",
        as_int=True,
        category_key="category",
    )


def cost_per_active_user_per_day(window_days: int) -> dict[str, Any]:
    """Daily ratio: total USD cost ÷ distinct active users on that day.

    Vanity-resistant: dividing by DAU keeps the line flat as the user base
    grows so long as per-user spend is stable. A rising line means *each*
    user is costing more — that's the signal worth investigating.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(
            cost=Sum("estimated_cost_usd"),
            users=Count("user", distinct=True),
        )
        .order_by("day")
    )
    by_day = {
        r["day"].date().isoformat(): r for r in rows if r["day"] is not None
    }
    today = timezone.now().date()
    labels: list[str] = []
    values: list[float] = []
    for i in range(window_days):
        day = today - timedelta(days=window_days - 1 - i)
        label = day.isoformat()
        labels.append(label)
        row = by_day.get(label)
        if row and (row["users"] or 0) > 0:
            values.append(float(Decimal(row["cost"] or 0) / row["users"]))
        else:
            values.append(0.0)
    return {"labels": labels, "values": values}


def user_budget_strip(activity_window_days: int = 7) -> list[dict[str, Any]]:
    """Per-user daily-budget heat strip — every recently active user.

    "Recently active" = either ``used_today > 0`` right now OR has logged
    at least one LLM call in the last ``activity_window_days``. Filtering
    keeps inactive accounts from drowning the list.

    The percent calculation handles the unlimited case (``daily_cap == 0``)
    by returning ``None`` so the template can render "—" instead of 0/0.
    """
    from django.db.models import Q

    since = timezone.now() - timedelta(days=activity_window_days)
    recent_user_ids = set(
        LLMCallLog.objects.filter(created_at__gte=since, user__isnull=False)
        .values_list("user_id", flat=True)
        .distinct()
    )
    budgets = (
        UserTokenBudget.objects.select_related("user")
        .filter(Q(used_today__gt=0) | Q(user_id__in=recent_user_ids))
        .order_by("-hard_blocked", "-used_today")
    )
    out: list[dict[str, Any]] = []
    for b in budgets:
        cap = b.daily_cap or 0
        percent: float | None
        if cap > 0:
            percent = round(min(b.used_today / cap, 2.0) * 100, 1)
        else:
            percent = None
        out.append(
            {
                "user_id": b.user_id,
                "username": b.user.username if b.user_id else "—",
                "used_today": b.used_today,
                "daily_cap": cap,
                "percent": percent,
                "hard_blocked": b.hard_blocked,
                "last_reset_at": b.last_reset_at,
            }
        )
    return out


# ── Tab 3: Reliability & Performance ───────────────────────────────────────


def kpis_for_reliability(window_days: int) -> dict[str, Any]:
    """Reliability KPI strip: success %, errors, p95 latency Ask, p95 latency Modify.

    Returns a flat dict with the four tile values plus the underlying counts
    for sanity-checking. One pass over ``LLMCallLog`` for the window —
    cheaper than calling :func:`kpis` and then re-querying for latency.
    """
    since = _since(window_days)
    qs = LLMCallLog.objects.filter(created_at__gte=since)

    # Aggregate alias names must NOT collide with model field names —
    # ``.aggregate(succeeded=...)`` would try to resolve `succeeded` as the
    # underlying field. Use ``succeeded_count``.
    aggs = qs.aggregate(
        calls=Count("id"),
        succeeded_count=Count("id", filter=_succeeded()),
        llm_errors=Count("id", filter=~_succeeded()),
    )
    error_log_count = ErrorLog.objects.filter(created_at__gte=since).count()

    ask_latencies = list(
        qs.filter(
            purpose=LLMCallLog.Purpose.ASK, latency_ms__isnull=False
        ).values_list("latency_ms", flat=True)
    )
    modify_latencies = list(
        qs.filter(
            purpose=LLMCallLog.Purpose.MODIFY, latency_ms__isnull=False
        ).values_list("latency_ms", flat=True)
    )

    calls = aggs["calls"] or 0
    success_pct = (
        round(((aggs["succeeded_count"] or 0) / calls) * 100, 1) if calls else None
    )

    return {
        "window_days": window_days,
        "calls": calls,
        "success_pct": success_pct,
        "errors": (aggs["llm_errors"] or 0) + error_log_count,
        "p95_latency_ask_ms": _percentile(ask_latencies, 0.95),
        "p95_latency_modify_ms": _percentile(modify_latencies, 0.95),
    }


def provider_success_rate(window_days: int) -> list[dict[str, Any]]:
    """Success % per provider over the window.

    Excludes providers with zero calls — keeps the bar chart visually clean
    and avoids 0/0 percentages. Sorted by call volume desc so the busiest
    provider sits leftmost.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .values("provider")
        .annotate(
            calls=Count("id"),
            succeeded_count=Count("id", filter=_succeeded()),
        )
        .order_by("-calls")
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        calls = r["calls"] or 0
        if calls == 0:
            continue
        succ = r["succeeded_count"] or 0
        out.append(
            {
                "provider": r["provider"] or "unknown",
                "calls": calls,
                "succeeded": succ,
                "success_pct": round(succ / calls * 100, 1),
            }
        )
    return out


def p95_latency_per_purpose_per_day(window_days: int) -> dict[str, Any]:
    """Daily p95 latency split by purpose (Ask vs Modify).

    Returns ``{"labels": [...], "series": {"ask": [...], "modify": [...]}}``
    where each series value is the p95 latency in ms for that day, or
    ``None`` when no calls landed on that day. Chart.js renders gaps when
    ``spanGaps`` is enabled — preserves the "we had no traffic" signal.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(
            created_at__gte=since, latency_ms__isnull=False
        )
        .annotate(day=TruncDay("created_at"))
        .values("day", "purpose", "latency_ms")
    )

    by_day_purpose: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        day_label = r["day"].date().isoformat() if r["day"] else None
        if not day_label:
            continue
        key = (day_label, r["purpose"] or "")
        by_day_purpose.setdefault(key, []).append(r["latency_ms"])

    today = timezone.now().date()
    labels = [
        (today - timedelta(days=window_days - 1 - i)).isoformat()
        for i in range(window_days)
    ]

    series: dict[str, list[int | None]] = {"ask": [], "modify": []}
    for label in labels:
        for purpose_key, purpose_name in (
            (LLMCallLog.Purpose.ASK, "ask"),
            (LLMCallLog.Purpose.MODIFY, "modify"),
        ):
            samples = by_day_purpose.get((label, purpose_key), [])
            series[purpose_name].append(_percentile(samples, 0.95) if samples else None)

    return {"labels": labels, "series": series}


def error_type_breakdown(
    window_days: int, limit: int = 10
) -> dict[str, Any]:
    """Top error categories from ``LLMCallLog.error_type`` ∪ ``ErrorLog.exception_type``.

    Free-form text on both sides; we normalise blanks to ``"(unknown)"`` and
    union by exact label. Anything past ``limit`` collapses into an
    ``other`` bucket so the donut stays readable. ``total`` covers
    everything including ``other``.
    """
    since = _since(window_days)
    counter: dict[str, int] = {}

    llm_rows = (
        LLMCallLog.objects.filter(created_at__gte=since, succeeded=False)
        .values("error_type")
        .annotate(n=Count("id"))
    )
    for r in llm_rows:
        label = (r["error_type"] or "").strip() or "(unknown)"
        counter[label] = counter.get(label, 0) + (r["n"] or 0)

    err_rows = (
        ErrorLog.objects.filter(created_at__gte=since)
        .values("exception_type")
        .annotate(n=Count("id"))
    )
    for r in err_rows:
        label = (r["exception_type"] or "").strip() or "(unknown)"
        counter[label] = counter.get(label, 0) + (r["n"] or 0)

    ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
    top = [{"label": k, "count": v} for k, v in ranked[:limit]]
    other = sum(v for _, v in ranked[limit:])
    return {
        "top": top,
        "other": other,
        "total": sum(counter.values()),
    }


def unresolved_error_backlog() -> dict[str, Any]:
    """Headline count + age buckets + the 10 most-recent unresolved rows.

    Age buckets are absolute (not window-respecting): a 5-day-old open
    error matters regardless of the window selector. The recent list is
    capped at 10 to keep the page light.
    """
    now = timezone.now()
    unresolved = ErrorLog.objects.filter(is_resolved=False)
    total = unresolved.count()

    buckets = [
        {
            "label": "<24h",
            "count": unresolved.filter(
                created_at__gte=now - timedelta(hours=24)
            ).count(),
        },
        {
            "label": "24h–7d",
            "count": unresolved.filter(
                created_at__gte=now - timedelta(days=7),
                created_at__lt=now - timedelta(hours=24),
            ).count(),
        },
        {
            "label": ">7d",
            "count": unresolved.filter(
                created_at__lt=now - timedelta(days=7)
            ).count(),
        },
    ]

    recent_qs = unresolved.order_by("-created_at")[:10].values(
        "id", "severity", "exception_type", "view_name", "message", "created_at"
    )
    recent = list(recent_qs)

    return {
        "total": total,
        "buckets": buckets,
        "recent": recent,
    }


def failure_record_taxonomy(window_days: int) -> dict[str, Any]:
    """Writeback ``FailureRecord`` taxonomy: phase × category grid + tier totals.

    Returns ``total=0`` (and an empty grid) when nothing has happened in
    the window — the template uses that to render the "quiet" empty state
    rather than drawing a chart with no bars. The deferred import keeps
    this module decoupled from the metacastor app's load order.
    """
    from metacastor.models import FailureRecord

    since = _since(window_days)
    qs = FailureRecord.objects.filter(created_at__gte=since)
    total = qs.count()

    phases = [p.value for p in FailureRecord.FailurePhase]
    categories = [c.value for c in FailureRecord.Category]
    grid = [[0 for _ in categories] for _ in phases]
    tier_totals: dict[int | None, int] = {1: 0, 2: 0, 3: 0, None: 0}

    if total:
        rows_by_phase_cat = (
            qs.values("failure_phase", "category").annotate(n=Count("id"))
        )
        phase_idx = {p: i for i, p in enumerate(phases)}
        cat_idx = {c: i for i, c in enumerate(categories)}
        for r in rows_by_phase_cat:
            i = phase_idx.get(r["failure_phase"])
            j = cat_idx.get(r["category"])
            if i is not None and j is not None:
                grid[i][j] = r["n"] or 0

        for r in qs.values("tier").annotate(n=Count("id")):
            key = r["tier"]
            tier_totals[key] = (tier_totals.get(key) or 0) + (r["n"] or 0)

    return {
        "phases": phases,
        "categories": categories,
        "grid": grid,
        "tier_totals": tier_totals,
        "total": total,
    }


def ingestion_status(window_days: int) -> dict[str, Any]:
    """Document + IFCFile pipeline health, last ``window_days``.

    Both models share the same ``Status`` choices (pending/processing/
    completed/failed) so the helper packages them with identical shape for
    a side-by-side rendering. Success % = completed / total (excludes
    pending/processing — they haven't reached a terminal state yet).
    """
    from documents.models import Document
    from ifc_processor.models import IFCFile

    since = _since(window_days)
    statuses = ["pending", "processing", "completed", "failed"]

    def _summarise(model) -> dict[str, Any]:
        qs = model.objects.filter(created_at__gte=since)
        total = qs.count()
        by_status: dict[str, int] = {s: 0 for s in statuses}
        for r in qs.values("status").annotate(n=Count("id")):
            by_status[r["status"]] = r["n"] or 0
        terminal = by_status["completed"] + by_status["failed"]
        success_pct = (
            round(by_status["completed"] / terminal * 100, 1) if terminal else None
        )
        return {
            "total": total,
            "by_status": by_status,
            "success_pct": success_pct,
        }

    return {
        "documents": _summarise(Document),
        "ifc_files": _summarise(IFCFile),
    }


# ── Tab 4: Engagement & Cohorts ────────────────────────────────────────────


def engagement_kpis(window_days: int) -> dict[str, Any]:
    """Top-line engagement tiles: DAU, WAU, MAU, stickiness, proposal share.

    DAU is "today" (calendar day, UTC). WAU/MAU are rolling 7/30-day distinct
    user counts. Stickiness = DAU / MAU expressed as a percent. Proposal-
    generator share = users who created at least one ``ModificationProposal``
    in the window ÷ users who fired any LLM call in the window — a quick
    "Ask-only vs power-user" proxy without computing the full feature mix.
    """
    from writeback.models import ModificationProposal

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    dau = (
        LLMCallLog.objects.filter(
            created_at__gte=today_start, user__isnull=False
        )
        .values("user_id")
        .distinct()
        .count()
    )
    wau = (
        LLMCallLog.objects.filter(
            created_at__gte=now - timedelta(days=7), user__isnull=False
        )
        .values("user_id")
        .distinct()
        .count()
    )
    mau = (
        LLMCallLog.objects.filter(
            created_at__gte=now - timedelta(days=30), user__isnull=False
        )
        .values("user_id")
        .distinct()
        .count()
    )
    stickiness = round(dau / mau * 100, 1) if mau else None

    since_window = _since(window_days)
    active_in_window_qs = (
        LLMCallLog.objects.filter(created_at__gte=since_window, user__isnull=False)
        .values_list("user_id", flat=True)
        .distinct()
    )
    active_in_window = set(active_in_window_qs)
    proposal_users = set(
        ModificationProposal.objects.filter(created_at__gte=since_window)
        .values_list("created_by_id", flat=True)
        .distinct()
    )
    proposal_share = (
        round(len(proposal_users & active_in_window) / len(active_in_window) * 100, 1)
        if active_in_window
        else None
    )

    return {
        "window_days": window_days,
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "stickiness_pct": stickiness,
        "proposal_generators_pct": proposal_share,
        "active_users_window": len(active_in_window),
    }


def dau_wau_mau(window_days: int = 60) -> dict[str, Any]:
    """Daily DAU / WAU / MAU rollups over the last ``window_days`` days.

    Pulls every ``(day, user_id)`` pair once, then computes rolling distinct
    counts in Python. For a 5–10 user beta and ~20 K row table this is
    cheaper than running 60 SQL queries with COUNT DISTINCT windows.
    """
    today = timezone.now().date()
    earliest = today - timedelta(days=window_days - 1 + 29)  # need 30d lookback for MAU on day 0

    rows = (
        LLMCallLog.objects.filter(
            created_at__date__gte=earliest, user__isnull=False
        )
        .annotate(day=TruncDay("created_at"))
        .values("day", "user_id")
        .distinct()
    )
    by_day: dict[str, set[Any]] = {}
    for r in rows:
        day_label = r["day"].date().isoformat() if r["day"] else None
        if not day_label:
            continue
        by_day.setdefault(day_label, set()).add(r["user_id"])

    labels: list[str] = []
    dau_series: list[int] = []
    wau_series: list[int] = []
    mau_series: list[int] = []
    for i in range(window_days):
        target = today - timedelta(days=window_days - 1 - i)
        labels.append(target.isoformat())

        dau_set = by_day.get(target.isoformat(), set())
        wau_set: set[Any] = set()
        mau_set: set[Any] = set()
        for d_offset in range(7):
            wau_set |= by_day.get((target - timedelta(days=d_offset)).isoformat(), set())
        for d_offset in range(30):
            mau_set |= by_day.get((target - timedelta(days=d_offset)).isoformat(), set())

        dau_series.append(len(dau_set))
        wau_series.append(len(wau_set))
        mau_series.append(len(mau_set))

    return {
        "labels": labels,
        "series": {"dau": dau_series, "wau": wau_series, "mau": mau_series},
    }


def _ttfv_bucket(delta_minutes: float | None) -> str:
    """Bucket a time-to-first-value delta into the canonical histogram label."""
    if delta_minutes is None:
        return "never"
    if delta_minutes < 60:
        return "<1h"
    if delta_minutes < 60 * 24:
        return "1h–1d"
    if delta_minutes < 60 * 24 * 7:
        return "1d–7d"
    if delta_minutes < 60 * 24 * 30:
        return "7d–30d"
    return ">30d"


TTFV_BUCKETS: list[str] = ["<1h", "1h–1d", "1d–7d", "7d–30d", ">30d", "never"]


def time_to_first_value() -> dict[str, Any]:
    """Two histograms: minutes from sign-up to first Ask + to first Proposal.

    "First Ask" = first successful ``LLMCallLog`` row for the user (any
    purpose). "First Proposal" = first ``ModificationProposal`` row.
    Each user lands in exactly one bucket per histogram; users who never
    fired the relevant event count as ``"never"``.
    """
    from writeback.models import ModificationProposal

    users = list(User.objects.values("id", "date_joined"))
    user_join_at = {u["id"]: u["date_joined"] for u in users}

    first_ask: dict[Any, Any] = {}
    for row in (
        LLMCallLog.objects.filter(succeeded=True, user__isnull=False)
        .order_by("user_id", "created_at")
        .values("user_id", "created_at")
    ):
        first_ask.setdefault(row["user_id"], row["created_at"])

    first_proposal: dict[Any, Any] = {}
    for row in (
        ModificationProposal.objects.order_by("created_by_id", "created_at").values(
            "created_by_id", "created_at"
        )
    ):
        first_proposal.setdefault(row["created_by_id"], row["created_at"])

    def _bucketise(first_event_by_user: dict) -> dict[str, int]:
        bucket_counts = {label: 0 for label in TTFV_BUCKETS}
        for uid, joined in user_join_at.items():
            event_at = first_event_by_user.get(uid)
            if event_at is None or joined is None:
                bucket_counts["never"] += 1
                continue
            delta = (event_at - joined).total_seconds() / 60
            bucket_counts[_ttfv_bucket(delta if delta >= 0 else 0)] += 1
        return bucket_counts

    return {
        "buckets": TTFV_BUCKETS,
        "first_ask": _bucketise(first_ask),
        "first_proposal": _bucketise(first_proposal),
        "user_total": len(user_join_at),
    }


def feature_mix(window_days: int = 30) -> dict[str, Any]:
    """Per-user Ask-vs-Modify mix over the window.

    Classifies every active user (≥1 LLMCallLog) into Ask-only, Modify-only,
    or Both. Proposal-generator share lives in :func:`engagement_kpis` —
    this helper returns the full split for the bar chart.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since, user__isnull=False)
        .values("user_id", "purpose")
        .distinct()
    )
    user_purposes: dict[Any, set[str]] = {}
    for r in rows:
        user_purposes.setdefault(r["user_id"], set()).add(r["purpose"])

    ask_only = 0
    modify_only = 0
    both = 0
    for purposes in user_purposes.values():
        has_ask = LLMCallLog.Purpose.ASK in purposes
        has_modify = LLMCallLog.Purpose.MODIFY in purposes
        if has_ask and has_modify:
            both += 1
        elif has_ask:
            ask_only += 1
        elif has_modify:
            modify_only += 1
    total = ask_only + modify_only + both

    return {
        "ask_only": ask_only,
        "modify_only": modify_only,
        "both": both,
        "total": total,
    }


MODIFY_FUNNEL_STAGES: list[str] = ["pending", "approved", "applied", "rejected", "failed"]


def modify_funnel(window_days: int = 30) -> dict[str, Any]:
    """Modify-mode funnel: proposal status counts by tier.

    Returns ``{"tiers": [1, 2, 3], "stages": [...], "grid": [[counts]],
    "totals": {stage: n, ...}, "total": int}``. ``grid[i][j]`` = count for
    tier ``tiers[i]`` at stage ``stages[j]``. Tier-`None` rows (early
    validation failures that never picked a tier) collapse into a
    separate ``"untiered"`` row appended after the numeric tiers when
    non-zero — keeps the chart honest without inflating the legend in the
    common case.
    """
    from writeback.models import ModificationProposal

    since = _since(window_days)
    rows = (
        ModificationProposal.objects.filter(created_at__gte=since)
        .values("tier", "status")
        .annotate(n=Count("id"))
    )

    counts: dict[Any, dict[str, int]] = {1: {}, 2: {}, 3: {}, None: {}}
    for r in rows:
        tier_key = r["tier"]
        if tier_key not in counts:
            counts[None][r["status"]] = counts[None].get(r["status"], 0) + (r["n"] or 0)
            continue
        counts[tier_key][r["status"]] = counts[tier_key].get(r["status"], 0) + (
            r["n"] or 0
        )

    tiers: list[Any] = [1, 2, 3]
    if any(counts[None].values()):
        tiers.append("untiered")

    grid: list[list[int]] = []
    for t in tiers:
        key = None if t == "untiered" else t
        grid.append([counts[key].get(stage, 0) for stage in MODIFY_FUNNEL_STAGES])

    totals = {
        stage: sum(grid[i][j] for i in range(len(tiers)))
        for j, stage in enumerate(MODIFY_FUNNEL_STAGES)
    }
    return {
        "tiers": tiers,
        "stages": MODIFY_FUNNEL_STAGES,
        "grid": grid,
        "totals": totals,
        "total": sum(totals.values()),
    }


def activity_heatmap(window_days: int = 30) -> dict[str, Any]:
    """Hour-of-day × day-of-week activity matrix, last ``window_days``.

    Rows are day-of-week (Mon=0..Sun=6), columns are hour-of-day (0..23).
    Values are LLM call counts. Drives the Plotly heatmap on the
    Engagement tab. ``max_count`` is returned so the colour scale can be
    normalised in the template without re-scanning the matrix in JS.
    """
    since = _since(window_days)
    rows = LLMCallLog.objects.filter(created_at__gte=since).values_list(
        "created_at", flat=True
    )
    matrix = [[0 for _ in range(24)] for _ in range(7)]
    max_count = 0
    for created_at in rows:
        if created_at is None:
            continue
        dow = created_at.weekday()  # Mon=0..Sun=6
        hour = created_at.hour
        matrix[dow][hour] += 1
        if matrix[dow][hour] > max_count:
            max_count = matrix[dow][hour]
    return {
        "matrix": matrix,
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "hours": list(range(24)),
        "max_count": max_count,
    }


def _iso_week_label(d) -> str:
    """ISO week label like ``2026-W19`` — stable cohort key across years."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def cohort_retention_grid(weeks: int = 8) -> dict[str, Any]:
    """Invite-cohort retention grid, ``weeks`` offsets wide.

    For each user, their cohort is the ISO week of ``date_joined``. For
    each cohort × ``W0…W{n-1}`` cell, returns the % of cohort members
    who fired ≥1 LLM call during that calendar week. The grid is the hero
    visual for the VC story — a cohort that holds at 60 %+ through W4 is
    the difference between a tool and a habit.

    Returns ``{"cohorts": [{label, size}], "weeks_offsets": [...],
    "grid": [[pct, ...]]}`` where ``grid[i][j]`` ∈ [0, 100] or ``None``
    for cohort/week pairs that haven't elapsed yet (so the heatmap can
    leave them blank rather than rendering them as 0 %).
    """
    today = timezone.now().date()

    users = list(User.objects.values("id", "date_joined"))
    if not users:
        return {"cohorts": [], "weeks_offsets": list(range(weeks)), "grid": []}

    user_cohort: dict[Any, str] = {}
    cohort_members: dict[str, set[Any]] = {}
    cohort_start_dates: dict[str, Any] = {}

    for u in users:
        joined = u["date_joined"]
        if joined is None:
            continue
        # ISO week start = Monday of that week.
        join_date = joined.date()
        monday = join_date - timedelta(days=join_date.weekday())
        label = _iso_week_label(monday)
        user_cohort[u["id"]] = label
        cohort_members.setdefault(label, set()).add(u["id"])
        cohort_start_dates[label] = monday

    # All weekly activity rows once, then walk them per-user.
    activity_rows = (
        LLMCallLog.objects.filter(user__isnull=False)
        .values_list("user_id", "created_at")
    )
    user_active_weeks: dict[Any, set] = {}
    for uid, created_at in activity_rows:
        if created_at is None:
            continue
        d = created_at.date()
        monday = d - timedelta(days=d.weekday())
        user_active_weeks.setdefault(uid, set()).add(monday)

    cohort_labels_sorted = sorted(
        cohort_members.keys(), key=lambda lbl: cohort_start_dates[lbl]
    )

    cohorts_out: list[dict[str, Any]] = []
    grid: list[list[Any]] = []
    for label in cohort_labels_sorted:
        members = cohort_members[label]
        size = len(members)
        row: list[Any] = []
        for w in range(weeks):
            target_monday = cohort_start_dates[label] + timedelta(weeks=w)
            if target_monday > today:
                row.append(None)
                continue
            active_count = sum(
                1
                for uid in members
                if target_monday in user_active_weeks.get(uid, set())
            )
            row.append(round(active_count / size * 100, 1) if size else 0.0)
        cohorts_out.append({"label": label, "size": size})
        grid.append(row)

    return {
        "cohorts": cohorts_out,
        "weeks_offsets": list(range(weeks)),
        "grid": grid,
    }


# ── Tab 7: Investor View ───────────────────────────────────────────────────


# Hypothesis-test acceptance targets, repeated in the help modal so the page
# can render the bar's status colour without the template carrying numbers.
ACCEPTANCE_TARGETS: dict[int, float] = {1: 90.0, 2: 70.0, 3: 50.0}


def proposal_acceptance_rate_by_tier(window_days: int) -> dict[str, Any]:
    """Per-tier proposal acceptance — % applied vs total proposals.

    Returns ``{"by_tier": [{tier, total, applied, accepted_pct, target_pct,
    meets_target}], "overall_total": int}``. "Accepted" collapses
    ``approved`` and ``applied`` into a single bucket because the
    hypothesis target is "the proposal was good enough to land", not "the
    proposal was clicked twice". Tier-`None` rows are excluded — they
    represent early-validation failures that never picked a tier.
    """
    from writeback.models import ModificationProposal

    since = _since(window_days)
    rows = (
        ModificationProposal.objects.filter(
            created_at__gte=since, tier__isnull=False
        )
        .values("tier", "status")
        .annotate(n=Count("id"))
    )
    per_tier: dict[int, dict[str, int]] = {1: {}, 2: {}, 3: {}}
    for r in rows:
        t = r["tier"]
        if t in per_tier:
            per_tier[t][r["status"]] = per_tier[t].get(r["status"], 0) + (r["n"] or 0)

    by_tier: list[dict[str, Any]] = []
    overall_total = 0
    for t in (1, 2, 3):
        counts = per_tier[t]
        total = sum(counts.values())
        applied = counts.get("applied", 0) + counts.get("approved", 0)
        accepted_pct = round(applied / total * 100, 1) if total else None
        target = ACCEPTANCE_TARGETS[t]
        by_tier.append(
            {
                "tier": t,
                "total": total,
                "applied": applied,
                "accepted_pct": accepted_pct,
                "target_pct": target,
                "meets_target": (
                    accepted_pct is not None and accepted_pct >= target
                ),
            }
        )
        overall_total += total
    return {"by_tier": by_tier, "overall_total": overall_total}


def provider_mix_summary(window_days: int) -> dict[str, Any]:
    """Local-vs-paid token spend summary for the gross-margin frame.

    Returns ``{"local_tokens", "paid_tokens", "total_tokens", "local_pct",
    "paid_pct"}``. Local = Ollama. A flat ≥80 % local share is the
    investor-facing margin story.
    """
    since = _since(window_days)
    rows = (
        LLMCallLog.objects.filter(created_at__gte=since)
        .values("provider")
        .annotate(total=Sum(F("tokens_in") + F("tokens_out")))
    )
    local = 0
    paid = 0
    for r in rows:
        n = int(r["total"] or 0)
        if (r["provider"] or "").lower() == "ollama":
            local += n
        else:
            paid += n
    total = local + paid
    return {
        "local_tokens": local,
        "paid_tokens": paid,
        "total_tokens": total,
        "local_pct": round(local / total * 100, 1) if total else None,
        "paid_pct": round(paid / total * 100, 1) if total else None,
    }


def ifc_ingestion_scatter() -> dict[str, Any]:
    """Latency-vs-entity-count scatter for every successfully parsed IFC file.

    Returns ``{"points": [{entities, latency_seconds, name}], "p95_latency_s",
    "median_entities"}``. Only ``status='completed'`` files with both
    ``created_at`` and ``processed_at`` populated are included — failed or
    in-flight rows would skew the percentile and add visual noise without
    representing a successful run.
    """
    from ifc_processor.models import IFCFile

    qs = (
        IFCFile.objects.filter(
            status=IFCFile.Status.COMPLETED, processed_at__isnull=False
        )
        .values("name", "entity_count", "created_at", "processed_at")
    )
    points: list[dict[str, Any]] = []
    latencies: list[float] = []
    entity_counts: list[int] = []
    for r in qs:
        latency = (r["processed_at"] - r["created_at"]).total_seconds()
        if latency < 0:
            continue
        points.append(
            {
                "entities": int(r["entity_count"] or 0),
                "latency_seconds": round(latency, 2),
                "name": r["name"],
            }
        )
        latencies.append(latency)
        entity_counts.append(int(r["entity_count"] or 0))

    p95 = _percentile([int(x) for x in latencies], 0.95) if latencies else None
    median_entities = (
        sorted(entity_counts)[len(entity_counts) // 2] if entity_counts else None
    )
    return {
        "points": points,
        "p95_latency_s": p95,
        "median_entities": median_entities,
        "count": len(points),
    }


def design_partner_engagement(window_days: int = 30) -> list[dict[str, Any]]:
    """Per-user engagement table for the design-partner section.

    For each active user in the window: total LLM calls, total proposals,
    last seen. Sorted by call count desc. Capped at 20 rows — investor
    view needs depth, not breadth. A "Likert score" column is left for
    the operator to annotate by hand on the screenshot; this helper
    surfaces the engagement signal that *isn't* manual.
    """
    from writeback.models import ModificationProposal

    since = _since(window_days)

    call_rows = (
        LLMCallLog.objects.filter(created_at__gte=since, user__isnull=False)
        .values("user_id", "user__username")
        .annotate(
            calls=Count("id"),
            last_seen=Max("created_at"),
        )
        .order_by("-calls")[:20]
    )
    call_rows_list = list(call_rows)

    proposal_counts = dict(
        ModificationProposal.objects.filter(
            created_at__gte=since,
            created_by_id__in=[r["user_id"] for r in call_rows_list],
        )
        .values_list("created_by_id")
        .annotate(n=Count("id"))
        .values_list("created_by_id", "n")
    )

    return [
        {
            "user_id": r["user_id"],
            "username": r["user__username"],
            "calls": r["calls"] or 0,
            "proposals": proposal_counts.get(r["user_id"], 0),
            "last_seen": r["last_seen"],
        }
        for r in call_rows_list
    ]


def investor_kpis() -> dict[str, Any]:
    """Top-line numbers for the investor header strip.

    Composes from helpers already used elsewhere on the dashboard: cohort
    W4 retention if any cohort is old enough; MAU; total entities
    processed across all successfully ingested IFC files; Ollama-local
    token share over the last 30 days.
    """
    from ifc_processor.models import IFCFile

    cohort = cohort_retention_grid(weeks=8)
    w4_pcts = [
        row[4]
        for row in cohort["grid"]
        if len(row) > 4 and row[4] is not None
    ]
    avg_w4_retention = (
        round(sum(w4_pcts) / len(w4_pcts), 1) if w4_pcts else None
    )

    mau = (
        LLMCallLog.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=30), user__isnull=False
        )
        .values("user_id")
        .distinct()
        .count()
    )

    total_entities = (
        IFCFile.objects.filter(status=IFCFile.Status.COMPLETED).aggregate(
            n=Coalesce(Sum("entity_count"), Value(0))
        )["n"]
        or 0
    )

    mix = provider_mix_summary(window_days=30)

    return {
        "mau_30d": mau,
        "avg_w4_retention_pct": avg_w4_retention,
        "cohorts_with_w4": len(w4_pcts),
        "total_entities_processed": int(total_entities),
        "local_token_share_pct": mix["local_pct"],
        "week_ending": timezone.now().date().isoformat(),
    }


def system_pulse() -> dict[str, Any]:
    """Live system-pulse strip for the Overview header.

    Pulls cheap counts only — no external HTTP. The ``/healthz/`` endpoint
    handles db + ollama liveness; the dashboard links to it rather than
    re-probing here (a TemplateView render shouldn't ping Ollama).
    """
    return {
        "unresolved_errors": ErrorLog.objects.filter(is_resolved=False).count(),
        "hard_blocked_users": UserTokenBudget.objects.filter(hard_blocked=True).count(),
        "calls_last_hour": LLMCallLog.objects.filter(
            created_at__gte=timezone.now() - timedelta(hours=1)
        ).count(),
    }


# ── helpers ─────────────────────────────────────────────────────────────────


def _succeeded():
    """Q-equivalent for ``succeeded=True``; lifted out so KPI's ``~_succeeded()``
    reads as "calls that did not succeed" without re-importing Q at call sites.
    """
    from django.db.models import Q

    return Q(succeeded=True)


def _bucket_by_day_and_category(
    rows,
    window_days: int,
    value_key: str,
    as_int: bool,
    category_key: str = "provider",
) -> dict[str, Any]:
    """Pivot ``[{day, <category>, total}]`` into stacked-chart shape.

    The ``category_key`` defaults to ``"provider"`` so existing callers stay
    compatible. New callers pass ``"purpose"`` (Ask/Modify) or any other
    column they ``.values()`` over. Output ships both ``categories`` (the
    stable name) and ``providers`` (legacy alias) so templates that still
    read ``providers`` keep working.

    Missing days are zero-filled so the chart x-axis is contiguous
    regardless of activity gaps.
    """
    today = timezone.now().date()
    labels = [
        (today - timedelta(days=window_days - 1 - i)).isoformat()
        for i in range(window_days)
    ]
    label_index = {label: i for i, label in enumerate(labels)}

    categories: list[str] = []
    series: dict[str, list[Any]] = {}

    for row in rows:
        cat = row[category_key] or "unknown"
        if cat not in series:
            series[cat] = [0 if as_int else Decimal("0")] * window_days
            categories.append(cat)
        day_label = row["day"].date().isoformat() if row["day"] else None
        idx = label_index.get(day_label)
        if idx is None:
            continue
        value = row[value_key] or (0 if as_int else Decimal("0"))
        # Accumulate, don't overwrite. The original (day, provider) rows
        # appear at most once per (day, category) by construction, but
        # callers that post-collapse providers into a coarser category
        # (e.g. ollama→local, anthropic+groq→paid) feed multiple rows per
        # (day, category) and the pivot must sum them.
        if as_int:
            series[cat][idx] += int(value)
        else:
            series[cat][idx] += Decimal(value)

    sorted_categories = sorted(categories)
    return {
        "labels": labels,
        "categories": sorted_categories,
        "providers": sorted_categories,  # legacy alias
        "series": {c: series[c] for c in sorted_categories},
    }


# Legacy alias kept so internal callers stay readable. Same function.
_bucket_by_day_and_provider = _bucket_by_day_and_category
