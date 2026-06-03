# facilities/services/explore_floor_plan_service.py
"""Floor-plan image persistence for the Explore module.

Pavla's iframe lets the user import floor-plan images (or PDFs whose
pages become floors) via the toolbar's "Import plans" button. The
image lives in the iframe as a data URL and the floor descriptor
carries it on every ``STATE_CHANGED`` push. This service persists data
URLs into :class:`ExploreFloorPlan` rows keyed to the matching IFC
storey so plans survive across browsers.

PDF-derived "floating" floors that don't map to an IFC storey are
session-scoped in V1 — only floors anchored to an IfcBuildingStorey
get a persistent plan (see plan §Known risks).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from environments.models import Project
from facilities.models.explore import ExploreFloorPlan
from facilities.services.explore_media_service import decode_data_url
from ifc_processor.models import IFCSpatialElement

logger = logging.getLogger(__name__)


def sync_floor_plans(
    project: Project,
    payload_floors: list[dict[str, Any]],
    user,
) -> None:
    """Persist data-URL plan images attached to known IFC storeys.

    Plans that already exist with a non-data-URL ``plan`` field
    (round-tripped from the server) are left alone. Knockout toggles
    persist via the ``knockout`` flag on the floor descriptor.
    """
    for floor in payload_floors or []:
        floor_id = floor.get("id")
        if not floor_id:
            continue
        try:
            pk = UUID(str(floor_id))
        except (ValueError, TypeError):
            continue
        storey = IFCSpatialElement.objects.filter(
            pk=pk,
            ifc_file__project=project,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        ).first()
        if storey is None:
            continue
        _sync_one(storey, floor, user)


def _sync_one(storey: IFCSpatialElement, floor: dict[str, Any], user) -> None:
    plan_src = floor.get("plan") or ""
    original_src = floor.get("planOriginal") or ""
    knockout = bool(floor.get("knockout"))

    plan = ExploreFloorPlan.objects.filter(storey=storey).first()
    content = decode_data_url(plan_src, f"plan-{storey.pk}")
    original_content = decode_data_url(original_src, f"plan-original-{storey.pk}")

    if plan is None and content is None:
        return  # nothing to persist and no row exists

    if plan is None:
        plan = ExploreFloorPlan(
            storey=storey,
            knockout=knockout,
            uploaded_by=user if (user and getattr(user, "is_authenticated", False)) else None,
        )
        plan.image.save(content.name, content, save=False)
        if original_content is not None:
            plan.original_image.save(original_content.name, original_content, save=False)
        plan.save()
        logger.info("Persisted explore floor plan for storey %s", storey.pk)
        return

    dirty = False
    if content is not None:
        plan.image.save(content.name, content, save=False)
        dirty = True
    if original_content is not None:
        plan.original_image.save(original_content.name, original_content, save=False)
        dirty = True
    if plan.knockout != knockout:
        plan.knockout = knockout
        dirty = True
    if dirty:
        plan.save()
