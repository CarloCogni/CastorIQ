# Backfill: refine IFCEntity.spatial_container from storey to room (IfcSpace)
# using IfcRelSpaceBoundary — for IFC files parsed before the parser learned
# to do this itself (Phase B.5). Safe to re-run; only touches entities whose
# current container is not already a space.

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Refine element containers (doors, windows, walls…) from storey to "
        "room using IfcRelSpaceBoundary. Re-runnable; processes COMPLETED "
        "IFC files. Optionally limit with --ifc-file <uuid>."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ifc-file",
            help="Limit to one IFCFile pk (default: all COMPLETED files).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        import ifcopenshell  # local import — heavy

        files = IFCFile.objects.filter(status=IFCFile.Status.COMPLETED)
        if options["ifc_file"]:
            files = files.filter(pk=options["ifc_file"])

        total_updated = 0
        for ifc_file in files:
            try:
                path = ifc_file.file.path
            except (ValueError, NotImplementedError):
                self.stderr.write(f"skip {ifc_file.pk}: file not on local disk")
                continue
            try:
                model = ifcopenshell.open(path)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"skip {ifc_file.pk}: cannot open ({exc})")
                continue

            # element GID -> space GID (first boundary seen wins)
            mapping: dict[str, str] = {}
            try:
                boundaries = model.by_type("IfcRelSpaceBoundary")
            except Exception:  # noqa: BLE001
                boundaries = []
            for rel in boundaries:
                space = getattr(rel, "RelatingSpace", None)
                element = getattr(rel, "RelatedBuildingElement", None)
                if space is None or element is None:
                    continue
                space_gid = getattr(space, "GlobalId", None)
                element_gid = getattr(element, "GlobalId", None)
                if space_gid and element_gid and element_gid not in mapping:
                    mapping[element_gid] = space_gid

            if not mapping:
                self.stdout.write(f"{ifc_file.name}: no space boundaries found")
                continue

            spaces = {
                s.entity.global_id: s
                for s in IFCSpatialElement.objects.filter(
                    ifc_file=ifc_file,
                    spatial_type=IFCSpatialElement.SpatialType.SPACE,
                ).select_related("entity")
                if s.entity_id
            }

            updated = 0
            entities = IFCEntity.objects.filter(
                ifc_file=ifc_file, global_id__in=list(mapping.keys())
            ).select_related("spatial_container")
            to_save = []
            for entity in entities:
                space_node = spaces.get(mapping[entity.global_id])
                if space_node is None:
                    continue
                current = entity.spatial_container
                if current and current.spatial_type == IFCSpatialElement.SpatialType.SPACE:
                    continue  # already room-anchored — leave it alone
                entity.spatial_container = space_node
                to_save.append(entity)
                updated += 1
            if to_save and not options["dry_run"]:
                IFCEntity.objects.bulk_update(to_save, ["spatial_container"], batch_size=500)

            verb = "would update" if options["dry_run"] else "updated"
            self.stdout.write(
                f"{ifc_file.name}: {verb} {updated} of {len(mapping)} boundary elements"
            )
            total_updated += updated

        self.stdout.write(self.style.SUCCESS(f"Done — {total_updated} entities refined."))
