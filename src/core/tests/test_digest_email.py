# core/tests/test_digest_email.py
"""Tests for ``core.services.digest_email`` — composition and orchestration.

The composition helpers are pure functions (no side effects beyond a /tmp
write in dry-run mode), so they're tested directly. ``send_digest`` is
tested via Django's ``mail.outbox`` (the locmem backend in test settings)
plus assertions on the ``WeeklyDigestConfig`` audit fields.

Brevo daily-cap tests reset the throttle cache between cases — otherwise
state leaks between tests and the cap arithmetic gets wrong.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import gettempdir

import pytest
from django.core import mail
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import WeeklyDigestConfig
from core.services import digest_email

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_throttle_cache():
    """Clear the Brevo daily-counter between tests so cap arithmetic is stable."""
    caches["throttle"].clear()
    yield
    caches["throttle"].clear()


@pytest.fixture
def cfg():
    """A fresh WeeklyDigestConfig with sensible test defaults."""
    obj = WeeklyDigestConfig.load()
    obj.enabled = True
    obj.recipients = ["op@castoriq.io"]
    obj.send_day_of_week = timezone.now().weekday()  # today, so day-skip doesn't fire
    obj.save()
    return obj


# ── WeeklyDigestConfig.clean() ─────────────────────────────────────────────


def test_clean_rejects_non_string_recipient():
    obj = WeeklyDigestConfig.load()
    obj.recipients = ["ok@castoriq.io", 42]
    with pytest.raises(ValidationError):
        obj.full_clean()


def test_clean_rejects_invalid_email_string():
    obj = WeeklyDigestConfig.load()
    obj.recipients = ["ok@castoriq.io", "not-an-email"]
    with pytest.raises(ValidationError):
        obj.full_clean()


def test_clean_accepts_empty_list():
    obj = WeeklyDigestConfig.load()
    obj.recipients = []
    obj.full_clean()  # must not raise


def test_clean_accepts_valid_list():
    obj = WeeklyDigestConfig.load()
    obj.recipients = ["a@castoriq.io", "b@castoriq.io"]
    obj.full_clean()  # must not raise


# ── build_digest_context ───────────────────────────────────────────────────


def test_build_digest_context_includes_all_default_sections(cfg):
    ctx = digest_email.build_digest_context()
    keys = {s["key"] for s in ctx["sections"]}
    # Defaults: 4 summary sections ON; top users OFF.
    assert keys == {"investor", "cost", "reliability", "engagement"}
    assert "top_users" not in ctx


def test_build_digest_context_respects_include_flags(cfg):
    cfg.include_investor_kpis = False
    cfg.include_cost_summary = False
    cfg.include_top_users_table = True
    cfg.save()

    ctx = digest_email.build_digest_context()
    keys = {s["key"] for s in ctx["sections"]}
    assert "investor" not in keys
    assert "cost" not in keys
    assert keys == {"reliability", "engagement"}
    assert "top_users" in ctx  # list, possibly empty


def test_build_digest_context_empty_db_does_not_crash(cfg):
    """Helper must tolerate an entirely fresh DB (no calls, no users, no IFC)."""
    ctx = digest_email.build_digest_context()
    # Every section's lines should be a non-empty list (rendered placeholders).
    for section in ctx["sections"]:
        assert isinstance(section["lines"], list)
        assert section["lines"]
    assert ctx["week_ending"] == timezone.now().date().isoformat()


# ── render_digest_html / _text ─────────────────────────────────────────────


def test_render_html_contains_dateline_and_sections(cfg):
    ctx = digest_email.build_digest_context()
    html = digest_email.render_digest_html(ctx)
    assert "Castor weekly digest" in html
    assert ctx["week_ending"] in html
    # Section headings render.
    assert "Investor KPIs" in html
    assert "Cost (last 7 days)" in html


def test_render_text_is_nonempty_and_includes_links(cfg):
    ctx = digest_email.build_digest_context()
    text = digest_email.render_digest_text(ctx)
    assert text.strip()
    assert ctx["dashboard_url"] in text
    assert ctx["admin_url"] in text


# ── send_digest — gate checks ──────────────────────────────────────────────


def test_send_digest_skipped_disabled_when_off():
    obj = WeeklyDigestConfig.load()
    obj.enabled = False
    obj.recipients = ["op@castoriq.io"]
    obj.save()

    result = digest_email.send_digest()
    assert result.status == WeeklyDigestConfig.Status.SKIPPED_DISABLED
    assert mail.outbox == []
    obj.refresh_from_db()
    assert obj.last_send_status == WeeklyDigestConfig.Status.SKIPPED_DISABLED
    assert obj.last_sent_at is not None


def test_send_digest_skipped_wrong_day_when_today_mismatch(cfg):
    cfg.send_day_of_week = (timezone.now().weekday() + 1) % 7
    cfg.save()

    result = digest_email.send_digest()
    assert result.status == WeeklyDigestConfig.Status.SKIPPED_WRONG_DAY
    assert mail.outbox == []


def test_send_digest_skipped_no_recipients_when_list_empty(cfg):
    cfg.recipients = []
    cfg.save()

    result = digest_email.send_digest()
    assert result.status == WeeklyDigestConfig.Status.SKIPPED_NO_RECIPIENTS
    assert mail.outbox == []


def test_send_digest_skipped_quota_when_would_exceed_cap(cfg, settings):
    settings.BETA_DAILY_TOTAL_CAP = 1
    # Pre-bump the counter so 1 send + 1 already = exceeds 1.
    from beta.throttle import bump_daily_count

    bump_daily_count(1)

    result = digest_email.send_digest()
    assert result.status == WeeklyDigestConfig.Status.SKIPPED_QUOTA
    assert mail.outbox == []


# ── send_digest — force / dry_run ──────────────────────────────────────────


def test_send_digest_force_bypasses_disabled_and_wrong_day():
    obj = WeeklyDigestConfig.load()
    obj.enabled = False  # would normally short-circuit
    obj.recipients = ["op@castoriq.io"]
    obj.send_day_of_week = (timezone.now().weekday() + 3) % 7  # not today
    obj.save()

    result = digest_email.send_digest(force=True)
    assert result.status == WeeklyDigestConfig.Status.SUCCESS
    assert len(mail.outbox) == 1


def test_send_digest_dry_run_writes_file_and_skips_smtp(cfg):
    result = digest_email.send_digest(dry_run=True)
    assert result.status == WeeklyDigestConfig.Status.SUCCESS
    assert mail.outbox == []
    # Stable per-day filename.
    expected_path = (
        Path(gettempdir()) / f"castor-digest-{timezone.now().date().isoformat()}.html"
    )
    assert expected_path.exists()
    body = expected_path.read_text(encoding="utf-8")
    assert "Castor weekly digest" in body


# ── send_digest — happy path ───────────────────────────────────────────────


def test_send_digest_success_sends_email_and_bumps_counter(cfg):
    from beta.throttle import today_send_count

    cfg.recipients = ["op@castoriq.io", "co@castoriq.io"]
    cfg.save()
    before = today_send_count()

    result = digest_email.send_digest()

    assert result.status == WeeklyDigestConfig.Status.SUCCESS
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["op@castoriq.io"]
    assert msg.bcc == ["co@castoriq.io"]
    assert "Castor weekly digest" in msg.subject
    # Both bodies attached.
    assert msg.body  # text part
    [(html_body, mime)] = msg.alternatives
    assert mime == "text/html"
    assert "Castor weekly digest" in html_body
    # Counter bumped by the number of recipients.
    assert today_send_count() == before + 2

    cfg.refresh_from_db()
    assert cfg.last_send_status == WeeklyDigestConfig.Status.SUCCESS
    assert cfg.last_sent_at is not None
    assert "op@castoriq.io" in cfg.last_send_log


def test_send_digest_single_recipient_has_no_bcc(cfg):
    cfg.recipients = ["op@castoriq.io"]
    cfg.save()

    digest_email.send_digest()
    msg = mail.outbox[0]
    assert msg.to == ["op@castoriq.io"]
    # bcc=None gets normalised to [] by EmailMessage.
    assert msg.bcc in ([], None)


# ── send_digest — failure path ─────────────────────────────────────────────


def test_send_digest_captures_smtp_exception(cfg, monkeypatch):
    """SMTP failure must be caught, logged, and recorded as ``FAILED``."""

    class BoomError(Exception):
        pass

    def _explode(self, *a, **kw):
        raise BoomError("relay refused")

    monkeypatch.setattr(
        "django.core.mail.EmailMultiAlternatives.send", _explode, raising=True
    )

    result = digest_email.send_digest()

    assert result.status == WeeklyDigestConfig.Status.FAILED
    assert "BoomError" in result.log
    assert "relay refused" in result.log
    assert mail.outbox == []

    cfg.refresh_from_db()
    assert cfg.last_send_status == WeeklyDigestConfig.Status.FAILED
    assert "BoomError" in cfg.last_send_log
