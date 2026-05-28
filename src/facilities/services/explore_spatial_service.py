# facilities/services/explore_spatial_service.py
"""IFC → Explore floor / room descriptors.

The Explore iframe expects host-supplied floor and room data in Pavla's
postMessage protocol shape (see ``static/facilities/explore/README.md``).
This service walks the project's IFC spatial tree and returns:

* one floor descriptor per ``IfcBuildingStorey`` (with the uploaded plan
  image URL if one has been attached via :class:`ExploreFloorPlan`)
* one room descriptor per ``IfcSpace`` under a given storey, with
  ``globalId``, ``ifcType``, ``name`` and a ``props`` dict suitable for
  use as identification fields and table filter keys.

Read-only — no DB writes here. Persistence of plan images lives in
:mod:`explore_floor_plan_service`.
"""

from __future__ import annotations

import logging
from typing import Any

from environments.models import Project
from ifc_processor.models import IFCSpatialElement

logger = logging.getLogger(__name__)


# IFC properties surfaced to the iframe as room props. Keys mirror what
# Pavla's panel uses for identification + filter keys (see her README
# "Data model" section). Extra keys are passed through verbatim if
# present on the entity's properties payload.
_DEFAULT_PROP_KEYS = (
    "number",
    "department",
    "building",
    "zone",
    "level",
    "area",
    "occupancy",
)


def list_floors_for_project(project: Project) -> list[dict[str, Any]]:
    """Return one descriptor per ``IfcBuildingStorey`` in ``project``.

    Shape matches Pavla's ``SET_FLOORS`` payload: ``{ id, name, label,
    plan, planType, rooms }``. ``plan`` is the URL of the uploaded plan
    image when an :class:`ExploreFloorPlan` exists for the storey;
    ``null`` otherwise (the iframe renders its empty-floor state).
    """
    storeys = (
        IFCSpatialElement.objects.filter(
            ifc_file__project=project,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        )
        # Storeys an editor hid in Explore are omitted from the switcher (the
        # Floor-plans manager still lists them so they can be un-hidden).
        .exclude(explore_settings__hidden=True)
        .select_related("entity", "explore_floor_plan")
        .order_by("elevation", "entity__name")
    )
    floors: list[dict[str, Any]] = []
    for storey in storeys:
        plan_obj = getattr(storey, "explore_floor_plan", None)
        plan_url = _plan_url_for(storey)
        entity = storey.entity
        name = entity.name if entity else str(storey.pk)
        floors.append(
            {
                "id": str(storey.pk),
                "name": name,
                "label": storey.long_name or name,
                "plan": plan_url,
                "planType": "image",
                # White-out state, so the iframe can toggle / revert it even after
                # a reload (planOriginal = the pre-knockout image).
                "knockout": bool(plan_obj.knockout) if plan_obj else False,
                "planOriginal": _original_plan_url_for(storey),
                "rooms": list_rooms_for_floor(storey),
            }
        )
    return floors


def list_rooms_for_floor(storey: IFCSpatialElement) -> list[dict[str, Any]]:
    """Return one descriptor per ``IfcSpace`` child of ``storey``."""
    spaces = (
        IFCSpatialElement.objects.filter(
            parent=storey,
            spatial_type=IFCSpatialElement.SpatialType.SPACE,
        )
        .select_related("entity")
        .order_by("entity__name")
    )
    rooms: list[dict[str, Any]] = []
    for space in spaces:
        entity = space.entity
        if entity is None:
            continue
        rooms.append(
            {
                "globalId": entity.global_id,
                "name": entity.name or "",
                "ifcType": entity.ifc_type or "IfcSpace",
                "props": _extract_props(entity),
            }
        )
    return rooms


def _original_plan_url_for(storey: IFCSpatialElement) -> str | None:
    """Return the pre-knockout (original) plan image URL, if one was kept."""
    plan = getattr(storey, "explore_floor_plan", None)
    if plan is None or not plan.original_image:
        return None
    try:
        return plan.original_image.url
    except (ValueError, AttributeError):
        return None


def _plan_url_for(storey: IFCSpatialElement) -> str | None:
    """Return the uploaded plan image URL for ``storey`` if one exists."""
    plan = getattr(storey, "explore_floor_plan", None)
    if not plan or not plan.image:
        return None
    try:
        return plan.image.url
    except (ValueError, AttributeError):
        return None


def _extract_props(entity) -> dict[str, Any]:
    """Project IFC entity properties down to keys the iframe panel uses.

    The Castor :class:`IFCEntity` carries a ``properties`` JSONField (or
    a related Pset model in some installations). We grab a flat dict of
    well-known keys; unknown keys are skipped to keep the payload tight.
    """
    props = getattr(entity, "properties", None)
    if not isinstance(props, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _DEFAULT_PROP_KEYS:
        value = props.get(key)
        if value not in (None, ""):
            out[key] = value
    return out
