# facilities/services/explore_point_service.py
"""Bulk sync of placed points (and their media) for the Explore module.

The iframe owns the in-session working set; on every change it emits
``STATE_CHANGED`` via postMessage with the full snapshot (debounced by
``state.js``). The host page POSTs that snapshot to ``ExploreUserStateApiView``,
which delegates to :func:`bulk_sync_points` to reconcile against the DB.

Reconciliation is keyed by ``client_id`` (Pavla's in-iframe ``pt-xxxxxxxx``
ids) so points round-trip cleanly: new ones get created, existing ones
get patched, missing ones get deleted (and their media cascades).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction

from environments.models import Project
from facilities.models.explore import ExplorePhase, ExplorePoint
from facilities.services.explore_media_service import (
    serialize_media,
    sync_media_for_point,
)
from ifc_processor.models import IFCEntity, IFCSpatialElement

logger = logging.getLogger(__name__)


@transaction.atomic
def bulk_sync_points(
    project: Project,
    payload_points: list[dict[str, Any]],
    user,
) -> dict[str, str]:
    """Reconcile points + their media against the iframe's snapshot.

    Returns a ``client_id → real URL`` map for newly persisted media so
    the host page can rewrite the iframe's in-memory data URLs to real
    server URLs on the next ``SET_USER_STATE``.

    Points whose ``floorId`` does not resolve to a known
    :class:`IFCSpatialElement` in this project are skipped — V1 only
    persists IFC-anchored points (see plan §Known risks).
    """
    phase_by_name = {p.name: p for p in ExplorePhase.objects.filter(project=project)}
    floor_cache: dict[str, IFCSpatialElement | None] = {}
    entity_cache: dict[str, IFCEntity | None] = {}
    existing = {p.client_id: p for p in ExplorePoint.objects.filter(project=project)}
    seen: set[str] = set()
    media_url_map: dict[str, str] = {}

    for item in payload_points:
        client_id = (item.get("id") or "").strip()
        if not client_id:
            continue
        floor_id = item.get("floorId") or ""
        floor = _resolve_floor(floor_id, project, floor_cache)
        if floor is None:
            logger.debug(
                "Skipping point %s — no IFCSpatialElement %s in project", client_id, floor_id
            )
            continue
        seen.add(client_id)
        point = existing.get(client_id)
        if point is None:
            point = _create_point(
                project, floor, client_id, item, user, phase_by_name, entity_cache
            )
        else:
            _update_point(point, floor, item, phase_by_name, entity_cache)

        media_results = sync_media_for_point(
            point,
            item.get("media", []) or [],
            user,
            phase_by_name,
        )
        # Only NEWLY created media goes into the map. Including unchanged
        # rows would cause the host to re-hydrate on every STATE_CHANGED
        # (which clobbers in-iframe UI state like the current selection).
        for result in media_results:
            if result.created:
                media_url_map[result.client_id] = result.url

    stale = [p for cid, p in existing.items() if cid not in seen]
    for point in stale:
        point.delete()
    return media_url_map


def _resolve_floor(
    floor_id: str,
    project: Project,
    cache: dict[str, IFCSpatialElement | None],
) -> IFCSpatialElement | None:
    if floor_id in cache:
        return cache[floor_id]
    storey: IFCSpatialElement | None = None
    try:
        pk = UUID(floor_id)
    except (ValueError, TypeError):
        cache[floor_id] = None
        return None
    storey = IFCSpatialElement.objects.filter(
        pk=pk,
        ifc_file__project=project,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
    ).first()
    cache[floor_id] = storey
    return storey


def _resolve_entity(
    global_id: str,
    project: Project,
    cache: dict[str, IFCEntity | None],
) -> IFCEntity | None:
    if not global_id:
        return None
    if global_id in cache:
        return cache[global_id]
    entity = (
        IFCEntity.objects.filter(global_id=global_id, ifc_file__project=project)
        .order_by("-created_at")
        .first()
    )
    cache[global_id] = entity
    return entity


def _create_point(
    project: Project,
    floor: IFCSpatialElement,
    client_id: str,
    item: dict[str, Any],
    user,
    phase_by_name: dict[str, ExplorePhase],
    entity_cache: dict[str, IFCEntity | None],
) -> ExplorePoint:
    return ExplorePoint.objects.create(
        project=project,
        floor=floor,
        client_id=client_id,
        label=item.get("label", "") or "",
        x_percent=_coord(item.get("x")),
        y_percent=_coord(item.get("y")),
        phase=phase_by_name.get(item.get("phase", "")),
        ifc_entity=_resolve_entity(item.get("globalId", ""), project, entity_cache),
        table_links=_coerce_tables(item.get("tables")),
        kind=_point_kind(item.get("kind")),
        symbol=(item.get("symbol") or "")[:8] if _point_kind(item.get("kind")) == "custom" else "",
        kind_label=(item.get("kindLabel") or "")[:80] if _point_kind(item.get("kind")) == "custom" else "",
        created_by=user if (user and getattr(user, "is_authenticated", False)) else None,
    )


def _point_kind(value):
    """Coerce an incoming kind to a valid choice (default 'photo')."""
    return value if value in ExplorePoint.Kind.values else "photo"


def _update_point(
    point: ExplorePoint,
    floor: IFCSpatialElement,
    item: dict[str, Any],
    phase_by_name: dict[str, ExplorePhase],
    entity_cache: dict[str, IFCEntity | None],
) -> None:
    dirty = False
    if point.floor_id != floor.pk:
        point.floor = floor
        dirty = True
    label = item.get("label", "") or ""
    if point.label != label:
        point.label = label
        dirty = True
    x = _coord(item.get("x"))
    if point.x_percent != x:
        point.x_percent = x
        dirty = True
    y = _coord(item.get("y"))
    if point.y_percent != y:
        point.y_percent = y
        dirty = True
    phase = phase_by_name.get(item.get("phase", ""))
    if point.phase_id != (phase.pk if phase else None):
        point.phase = phase
        dirty = True
    entity = _resolve_entity(item.get("globalId", ""), point.project, entity_cache)
    if point.ifc_entity_id != (entity.pk if entity else None):
        point.ifc_entity = entity
        dirty = True
    tables = _coerce_tables(item.get("tables"))
    if point.table_links != tables:
        point.table_links = tables
        dirty = True
    kind = _point_kind(item.get("kind"))
    if point.kind != kind:
        point.kind = kind
        dirty = True
    symbol = (item.get("symbol") or "")[:8] if kind == "custom" else ""
    if point.symbol != symbol:
        point.symbol = symbol
        dirty = True
    kind_label = (item.get("kindLabel") or "")[:80] if kind == "custom" else ""
    if point.kind_label != kind_label:
        point.kind_label = kind_label
        dirty = True
    if dirty:
        point.save()


def _coord(value: Any) -> Decimal:
    """Clamp a percent coordinate to [0, 100] with three-decimal precision."""
    try:
        n = Decimal(str(value if value is not None else 0))
    except Exception:
        n = Decimal("0")
    if n < 0:
        n = Decimal("0")
    if n > 100:
        n = Decimal("100")
    return n.quantize(Decimal("0.001"))


def _coerce_tables(raw: Any) -> list[dict[str, str]]:
    """Validate the per-point linked-table list shape."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        out.append(
            {
                "key": str(key),
                "filterBy": str(item.get("filterBy") or "globalId"),
            }
        )
    return out


def serialize_point(point: ExplorePoint) -> dict[str, Any]:
    """Return a point in the in-iframe shape Pavla's state expects."""
    return {
        "id": point.client_id,
        "floorId": str(point.floor_id),
        "label": point.label,
        "roomId": point.ifc_entity.global_id if point.ifc_entity_id else "",
        "globalId": point.ifc_entity.global_id if point.ifc_entity_id else "",
        "ifcType": point.ifc_entity.ifc_type if point.ifc_entity_id else "",
        "x": float(point.x_percent),
        "y": float(point.y_percent),
        "phase": point.phase.name if point.phase_id else "",
        "kind": point.kind,
        "symbol": point.symbol,
        "kindLabel": point.kind_label,
        "tables": point.table_links or [],
        "media": [serialize_media(m) for m in point.media.all()],
    }
