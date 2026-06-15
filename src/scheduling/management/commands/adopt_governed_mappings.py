# scheduling/management/commands/adopt_governed_mappings.py
"""Governed mapping adoption — dry-run by default (DF-D2)."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from scheduling.services.governed_mapping.population import GovernedMappingPopulationService


class Command(BaseCommand):
    """Assess or write governed mapping proposals from source evidence."""

    help = "Dry-run or write governed mapping adoption for a project (default: dry-run)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project", required=True, help="Project UUID")
        parser.add_argument("--dimension", required=True, help="Dimension key (e.g. trade)")
        parser.add_argument("--source", required=True, help="Source adapter id")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Analyze only — no writes (default).",
        )
        parser.add_argument(
            "--write-proposals",
            action="store_true",
            help="Create proposed assignments only (transactional).",
        )
        parser.add_argument(
            "--write-authoritative",
            action="store_true",
            help="Import explicit authoritative evidence only.",
        )
        parser.add_argument("--mapping-set", default="", help="Optional mapping set UUID")
        parser.add_argument("--report-path", default="", help="Optional JSON report path")
        parser.add_argument("--limit", type=int, default=None, help="Optional row limit")
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Activate mapping set after write (requires write mode).",
        )

    def handle(self, *args, **options) -> None:
        project_id = options["project"]
        if project_id == "eb3b0c76-4812-4ce0-8927-ad85a111763a" and (
            options["write_proposals"] or options["write_authoritative"] or options["activate"]
        ):
            raise CommandError("IBS project is read-only — dry-run only.")

        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist as exc:
            raise CommandError(f"Project not found: {project_id}") from exc

        write = bool(options["write_proposals"] or options["write_authoritative"])
        dry_run = not write
        if options["activate"] and not write:
            raise CommandError("--activate requires --write-proposals or --write-authoritative.")

        svc = GovernedMappingPopulationService(project)
        result = svc.run_adoption(
            source=options["source"],
            dimension_key=options["dimension"],
            dry_run=dry_run,
            write_proposals=bool(options["write_proposals"]),
            write_authoritative=bool(options["write_authoritative"]),
            mapping_set_id=options["mapping_set"] or None,
            limit=options["limit"],
            activate=bool(options["activate"]),
        )

        payload = result.to_summary()
        self.stdout.write(json.dumps(payload, indent=2))

        report_path = options["report_path"]
        if report_path:
            path = Path(report_path)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Report written: {path}"))

        if result.errors and write:
            raise CommandError("; ".join(result.errors))
        if result.errors and not write:
            self.stderr.write(self.style.WARNING("; ".join(result.errors)))
