# facilities/services/explore_catalog_service.py
"""Facility / Schedule table catalog for the Explore module.

Pavla's iframe supports per-point linked tables — a panel shows rows
from a host-supplied catalog filtered by the point's room (GlobalID,
room number or department, configurable per table). This service
builds the catalog from existing Castor FM tables: open Work Orders,
Facility Assets and active Permits.

The catalog shape mirrors Pavla's ``SET_TABLE_CATALOG`` protocol:
``{ <key>: { group, label, columns, rows } }``. Rows carry the
``globalId``, ``roomNumber`` and ``department`` fields so the iframe
can filter without a round-trip.
"""

from __future__ import annotations

import logging
from typing import Any

from environments.models import Project
from facilities.models import ActionRequest, FacilityAsset, Permit, WorkOrder
from facilities.services.workorder_service import KANBAN_STATUSES
from ifc_processor.models import IFCEntity

# IFC spatial container types — excluded from the "Elements" table (they are the
# rooms/storeys, not elements placed in them).
_SPATIAL_IFC_TYPES = ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace")

logger = logging.getLogger(__name__)


def build_table_catalog(project: Project) -> dict[str, dict[str, Any]]:
    """Return the catalog of FM tables linkable to a point.

    Three default tables are populated: assets, work, permits. Each
    row carries the join keys the iframe panel uses.
    """
    return {
        "assets": _build_assets_table(project),
        "work": _build_work_table(project),
        "permits": _build_permits_table(project),
        "requests": _build_requests_table(project),
        "elements": _build_elements_table(project),
    }


_PROP_VALUE_MAX = 200


def _keep_prop(key: str) -> bool:
    """Drop authoring-tool noise: Revit internal ids carry no FM meaning and
    inflate the payload (every pset ships an ``…id`` entry)."""
    lowered = key.lower()
    return not (lowered == "id" or lowered.endswith(".id"))


def _flatten_props_dict(props) -> dict[str, Any]:
    """Flatten an entity's properties (one pset level) to a flat key → value
    dict for the per-element property table. Empty values and internal-id
    noise are dropped; long values are truncated to keep the catalog light."""
    if not isinstance(props, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in props.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_value in (None, "", [], {}) or not _keep_prop(str(sub_key)):
                    continue
                out[str(sub_key)] = str(sub_value)[:_PROP_VALUE_MAX]
        elif value not in (None, "", [], {}) and _keep_prop(str(key)):
            out[str(key)] = str(value)[:_PROP_VALUE_MAX]
    return out


def _flatten_params(props) -> str:
    """Flatten an IFC entity's properties (incl. one level of psets) to a compact
    "key: value · key: value" string for the dynamic Parameters column."""
    if not isinstance(props, dict):
        return ""
    parts = []
    for key, value in props.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_value not in (None, "", [], {}):
                    parts.append(f"{sub_key}: {sub_value}")
        elif value not in (None, "", [], {}):
            parts.append(f"{key}: {value}")
    return "  ·  ".join(parts)


def _build_elements_table(project: Project) -> dict[str, Any]:
    """IFC elements placed in a ROOM, with their parameters searchable.

    Only room-anchored elements ship (``spatial_container`` is a SPACE) —
    the table's whole purpose is "what's in this room", and dropping
    storey-anchored rows keeps the postMessage payload sane on big models
    (this catalog travels to the iframe on every Spaces load).
    """
    rows = []
    qs = (
        IFCEntity.objects.filter(
            ifc_file__project=project,
            spatial_container__spatial_type="space",
        )
        .exclude(ifc_type__in=_SPATIAL_IFC_TYPES)
        .select_related("spatial_container", "spatial_container__entity")
    )
    for ent in qs:
        room_global_id, room_props = _room_of(ent.spatial_container)
        props = _flatten_props_dict(ent.properties)
        rows.append(
            {
                "id": str(ent.pk),
                "name": ent.name or "",
                "ifc_type": ent.ifc_type or "",
                # Built from the cleaned props so the filter column and the
                # per-element table agree on what exists. Display-capped —
                # the full values live in ``props`` for the element table.
                "params": "  ·  ".join(f"{k}: {v}" for k, v in props.items())[:400],
                # Structured properties (flat key → value) power the
                # per-element property table where the user picks columns.
                "props": props,
                "elementGlobalId": ent.global_id,
                "globalId": room_global_id,
                "roomNumber": room_props.get("number", ""),
                "department": room_props.get("department", ""),
            }
        )
    return {
        "group": "IFC",
        "label": "Elements in room",
        "columns": [
            {"field": "name", "label": "Name"},
            {"field": "ifc_type", "label": "Type"},
            {"field": "params", "label": "Parameters"},
        ],
        "rows": rows,
    }


def _room_of(spatial) -> tuple[str, dict[str, Any]]:
    """(room GlobalID, room props) for a spatial container — walking UP the
    tree until a SPACE is found (an element anchored at storey level has no
    room). Returns ("", {}) when no space is on the chain."""
    node = spatial
    while node is not None:
        if node.spatial_type == "space" and node.entity_id:
            return node.entity.global_id, _spatial_props(node)
        node = node.parent
    return "", {}


def _build_assets_table(project: Project) -> dict[str, Any]:
    """One row per asset. ``globalId`` is the containing ROOM's GlobalID —
    the join key every linked table shares — so a point linked to room 1A01
    lists the assets located in 1A01. The asset's own entity GlobalID rides
    along as ``elementGlobalId`` for cross-referencing."""
    rows = []
    qs = FacilityAsset.objects.filter(project=project).select_related(
        "ifc_entity",
        "ifc_entity__spatial_container",
        "ifc_entity__spatial_container__entity",
        "ifc_entity__spatial_container__parent",
        "spatial_container",
        "spatial_container__entity",
        "spatial_container__parent",
    )
    for asset in qs:
        room_gid, room_props = _room_of(asset.display_spatial_container)
        rows.append(
            {
                "id": str(asset.pk),
                "tag": asset.asset_tag or "",
                "name": asset.name or (asset.ifc_entity.name if asset.ifc_entity else ""),
                "manufacturer": asset.manufacturer or "",
                "condition": asset.condition_score,
                "globalId": room_gid,
                "elementGlobalId": asset.ifc_entity.global_id if asset.ifc_entity else "",
                "roomNumber": room_props.get("number", ""),
                "department": room_props.get("department", ""),
            }
        )
    return {
        "group": "Assets",
        "label": "Assets",
        "columns": [
            {"field": "tag", "label": "Tag"},
            {"field": "name", "label": "Name"},
            {"field": "manufacturer", "label": "Manufacturer"},
            {"field": "condition", "label": "Condition"},
        ],
        "rows": rows,
    }


def _build_work_table(project: Project) -> dict[str, Any]:
    rows = []
    qs = WorkOrder.objects.filter(project=project, status__in=KANBAN_STATUSES).select_related(
        "affected_asset", "affected_spatial", "affected_spatial__entity"
    )
    for wo in qs:
        # Room join: the WO's own spatial anchor wins; otherwise fall back to
        # the affected asset's location. Both walk up to the nearest SPACE.
        room_gid, room_props = _room_of(wo.affected_spatial)
        if not room_gid and wo.affected_asset:
            room_gid, room_props = _room_of(wo.affected_asset.display_spatial_container)
        rows.append(
            {
                "id": str(wo.pk),
                "wo_number": wo.wo_number,
                "title": wo.title,
                "status": wo.get_status_display(),
                "priority": wo.priority,
                "globalId": room_gid,
                "roomNumber": room_props.get("number", ""),
                "department": room_props.get("department", ""),
                "_status": "open",
            }
        )
    return {
        "group": "Work",
        "label": "Work Orders",
        "columns": [
            {"field": "wo_number", "label": "WO #"},
            {"field": "title", "label": "Title"},
            {"field": "status", "label": "Status"},
            {"field": "priority", "label": "Priority"},
        ],
        "rows": rows,
    }


def _build_permits_table(project: Project) -> dict[str, Any]:
    """Permits join rooms through their linked assets (Permit.assets M2M):
    one row per (permit, room) pair, so filtering by the point's room shows
    every permit touching an asset in that room. Permits with no room-
    anchored asset emit one row with an empty join key (visible unfiltered)."""
    rows = []
    qs = Permit.objects.filter(project=project).prefetch_related(
        "assets__ifc_entity__spatial_container__entity",
        "assets__ifc_entity__spatial_container__parent",
        "assets__spatial_container__entity",
        "assets__spatial_container__parent",
    )
    for permit in qs:
        base = {
            "id": str(permit.pk),
            "title": permit.title,
            "permit_type": permit.get_kind_display(),
            "status": permit.get_status_display(),
            "valid_until": permit.valid_until.isoformat() if permit.valid_until else "",
        }
        rooms: dict[str, dict[str, Any]] = {}
        for asset in permit.assets.all():
            room_gid, room_props = _room_of(asset.display_spatial_container)
            if room_gid:
                rooms[room_gid] = room_props
        if rooms:
            for room_gid, room_props in rooms.items():
                rows.append(
                    {
                        **base,
                        "globalId": room_gid,
                        "roomNumber": room_props.get("number", ""),
                        "department": room_props.get("department", ""),
                    }
                )
        else:
            rows.append({**base, "globalId": "", "roomNumber": "", "department": ""})
    return {
        "group": "Permits",
        "label": "Permits",
        "columns": [
            {"field": "title", "label": "Title"},
            {"field": "permit_type", "label": "Type"},
            {"field": "status", "label": "Status"},
            {"field": "valid_until", "label": "Valid Until"},
        ],
        "rows": rows,
    }


def _build_requests_table(project: Project) -> dict[str, Any]:
    rows = []
    qs = ActionRequest.objects.filter(project=project).select_related(
        "affected_asset",
        "affected_asset__ifc_entity",
        "affected_spatial",
        "affected_spatial__entity",
    )
    for ar in qs:
        room_gid, room_props = _room_of(ar.affected_spatial)
        if not room_gid and ar.affected_asset:
            room_gid, room_props = _room_of(ar.affected_asset.display_spatial_container)
        rows.append(
            {
                "id": str(ar.pk),
                "title": ar.title,
                "severity": ar.get_severity_display(),
                "status": ar.get_status_display(),
                "globalId": room_gid,
                "roomNumber": room_props.get("number", ""),
                "department": room_props.get("department", ""),
                "_status": "open" if ar.status == ActionRequest.Status.OPEN else "",
            }
        )
    return {
        "group": "Requests",
        "label": "Action Requests",
        "columns": [
            {"field": "title", "label": "Title"},
            {"field": "severity", "label": "Severity"},
            {"field": "status", "label": "Status"},
        ],
        "rows": rows,
    }


def _spatial_props(spatial) -> dict[str, Any]:
    """Lift number / department from a spatial container's IFC entity."""
    if spatial is None:
        return {}
    entity = getattr(spatial, "entity", None)
    props = getattr(entity, "properties", None) if entity else None
    if not isinstance(props, dict):
        return {}
    return {
        "number": props.get("number", ""),
        "department": props.get("department", ""),
    }
