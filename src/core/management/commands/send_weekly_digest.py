# core/management/commands/send_weekly_digest.py
"""CLI entry point for the weekly operator digest.

Invoked from the VPS crontab daily at 08:00 UTC. The command itself checks
``WeeklyDigestConfig.send_day_of_week`` and exits cleanly with a "skipped"
status on non-send days — the schedule lives in the admin, not the cron
entry, so the operator can move the digest from Monday to Tuesday without
SSH-ing into the server.

Exit codes:
- 0 — sent successfully OR skipped on purpose (disabled, wrong day, no
  recipients, quota). Cron treats these as healthy noops.
- 1 — actually failed (template render error, SMTP exception). Cron
  surfaces these via the captured log file.
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from core.models import WeeklyDigestConfig
from core.services import digest_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send the weekly operator digest email. Honours WeeklyDigestConfig."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore `enabled` + `send_day_of_week` checks. Used by the admin action.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Render and write to /tmp; do not send. Use to preview before enabling.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        dry_run = options["dry_run"]

        result = digest_email.send_digest(force=force, dry_run=dry_run)

        if result.status == WeeklyDigestConfig.Status.SUCCESS:
            self.stdout.write(self.style.SUCCESS(f"OK ({result.status}): {result.log}"))
            return

        if result.status == WeeklyDigestConfig.Status.FAILED:
            # Raise so cron logs a non-zero exit and we see it in the captured stderr.
            raise CommandError(f"FAILED: {result.log}")

        # Any of the skipped_* statuses — healthy noops on non-send days.
        self.stdout.write(self.style.WARNING(f"skipped ({result.status}): {result.log}"))
