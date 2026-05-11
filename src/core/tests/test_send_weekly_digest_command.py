# core/tests/test_send_weekly_digest_command.py
"""Tests for the ``send_weekly_digest`` management command.

The command is a thin CLI wrapper — these tests assert the
``--force``/``--dry-run`` flags reach ``digest_email.send_digest``
correctly, the right exit codes are produced (``CommandError`` for the
``FAILED`` status, clean exit for everything else), and the stdout
reflects the status returned by the service.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.cache import caches
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import WeeklyDigestConfig
from core.services.digest_email import DigestResult

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_throttle_cache():
    caches["throttle"].clear()
    yield
    caches["throttle"].clear()


def test_command_passes_force_flag(monkeypatch):
    """``--force`` must propagate to the service call as ``force=True``."""
    captured: dict = {}

    def _capture(*, force=False, dry_run=False):
        captured["force"] = force
        captured["dry_run"] = dry_run
        return DigestResult(WeeklyDigestConfig.Status.SUCCESS, "ok")

    monkeypatch.setattr("core.services.digest_email.send_digest", _capture)

    out = StringIO()
    call_command("send_weekly_digest", "--force", stdout=out)

    assert captured == {"force": True, "dry_run": False}
    assert "OK" in out.getvalue()


def test_command_passes_dry_run_flag(monkeypatch):
    captured: dict = {}

    def _capture(*, force=False, dry_run=False):
        captured["force"] = force
        captured["dry_run"] = dry_run
        return DigestResult(WeeklyDigestConfig.Status.SUCCESS, "wrote /tmp/foo.html")

    monkeypatch.setattr("core.services.digest_email.send_digest", _capture)

    out = StringIO()
    call_command("send_weekly_digest", "--dry-run", stdout=out)

    assert captured == {"force": False, "dry_run": True}
    assert "OK" in out.getvalue()
    assert "/tmp/foo.html" in out.getvalue()


def test_command_succeeds_with_default_args(monkeypatch):
    monkeypatch.setattr(
        "core.services.digest_email.send_digest",
        lambda *, force=False, dry_run=False: DigestResult(
            WeeklyDigestConfig.Status.SUCCESS, "sent"
        ),
    )

    out = StringIO()
    call_command("send_weekly_digest", stdout=out)
    assert "OK" in out.getvalue()
    assert "sent" in out.getvalue()


def test_command_skipped_statuses_print_warning_but_exit_zero(monkeypatch):
    """All ``skipped_*`` statuses are healthy noops — stdout warning, no raise."""
    for status in (
        WeeklyDigestConfig.Status.SKIPPED_DISABLED,
        WeeklyDigestConfig.Status.SKIPPED_WRONG_DAY,
        WeeklyDigestConfig.Status.SKIPPED_NO_RECIPIENTS,
        WeeklyDigestConfig.Status.SKIPPED_QUOTA,
    ):
        monkeypatch.setattr(
            "core.services.digest_email.send_digest",
            lambda *, force=False, dry_run=False, _s=status: DigestResult(_s, "reason"),
        )

        out = StringIO()
        call_command("send_weekly_digest", stdout=out)  # must not raise
        assert "skipped" in out.getvalue().lower()
        assert status in out.getvalue()


def test_command_failed_status_raises_command_error(monkeypatch):
    """``FAILED`` is the only non-zero exit — cron flags it via stderr."""
    monkeypatch.setattr(
        "core.services.digest_email.send_digest",
        lambda *, force=False, dry_run=False: DigestResult(
            WeeklyDigestConfig.Status.FAILED, "SMTPException: relay down"
        ),
    )

    with pytest.raises(CommandError) as excinfo:
        call_command("send_weekly_digest")
    assert "FAILED" in str(excinfo.value)
    assert "relay down" in str(excinfo.value)


def test_command_end_to_end_through_real_service(settings):
    """Sanity: with a real configured singleton, the command sends a real email
    via locmem and exits cleanly."""
    settings.BETA_DAILY_TOTAL_CAP = 1000

    cfg = WeeklyDigestConfig.load()
    cfg.enabled = True
    cfg.recipients = ["op@castoriq.io"]
    cfg.send_day_of_week = timezone.now().weekday()
    cfg.save()

    out = StringIO()
    call_command("send_weekly_digest", stdout=out)
    assert "OK" in out.getvalue()

    from django.core import mail

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["op@castoriq.io"]
