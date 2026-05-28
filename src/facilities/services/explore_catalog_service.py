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
    }


def _build_assets_table(project: Project) -> dict[str, Any]:
    rows = []
    qs = FacilityAsset.objects.filter(ifc_entity__ifc_file__project=project).select_related(
        "ifc_entity", "spatial_container", "spatial_container__entity"
    )
    for asset in qs:
        global_id = asset.ifc_entity.global_id if asset.ifc_entity else ""
        room_props = _spatial_props(asset.spatial_container)
        rows.append(
            {
                "id": str(asset.pk),
                "tag": asset.asset_tag or "",
                "name": asset.name or (asset.ifc_entity.name if asset.ifc_entity else ""),
                "manufacturer": asset.manufacturer or "",
                "condition": asset.condition_score,
                "globalId": global_id,
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
        asset = wo.affected_asset
        global_id = asset.ifc_entity.global_id if asset and asset.ifc_entity else ""
        room_props = _spatial_props(wo.affected_spatial)
        rows.append(
            {
                "id": str(wo.pk),
                "wo_number": wo.wo_number,
                "title": wo.title,
                "status": wo.get_status_display(),
                "priority": wo.priority,
                "globalId": global_id,
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
    rows = []
    for permit in Permit.objects.filter(project=project):
        rows.append(
            {
                "id": str(permit.pk),
                "title": permit.title,
                "permit_type": permit.permit_type or "",
                "valid_until": permit.valid_until.isoformat() if permit.valid_until else "",
                "globalId": "",
                "roomNumber": "",
                "department": "",
            }
        )
    return {
        "group": "Permits",
        "label": "Permits",
        "columns": [
            {"field": "title", "label": "Title"},
            {"field": "permit_type", "label": "Type"},
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
        asset = ar.affected_asset
        global_id = asset.ifc_entity.global_id if asset and asset.ifc_entity else ""
        room_props = _spatial_props(ar.affected_spatial)
        rows.append(
            {
                "id": str(ar.pk),
                "title": ar.title,
                "severity": ar.get_severity_display(),
                "status": ar.get_status_display(),
                "globalId": global_id,
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
