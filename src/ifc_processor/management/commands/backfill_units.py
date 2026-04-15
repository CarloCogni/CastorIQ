# ifc_processor/management/commands/backfill_units.py
"""Backfill project_units for already-parsed IFC files."""

import ifcopenshell
from django.core.management.base import BaseCommand

from ifc_processor.models import IFCFile
from ifc_processor.services.parser import _UNIT_TYPES, _format_unit_label


class Command(BaseCommand):
    help = "Extract IfcUnitAssignment into project_units for completed IFC files"

    def handle(self, *args, **options) -> None:
        qs = IFCFile.objects.filter(
            status=IFCFile.Status.COMPLETED,
            project_units__isnull=True,
        )
        total = qs.count()
        if not total:
            self.stdout.write("No files need backfilling.")
            return

        updated = 0
        for ifc_file in qs.iterator():
            try:
                model = ifcopenshell.open(ifc_file.file.path)
                assignments = model.by_type("IfcUnitAssignment")
                if not assignments:
                    continue
                units: dict[str, str] = {}
                for unit in assignments[0].Units:
                    unit_type = getattr(unit, "UnitType", None)
                    if unit_type and unit_type in _UNIT_TYPES:
                        units[unit_type] = _format_unit_label(unit)
                if units:
                    ifc_file.project_units = units
                    ifc_file.save(update_fields=["project_units"])
                    updated += 1
            except Exception as exc:
                self.stderr.write(f"  Skipped {ifc_file.pk}: {exc}")

        self.stdout.write(f"Backfilled {updated}/{total} files.")
