# writeback/management/commands/purge_mistyped_conflicts.py
"""
Delete Conflict rows where the property→IFC-type pairing is clearly wrong.

Before the element-type gate shipped, the scanner could persist findings
like FireRating/EI60 attached to an IfcBeam when the requirement text
actually targeted walls. This command wipes those orphaned rows so a
clean rescan starts from a consistent state.

Usage:
    uv run manage.py purge_mistyped_conflicts              # dry-run, prints what would be deleted
    uv run manage.py purge_mistyped_conflicts --apply      # actually delete
    uv run manage.py purge_mistyped_conflicts --project <uuid>  # scope to one project
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from writeback.models import Conflict

logger = logging.getLogger(__name__)

# Known property → allowed IFC types. Conflicts on rows with a property
# listed here but an ifc_type outside the allowed set are mistyped.
PROPERTY_TYPE_ALLOWLIST: dict[str, set[str]] = {
    "FireRating": {"IfcWall", "IfcWallStandardCase", "IfcCurtainWall", "IfcDoor", "IfcWindow"},
    "LoadBearing": {
        "IfcWall",
        "IfcWallStandardCase",
        "IfcBeam",
        "IfcColumn",
        "IfcSlab",
        "IfcFooting",
    },
    "AcousticRating": {"IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcWindow", "IfcSlab"},
    "ThermalTransmittance": {
        "IfcWall",
        "IfcWallStandardCase",
        "IfcCurtainWall",
        "IfcWindow",
        "IfcDoor",
        "IfcRoof",
        "IfcSlab",
    },
}


class Command(BaseCommand):
    help = "Delete Conflict rows where property_name does not belong on the entity's IFC type."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the deletion. Without this flag, runs as a dry-run.",
        )
        parser.add_argument(
            "--project",
            type=str,
            default=None,
            help="Optional project UUID to scope the purge.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        project_id = options["project"]

        qs = Conflict.objects.select_related("ifc_entity")
        if project_id:
            qs = qs.filter(project_id=project_id)

        mistyped_ids: list = []
        for conflict in qs.iterator():
            allowed = PROPERTY_TYPE_ALLOWLIST.get(conflict.property_name)
            if allowed is None:
                continue
            if conflict.ifc_entity is None:
                continue
            if conflict.ifc_entity.ifc_type in allowed:
                continue

            mistyped_ids.append(conflict.id)
            self.stdout.write(
                f"  {conflict.id} · {conflict.property_name} on "
                f"{conflict.ifc_entity.ifc_type} "
                f"({conflict.ifc_entity.name or conflict.ifc_entity.global_id}) — "
                f"title: {conflict.title!r}"
            )

        if not mistyped_ids:
            self.stdout.write(self.style.SUCCESS("No mistyped conflicts found."))
            return

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDry-run: {len(mistyped_ids)} mistyped conflict(s) would be deleted. "
                    "Re-run with --apply to delete."
                )
            )
            return

        deleted, _ = Conflict.objects.filter(id__in=mistyped_ids).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} mistyped conflict row(s)."))
