# users/management/commands/backfill_sample_projects.py
"""
Backfill auto-provisioned Sample Projects for existing users.

Use cases:
  - Users created in Django admin or via `createsuperuser` before the
    `post_save` signal landed.
  - Re-seeding broken file states for users whose Sample Project rows
    exist but whose underlying files are missing from MEDIA_ROOT
    (pair with `--force-files`).

Idempotent. Safe to re-run.

Usage:
    cd src && uv run python manage.py backfill_sample_projects --dry-run
    cd src && uv run python manage.py backfill_sample_projects
    cd src && uv run python manage.py backfill_sample_projects --skip-pipeline
    cd src && uv run python manage.py backfill_sample_projects --force-files
    cd src && uv run python manage.py backfill_sample_projects --user carloc
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)
User = get_user_model()

SAMPLE_PROJECT_NAME = "Sample Project"


class Command(BaseCommand):
    help = (
        "Run `provision_sample_project` for every active user who does not "
        "already have a Sample Project (or for a single --user)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List users that would be provisioned without acting.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after N successful provisions (safety against runaway).",
        )
        parser.add_argument(
            "--skip-pipeline",
            action="store_true",
            help="Pass --skip-pipeline through to provision_sample_project.",
        )
        parser.add_argument(
            "--force-files",
            action="store_true",
            help=(
                "Pass --force-files through to provision_sample_project — also "
                "re-seeds missing files for users whose Sample Project already "
                "exists but whose underlying files are gone from MEDIA_ROOT."
            ),
        )
        parser.add_argument(
            "--user",
            default=None,
            help="Provision a single user (by PK or username) instead of iterating all.",
        )

    def handle(self, *args, **options) -> None:
        from environments.models import Project

        dry_run: bool = options["dry_run"]
        limit: int | None = options["limit"]
        skip_pipeline: bool = options["skip_pipeline"]
        force_files: bool = options["force_files"]
        user_identifier: str | None = options["user"]

        if user_identifier:
            users = [self._resolve_user(user_identifier)]
        else:
            users = list(User.objects.filter(is_active=True).order_by("pk"))

        provisioned = 0
        skipped = 0
        failed = 0

        for user in users:
            already_has = Project.objects.filter(owner=user, name=SAMPLE_PROJECT_NAME).exists()

            if already_has and not force_files:
                skipped += 1
                self.stdout.write(f"  = {user.username} — already has Sample Project, skipped")
                continue

            if dry_run:
                action = "top up files for" if already_has else "provision"
                self.stdout.write(f"  ? {user.username} — would {action}")
                continue

            try:
                cmd_args: list[str] = [str(user.pk)]
                if skip_pipeline:
                    cmd_args.append("--skip-pipeline")
                if force_files:
                    cmd_args.append("--force-files")
                call_command("provision_sample_project", *cmd_args, verbosity=0)
                provisioned += 1
                self.stdout.write(self.style.SUCCESS(f"  + {user.username} — Sample Project ready"))
            except Exception as exc:
                failed += 1
                logger.exception(
                    "backfill_sample_projects: provisioning failed for %s: %s",
                    user.username,
                    exc,
                )
                self.stdout.write(
                    self.style.WARNING(f"  ! {user.username} — failed: {type(exc).__name__}: {exc}")
                )

            if limit is not None and provisioned >= limit:
                self.stdout.write(self.style.WARNING(f"  Stopped: hit --limit {limit}"))
                break

        verb = "would provision" if dry_run else "provisioned"
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSummary: {verb}={provisioned} skipped={skipped} failed={failed} "
                f"total_considered={len(users)}"
            )
        )

    def _resolve_user(self, identifier: str):
        try:
            if identifier.isdigit():
                return User.objects.get(pk=int(identifier))
        except User.DoesNotExist:
            pass
        try:
            return User.objects.get(username=identifier)
        except User.DoesNotExist as exc:
            raise CommandError(f"User '{identifier}' not found.") from exc
