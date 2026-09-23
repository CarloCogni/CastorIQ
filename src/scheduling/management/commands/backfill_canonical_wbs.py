# scheduling/management/commands/backfill_canonical_wbs.py
"""Deterministic canonical WBS backfill — dry-run by default (DF-C2)."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from scheduling.services.wbs.population import CanonicalWBSPopulationService


class Command(BaseCommand):
    """Assess or explicitly write canonical WBS from legacy staging evidence."""

    help = "Dry-run or write canonical WBS backfill for a project (default: dry-run)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project", required=True, help="Project UUID")
        parser.add_argument(
            "--source-version", default="", help="Optional ScheduleSourceVersion UUID"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Analyze only — no writes (default).",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            help="Explicit write mode (transactional).",
        )
        parser.add_argument(
            "--source",
            default="p6_legacy",
            help="Backfill adapter source id (default: p6_legacy).",
        )
        parser.add_argument("--report-path", default="", help="Optional JSON report output path")
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Activate/select WBS version after write (requires --write).",
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="Optional node limit for diagnostics"
        )

    def handle(self, *args, **options) -> None:
        project_id = options["project"]
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist as exc:
            raise CommandError(f"Project not found: {project_id}") from exc

        write = bool(options["write"])
        dry_run = not write
        if options["activate"] and not write:
            raise CommandError("--activate requires --write.")

        svc = CanonicalWBSPopulationService(project)
        result = svc.run_backfill(
            source=options["source"],
            source_version_id=options["source_version"] or None,
            dry_run=dry_run,
            write=write,
            activate=bool(options["activate"]),
            limit=options["limit"],
        )

        payload = result.to_summary()
        payload["errors"] = result.errors
        payload["warnings"] = result.warnings
        self.stdout.write(json.dumps(payload, indent=2))

        report_path = options["report_path"]
        if report_path:
            path = Path(report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.stdout.write(self.style.NOTICE(f"Report written to {path}"))

        if result.errors:
            raise CommandError("Backfill blocked — see report for errors.")
