# castor/ifc_viewer/services/colormap.py
"""Colormap builder — assigns a color hex to every IFCEntity for the Color By toolbar."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PALETTE = [
    "#3b82f6",
    "#22c55e",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#6366f1",
    "#14b8a6",
    "#e879f9",
]

_VALID = {"material", "level", "element_type", "schedule_status"}


def build_colormap(ifc_file, by: str, project_id: str | None = None) -> dict:
    """Return {"colormap": {global_id: color_hex}, "legend": [{label, color}]}.

    by: "material" | "level" | "element_type" | "schedule_status"
    project_id: required for schedule_status (TaskEntityBinding lookup).
    """
    if by not in _VALID:
        return {"colormap": {}, "legend": []}

    from ifc_processor.models import IFCEntity  # local import — avoids circular

    qs = IFCEntity.objects.filter(ifc_file=ifc_file).only(
        "global_id", "ifc_type", "spatial_container", "properties"
    )

    if by == "schedule_status":
        pid = project_id or getattr(ifc_file, "project_id", None)
        return _schedule_status(qs, str(pid) if pid else None)
    return _by_field(qs, by)


def _schedule_status(qs, project_id: str | None) -> dict:
    GREEN = "#22c55e"
    GRAY = "#94a3b8"
    bound_gids: set[str] = set()
    if project_id:
        from castor.scheduling.services.link_resolver import linked_entity_gids_for_project

        bound_gids = linked_entity_gids_for_project(project_id)
    colormap: dict[str, str] = {}
    for entity in qs.iterator(chunk_size=500):
        props = entity.properties or {}
        prop_linked = any(k.lower().endswith("activity id") for k, v in props.items() if v)
        linked = entity.global_id in bound_gids or prop_linked
        colormap[entity.global_id] = GREEN if linked else GRAY
    return {
        "colormap": colormap,
        "legend": [
            {"label": "Linked to schedule", "color": GREEN},
            {"label": "Not linked", "color": GRAY},
        ],
    }


def _by_field(qs, by: str) -> dict:
    group_colors: dict[str, str] = {}
    colormap: dict[str, str] = {}
    for entity in qs.iterator(chunk_size=500):
        group = _group_key(entity, by)
        if group not in group_colors:
            group_colors[group] = _PALETTE[len(group_colors) % len(_PALETTE)]
        colormap[entity.global_id] = group_colors[group]
    legend = [{"label": label, "color": color} for label, color in group_colors.items()]
    return {"colormap": colormap, "legend": legend}


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
