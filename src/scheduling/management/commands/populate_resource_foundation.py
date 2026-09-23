# scheduling/management/commands/populate_resource_foundation.py
"""Populate canonical Resource / ResourceAssignment from P6 (DF-E2).

Dry-run by default. Pass --apply to write. Does not cut over EVM.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from scheduling.services.resource_population import ResourceFoundationPopulationService


class Command(BaseCommand):
    """Dry-run or apply P6 → canonical resource foundation population."""

    help = (
        "Populate canonical Resource/ResourceAssignment from P6ResourceAssignment. "
        "Dry-run by default; use --apply to write. Does not change EVM reads."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project", required=True, help="Project UUID (required)")
        parser.add_argument(
            "--source-version",
            default="",
            help="Optional ScheduleSourceVersion UUID to attach on assignments",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Analyze only — no writes (default).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist creates/updates (overrides dry-run).",
        )
        parser.add_argument(
            "--include-pending",
            action="store_true",
            help="Include is_pending=True P6ResourceAssignment rows.",
        )
        parser.add_argument(
            "--report-path",
            default="",
            help="Optional JSON report output path",
        )

    def handle(self, *args, **options) -> None:
        project_id = options["project"]
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist as exc:
            raise CommandError(f"Project not found: {project_id}") from exc

        apply = bool(options["apply"])
        dry_run = not apply
        if apply:
            self.stdout.write(self.style.WARNING("APPLY mode — writing canonical resource rows."))
        else:
            self.stdout.write(self.style.NOTICE("Dry-run mode — no database writes."))

        svc = ResourceFoundationPopulationService(project)
        result = svc.run(
            dry_run=dry_run,
            apply=apply,
            source_version_id=options["source_version"] or None,
            include_pending=bool(options["include_pending"]),
        )
        payload = result.to_summary()
        self.stdout.write(json.dumps(payload, indent=2))

        report_path = options["report_path"]
        if report_path:
            path = Path(report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.stdout.write(self.style.NOTICE(f"Report written to {path}"))

        if result.errors:
            raise CommandError("Population reported errors — see JSON output.")
