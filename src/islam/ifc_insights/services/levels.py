# islam/ifc_insights/services/levels.py
"""Level Panel services — extract storeys from IFC, cluster Z-values, apply back to IFC."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_Z_TOLERANCE_MM = 500  # storeys within 500 mm collapse into one cluster


# ---------------------------------------------------------------------------
# Section 1 helpers — reference points
# ---------------------------------------------------------------------------


def get_reference_points(ifc_file) -> dict:
    """Read IfcSite elevation and named reference points from IFCEntity.properties.

    Returns dict with keys:
        project_base_point: {x, y, z} or None
        survey_point:       {x, y, z} or None
        site_elevation:     float or None
    """
    from ifc_processor.models import IFCEntity  # local — avoids circular

    result: dict = {
        "project_base_point": None,
        "survey_point": None,
        "site_elevation": None,
    }

    for entity in IFCEntity.objects.filter(ifc_file=ifc_file, ifc_type="IfcSite").only("properties"):
        props = entity.properties or {}
        for k, v in props.items():
            k_lower = k.lower()
            if "refelevation" in k_lower or "ref_elevation" in k_lower:
                result["site_elevation"] = v
                break

    _name_map = {
        "project base point": "project_base_point",
        "survey point": "survey_point",
    }
    for entity in IFCEntity.objects.filter(ifc_file=ifc_file).only("name", "properties"):
        key = _name_map.get((entity.name or "").lower())
        if not key:
            continue
        props = entity.properties or {}
        coords: dict = {}
        for pk, pv in props.items():
            pk_lower = pk.lower()
            if pk_lower in ("x", "easting", "east/west"):
                coords["x"] = pv
            elif pk_lower in ("y", "northing", "north/south"):
                coords["y"] = pv
            elif pk_lower in ("z", "elevation", "altitude", "angle"):
                coords["z"] = pv
        result[key] = coords or None

    return result


# ---------------------------------------------------------------------------
# Section 2 helpers — storey registry
# ---------------------------------------------------------------------------


def get_storeys_from_db(ifc_file) -> list[dict]:
    """Return all IfcBuildingStorey records from IFCSpatialElement with element counts.

    Faster and more complete than parsing the raw IFC file when the DB is populated.
    """
    from django.db.models import Count
    from ifc_processor.models import IFCSpatialElement  # local — avoids circular

    rows = (
        IFCSpatialElement.objects
        .filter(ifc_file=ifc_file, spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY)
        .select_related("entity")
        .annotate(element_count=Count("contained_entities"))
        .order_by("elevation", "entity__name")
    )

    results: list[dict] = []
    for s in rows:
        entity = s.entity
        results.append({
            "global_id": entity.global_id,
            "name": entity.name or entity.global_id,
            "z_elevation": float(s.elevation) if s.elevation is not None else 0.0,
            "element_count": s.element_count,
            "long_name": s.long_name or "",
        })
    return results


def match_storeys_to_tasks(storey_names: list[str], project) -> dict[str, bool]:
    """Return {storey_name_lower: True} for names found in any task name or activity_code."""
    try:
        from islam.scheduling.models import Task  # local — avoids circular
    except ImportError:
        return {}

    all_tasks = list(
        Task.objects.filter(project=project).values_list("name", "activity_code")
    )
    corpus = " ".join(f"{n or ''} {ac or ''}" for n, ac in all_tasks).lower()
    return {name.lower(): name.lower() in corpus for name in storey_names}


# ---------------------------------------------------------------------------
# Section 3 helpers — missing level hints
# ---------------------------------------------------------------------------

_LEVEL_RE = re.compile(
    r"\b("
    r"(?:level|floor|storey|story|lvl)\s*\d*\w*"
    r"|l\d+"
    r"|gf|rf|b\d+"
    r"|ground\s*floor|ground\s*level|basement|roof\s*level|podium|mezzanine"
    r")\b",
    re.IGNORECASE,
)


def get_missing_level_hints(storey_names_lower: set[str], project) -> list[dict]:
    """Scan task names/codes for level keywords not matched by any known IFC storey name.

    Returns list of {"hint": str, "task_names": list[str]} capped at 5 tasks per hint.
    """
    try:
        from islam.scheduling.models import Task  # local — avoids circular
    except ImportError:
        return []

    hits: dict[str, set] = {}
    for task in Task.objects.filter(project=project).only("name", "activity_code"):
        for field in (task.name, task.activity_code or ""):
            for m in _LEVEL_RE.finditer(field):
                hint_lower = m.group(0).strip().lower()
                # Skip if any storey name contains or is contained by the hint
                if any(hint_lower in sn or sn in hint_lower for sn in storey_names_lower):
                    continue
                hits.setdefault(hint_lower, set()).add(task.name)

    return [
        {"hint": k, "task_names": sorted(v)[:5]}
        for k, v in sorted(hits.items())
    ]


# ---------------------------------------------------------------------------
# Existing helpers — kept unchanged
# ---------------------------------------------------------------------------


def extract_levels_from_ifc(ifc_path: str) -> list[dict]:
    """Parse IfcBuildingStorey objects from file and return dicts ready to display.

    Returns list of {"global_id", "name", "z_elevation"} sorted by z_elevation.
    Prefer get_storeys_from_db() when the DB is populated — this is a fallback.
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
    """Cluster entity spatial containers to propose floor levels.

    Fallback when both IFCSpatialElement and IfcBuildingStorey parsing are empty.
    Returns list of {"name", "z_elevation"} sorted by z_elevation.
    """
    from ifc_processor.models import IFCEntity  # local import to avoid circular

    storey_names: list[str] = (
        IFCEntity.objects
        .filter(ifc_file=ifc_file, spatial_container__isnull=False)
        .values_list("spatial_container__entity__name", flat=True)
        .distinct()
    )

    names = sorted(n for n in storey_names if n)
    if not names:
        return []

    return [
        {"name": name, "z_elevation": float(idx * 3000)}
        for idx, name in enumerate(names)
    ]


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

    backup_path = _backup_ifc(ifc_path)

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
