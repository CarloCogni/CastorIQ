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

_LINKED = "#22c55e"
_NOT_LINKED = "#94a3b8"
_NO_SCHEDULE = "#64748b"


def build_colormap(ifc_file, by: str, project_id: str | None = None) -> dict:
    """Return colormap payload for the Color By toolbar.

    Response shape::

        {
          "by": <scheme>,
          "colormap": {global_id: color_hex},
          "legend": [{label, color}],
        }

    ``by``: ``material`` | ``level`` | ``element_type`` | ``schedule_status``

    Schedule Status uses trusted active ``TaskEntityBinding`` rows only — never
    IFC property strings such as Activity Id. Linked GIDs come from
    ``linked_entity_gids_for_project`` (packaging binding reader).
    """
    if by not in _VALID:
        return {"by": by, "colormap": {}, "legend": [], "error": "invalid_scheme"}

    from ifc_processor.models import IFCEntity  # local import — avoids circular

    qs = IFCEntity.objects.filter(ifc_file=ifc_file).only(
        "global_id", "ifc_type", "spatial_container", "properties"
    )
    if by == "level":
        qs = qs.select_related("spatial_container__entity")

    if by == "schedule_status":
        pid = project_id or getattr(ifc_file, "project_id", None)
        payload = _schedule_status(qs, str(pid) if pid else None)
    else:
        payload = _by_field(qs, by)
    payload["by"] = by
    return payload


def _schedule_status(qs, project_id: str | None) -> dict:
    """Colour by trusted schedule bindings for this project.

    Linked = GlobalId in ``linked_entity_gids_for_project`` (trusted + active).
    When the project has no persisted ScheduleSource, return the honest
    ``No schedule imported`` legend — never infer Linked from IFC Activity Id.
    """
    from scheduling.models import ScheduleSource

    entity_gids = list(qs.values_list("global_id", flat=True))

    if not project_id:
        colormap = {gid: _NO_SCHEDULE for gid in entity_gids}
        return {
            "colormap": colormap,
            "legend": [{"label": "No schedule imported", "color": _NO_SCHEDULE}],
            "counts": {"linked": 0, "not_linked": 0, "no_schedule": len(entity_gids)},
        }

    has_schedule = ScheduleSource.objects.filter(project_id=project_id).exists()
    if not has_schedule:
        colormap = {gid: _NO_SCHEDULE for gid in entity_gids}
        return {
            "colormap": colormap,
            "legend": [{"label": "No schedule imported", "color": _NO_SCHEDULE}],
            "counts": {"linked": 0, "not_linked": 0, "no_schedule": len(entity_gids)},
        }

    from scheduling.services.link_resolver import linked_entity_gids_for_project

    bound_gids = linked_entity_gids_for_project(project_id)

    colormap: dict[str, str] = {}
    linked_n = 0
    not_linked_n = 0
    for gid in entity_gids:
        if gid in bound_gids:
            colormap[gid] = _LINKED
            linked_n += 1
        else:
            colormap[gid] = _NOT_LINKED
            not_linked_n += 1

    legend = [
        {"label": "Linked to schedule", "color": _LINKED},
        {"label": "Not linked", "color": _NOT_LINKED},
    ]
    if linked_n == 0:
        legend = [{"label": "Not linked", "color": _NOT_LINKED}]

    return {
        "colormap": colormap,
        "legend": legend,
        "counts": {"linked": linked_n, "not_linked": not_linked_n, "no_schedule": 0},
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
    legend.sort(key=lambda row: row["label"])
    return {"colormap": colormap, "legend": legend}


def _group_key(entity, by: str) -> str:
    if by == "level":
        # Authoritative storey name — never the IFCSpatialElement FK object.
        sc = entity.spatial_container
        if sc is not None and sc.entity_id:
            name = (sc.entity.name or "").strip()
            if name:
                return name
        return "Unassigned"
    if by == "element_type":
        # IFC entity class (IfcBeam, IfcWall, …) — not Revit/type-name strings.
        return entity.ifc_type or "Unassigned"
    if by == "material":
        props = entity.properties or {}
        for k, v in props.items():
            if k.lower().endswith("material") and v:
                return str(v).strip()
        return "Unassigned"
    return "Unassigned"
