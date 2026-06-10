# castor/ifc_viewer/services/gap_analysis.py
"""Gap Analysis builder — computes schedule-linkage coverage per entity group."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def build_gap_analysis(
    ifc_file,
    by: str,
    project_id: str | None = None,
) -> list[dict]:
    """Return rows for the Gap Analysis panel table.

    Each row: {group, total, linked, pct, gap, global_ids_json}
    Sorted by gap descending (biggest gaps first).

    project_id: when set, only entities with TaskEntityBinding count as linked.
    """
    from ifc_processor.models import IFCEntity  # local import — avoids circular

    bound_gids: set[str] = set()
    if project_id:
        from scheduling.services.link_resolver import linked_entity_gids_for_project

        bound_gids = linked_entity_gids_for_project(project_id)

    qs = IFCEntity.objects.filter(ifc_file=ifc_file).only(
        "global_id", "ifc_type", "spatial_container", "properties"
    )

    groups: dict[str, dict] = {}
    for entity in qs.iterator(chunk_size=500):
        key = _group_key(entity, by)
        if key not in groups:
            groups[key] = {"total": 0, "linked": 0, "global_ids": []}
        bucket = groups[key]
        bucket["total"] += 1
        bucket["global_ids"].append(entity.global_id)
        if entity.global_id in bound_gids:
            bucket["linked"] += 1

    rows = []
    for group, data in groups.items():
        total = data["total"]
        linked = data["linked"]
        pct = round(linked / total * 100) if total else 0
        rows.append(
            {
                "group": group,
                "total": total,
                "linked": linked,
                "pct": pct,
                "gap": total - linked,
                "global_ids_json": json.dumps(data["global_ids"]),
            }
        )

    return sorted(rows, key=lambda r: -r["gap"])


def _group_key(entity, by: str) -> str:
    if by == "level":
        return entity.spatial_container or "—"
    if by == "element_type":
        return entity.ifc_type or "—"
    if by == "material":
        props = entity.properties or {}
        for k, v in props.items():
            if k.lower().endswith("material") and v:
                return str(v).strip()
        return "—"
    return "—"
