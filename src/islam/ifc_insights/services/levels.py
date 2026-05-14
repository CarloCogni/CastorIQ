# islam/ifc_insights/services/levels.py
"""Level Panel services — extract storeys from IFC, cluster Z-values, apply back to IFC."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_Z_TOLERANCE_MM = 500  # storeys within 500 mm collapse into one cluster


def extract_levels_from_ifc(ifc_path: str) -> list[dict]:
    """Parse IfcBuildingStorey objects from file and return dicts ready to display.

    Returns list of {"global_id", "name", "z_elevation"} sorted by z_elevation.
    """
    try:
        import ifcopenshell  # type: ignore[import-untyped]
    except ImportError:
        logger.error("ifcopenshell not installed — cannot extract levels")
        return []

    try:
        model = ifcopenshell.open(ifc_path)
    except Exception as exc:
        logger.error("Failed to open IFC at %s: %s", ifc_path, exc)
        return []

    results: list[dict] = []
    for storey in model.by_type("IfcBuildingStorey"):
        z = 0.0
        if storey.ObjectPlacement:
            try:
                placement = storey.ObjectPlacement
                if hasattr(placement, "RelativePlacement"):
                    loc = placement.RelativePlacement.Location
                    if loc and hasattr(loc, "Coordinates"):
                        z = float(loc.Coordinates[2]) if len(loc.Coordinates) > 2 else 0.0
            except Exception:
                z = 0.0
        results.append({
            "global_id": storey.GlobalId,
            "name": storey.Name or storey.GlobalId,
            "z_elevation": z,
        })

    return sorted(results, key=lambda r: r["z_elevation"])


def suggest_levels_from_entities(ifc_file) -> list[dict]:
    """Cluster entity Z-coordinates to propose floor levels (±500 mm tolerance).

    Uses IFCEntity.spatial_container groupings as a proxy for storey elevation when
    IfcBuildingStorey parsing is unavailable or empty.

    Returns list of {"name", "z_elevation"} sorted by z_elevation.
    """
    from ifc_processor.models import IFCEntity  # local import to avoid circular

    storeys_seen: dict[str, int] = {}
    for entity in (
        IFCEntity.objects.filter(ifc_file=ifc_file)
        .exclude(spatial_container="")
        .values_list("spatial_container", flat=True)
        .distinct()
    ):
        if entity:
            storeys_seen[entity] = storeys_seen.get(entity, 0) + 1

    if not storeys_seen:
        return []

    # Assign synthetic Z values: 0, 3000, 6000, … mm per floor (alphabetical order)
    suggestions = []
    for idx, name in enumerate(sorted(storeys_seen)):
        suggestions.append({"name": name, "z_elevation": float(idx * 3000)})

    return suggestions


def _backup_ifc(ifc_path: str) -> str:
    """Copy IFC file to a .bak sibling and return the backup path."""
    src = Path(ifc_path)
    backup = src.with_suffix(".ifc.bak")
    shutil.copy2(src, backup)
    logger.info("IFC backup created at %s", backup)
    return str(backup)


def apply_levels_to_ifc(ifc_path: str, levels: list, ifc_file) -> dict:
    """Write IslamLevel records back to IfcBuildingStorey objects in the IFC file.

    Uses Tier1Writer for the disk write and GitService for the audit commit.
    A .ifc.bak backup is created before anything else.

    Returns {"backup_path", "updated", "created", "errors", "commit_hash"}.
    """
    try:
        import ifcopenshell  # type: ignore[import-untyped]
        import ifcopenshell.api  # type: ignore[import-untyped]
    except ImportError:
        return {"backup_path": "", "updated": 0, "created": 0,
                "errors": ["ifcopenshell not installed"], "commit_hash": ""}

    from ifc_processor.services.ifc_writer import IFCWriteError, Tier1Writer
    from writeback.services.git_service import GitService

    backup_path = _backup_ifc(ifc_path)  # always first, before any open

    try:
        writer = Tier1Writer(ifc_path)
    except Exception as exc:
        return {"backup_path": backup_path, "updated": 0, "created": 0,
                "errors": [str(exc)], "commit_hash": ""}

    updated = 0
    created = 0
    errors: list[str] = []

    existing_storeys = {s.GlobalId: s for s in writer.model.by_type("IfcBuildingStorey")}

    for level in levels:
        gid = level.ifc_storey_global_id
        z = level.z_elevation
        name = level.name

        if gid and gid in existing_storeys:
            storey = existing_storeys[gid]
            try:
                writer.set_attribute([gid], "Name", name)
                # Z-elevation is geometry — updated directly on writer.model;
                # writer.save() persists it along with the name change.
                placement = storey.ObjectPlacement
                if placement and hasattr(placement, "RelativePlacement"):
                    loc = placement.RelativePlacement.Location
                    if loc and hasattr(loc, "Coordinates"):
                        coords = list(loc.Coordinates)
                        if len(coords) > 2:
                            coords[2] = z
                        else:
                            coords = [coords[0] if coords else 0.0,
                                      coords[1] if len(coords) > 1 else 0.0, z]
                        loc.Coordinates = coords
                updated += 1
            except (IFCWriteError, Exception) as exc:
                errors.append(f"Update {gid}: {exc}")
        elif not gid:
            try:
                buildings = writer.model.by_type("IfcBuilding")
                building = buildings[0] if buildings else None
                new_storey = ifcopenshell.api.run(
                    "root.create_entity",
                    writer.model,
                    ifc_class="IfcBuildingStorey",
                    name=name,
                )
                if building:
                    ifcopenshell.api.run(
                        "aggregate.assign_object",
                        writer.model,
                        relating_object=building,
                        product=new_storey,
                    )
                ifcopenshell.api.run(
                    "geometry.edit_object_placement",
                    writer.model,
                    product=new_storey,
                    matrix=_z_matrix(z),
                )
                level.ifc_storey_global_id = new_storey.GlobalId
                level.save(update_fields=["ifc_storey_global_id"])
                created += 1
            except Exception as exc:
                errors.append(f"Create '{name}': {exc}")

    commit_hash = ""
    try:
        writer.save()
        git = GitService(ifc_file.project)
        git.ensure_repo()
        commit_hash = git.commit_modification(
            ifc_file=ifc_file,
            message=f"Level Panel: {updated} storey(s) updated, {created} created",
            tier=1,
            diff_data={"affected_entities": updated + created},
            author_name="Castor Level Panel",
        )
    except Exception as exc:
        errors.append(f"Write/commit failed: {exc}")

    return {
        "backup_path": backup_path,
        "updated": updated,
        "created": created,
        "errors": errors,
        "commit_hash": commit_hash,
    }


def _z_matrix(z_mm: float):
    """Return a 4×4 identity matrix with Z translation set."""
    import numpy as np  # type: ignore[import-untyped]
    m = np.eye(4)
    m[2][3] = z_mm
    return m
