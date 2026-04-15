# ifc_processor/management/commands/backfill_element_types.py
"""Backfill IFCElementType records and link existing IFCEntity instances."""

import logging

import ifcopenshell
import ifcopenshell.util.element as element_util
from django.core.management.base import BaseCommand

from ifc_processor.models import IFCElementType, IFCEntity, IFCFile
from ifc_processor.services.parser import IFCParserService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Create IFCElementType records from IFC files and link existing "
        "IFCEntity instances that have no element_type set."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of entities to bulk_update per batch (default: 500).",
        )

    def handle(self, *args, **options) -> None:
        batch_size = options["batch_size"]

        # Find completed files that have unlinked entities
        file_ids = (
            IFCEntity.objects.filter(element_type__isnull=True)
            .values_list("ifc_file_id", flat=True)
            .distinct()
        )
        ifc_files = IFCFile.objects.filter(
            pk__in=file_ids,
            status=IFCFile.Status.COMPLETED,
        )
        total_files = ifc_files.count()
        if not total_files:
            self.stdout.write("No files need backfilling.")
            return

        self.stdout.write(f"Backfilling element types for {total_files} file(s)...")

        files_done = 0
        entities_linked = 0

        for ifc_file in ifc_files.iterator():
            try:
                linked = self._backfill_file(ifc_file, batch_size)
                entities_linked += linked
                files_done += 1
                self.stdout.write(
                    f"  [{files_done}/{total_files}] {ifc_file.name}: linked {linked} entities"
                )
            except Exception as exc:
                self.stderr.write(f"  Skipped {ifc_file.name} ({ifc_file.pk}): {exc}")

        self.stdout.write(
            f"Done. Processed {files_done}/{total_files} files, "
            f"linked {entities_linked} entities to element types."
        )

    def _backfill_file(self, ifc_file: IFCFile, batch_size: int) -> int:
        """Open an IFC file and link its entities to IFCElementType records."""
        model = ifcopenshell.open(ifc_file.file.path)

        # Build a parser instance just for _serialize_value
        parser = IFCParserService.__new__(IFCParserService)

        # Cache type GUIDs → IFCElementType records for this file
        type_cache: dict[str, IFCElementType] = {}

        # Pre-load any existing IFCElementType records for this file
        for et in IFCElementType.objects.filter(ifc_file=ifc_file):
            type_cache[et.global_id] = et

        unlinked = IFCEntity.objects.filter(
            ifc_file=ifc_file,
            element_type__isnull=True,
        ).only("id", "global_id")

        batch: list[IFCEntity] = []
        linked = 0

        for entity in unlinked.iterator():
            try:
                ifc_element = model.by_guid(entity.global_id)
            except Exception:
                continue

            try:
                type_obj = element_util.get_type(ifc_element)
            except Exception:
                continue

            if type_obj is None:
                continue

            type_gid = type_obj.GlobalId
            if type_gid not in type_cache:
                # Extract type properties
                type_properties: dict = {}
                try:
                    type_psets = element_util.get_psets(type_obj)
                    for pset_name, pset_props in type_psets.items():
                        for prop_name, prop_value in pset_props.items():
                            if prop_value is not None:
                                key = f"{pset_name}.{prop_name}"
                                type_properties[key] = parser._serialize_value(prop_value)
                except Exception:
                    pass

                type_cache[type_gid] = IFCElementType.objects.create(
                    ifc_file=ifc_file,
                    global_id=type_gid,
                    ifc_type=type_obj.is_a(),
                    name=type_obj.Name or "",
                    description=getattr(type_obj, "Description", None) or "",
                    applicable_occurrence=(getattr(type_obj, "ApplicableOccurrence", None) or ""),
                    tag=getattr(type_obj, "Tag", None) or "",
                    properties=type_properties,
                )

            entity.element_type = type_cache[type_gid]
            batch.append(entity)
            linked += 1

            if len(batch) >= batch_size:
                IFCEntity.objects.bulk_update(batch, ["element_type"])
                batch = []

        if batch:
            IFCEntity.objects.bulk_update(batch, ["element_type"])

        return linked
