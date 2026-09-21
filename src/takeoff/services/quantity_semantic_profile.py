# takeoff/services/quantity_semantic_profile.py
"""SEM-4A — minimal User Semantic Mapping Profile readiness (read-only)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

STATUS_AVAILABLE = "available"
STATUS_MISSING = "missing_in_export"
STATUS_USER_MAPPING = "user_mapping_required"
STATUS_CONFIRMED = "confirmed"
STATUS_UNRESOLVED = "unresolved"


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_prop_coverage(scan: Mapping[str, Any], prop_key: str) -> bool:
    key_nonempty = scan.get("key_nonempty") or {}
    try:
        return int(key_nonempty.get(prop_key) or 0) > 0
    except (TypeError, ValueError):
        return False


def _spatial_available(scan: Mapping[str, Any], spatial_key: str) -> bool:
    spatial_nonempty = scan.get("spatial_nonempty") or {}
    try:
        return int(spatial_nonempty.get(spatial_key) or 0) > 0
    except (TypeError, ValueError):
        return False


def _lightweight_scan_summary(project: Any) -> dict[str, Any]:
    """Cheap EXISTS checks for SEM-4A readiness keys — no full property catalogue scan.

    PERF-15C1: when Quantities defers catalogue discovery, readiness must not
    reintroduce a third full IFCEntity iterator solely for this panel.
    """
    from ifc_processor.models import IFCEntity, IFCSpatialElement
    from ifc_processor.services.classification_ref_index import KEY_DISPLAY
    from takeoff.services.ifc_semantic_fields import (
        SPATIAL_STOREY_KEY,
        _latest_completed_ifc,
    )

    key_nonempty: dict[str, int] = {}
    spatial_nonempty: dict[str, int] = {}
    ifc = _latest_completed_ifc(project)
    if ifc is None:
        return {"key_nonempty": key_nonempty, "spatial_nonempty": spatial_nonempty}

    qs = IFCEntity.objects.filter(ifc_file=ifc)
    for prop_key in (
        KEY_DISPLAY,
        "Identity Data.OmniClass Number",
        "Identity Data.OmniClass Title",
        "Identity Data.Assembly Code",
        "Identity Data.Project Level",
    ):
        if qs.filter(properties__has_key=prop_key).exists():
            key_nonempty[prop_key] = 1

    if IFCSpatialElement.objects.filter(ifc_file=ifc, spatial_type="building_storey").exists():
        spatial_nonempty[SPATIAL_STOREY_KEY] = 1
    return {"key_nonempty": key_nonempty, "spatial_nonempty": spatial_nonempty}


def build_semantic_source_readiness(
    *,
    project: Any,
    scan: Mapping[str, Any] | None = None,
    unit_confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact readiness rows for 5D semantic source roles.

    No DB writes. Does not auto-map to Castor ClassificationNode.
    """
    from ifc_processor.services.classification_ref_index import KEY_DISPLAY
    from takeoff.services.ifc_semantic_fields import SPATIAL_STOREY_KEY

    if scan is None:
        scan = _lightweight_scan_summary(project)

    classref_ok = _has_prop_coverage(scan, KEY_DISPLAY)
    omni_num = _has_prop_coverage(scan, "Identity Data.OmniClass Number")
    omni_title = _has_prop_coverage(scan, "Identity Data.OmniClass Title")
    assembly = _has_prop_coverage(scan, "Identity Data.Assembly Code")
    project_level = _has_prop_coverage(scan, "Identity Data.Project Level")
    storey_ok = _spatial_available(scan, SPATIAL_STOREY_KEY)
    zone_prop = any(
        "zone" in str(k).lower()
        for k in (scan.get("key_nonempty") or {})
        if int((scan.get("key_nonempty") or {}).get(k) or 0) > 0
    )

    unit_confirmed = bool(
        (unit_confirmation or {}).get("confirmed")
        or (unit_confirmation or {}).get("has_confirmed_units")
        or (unit_confirmation or {}).get("any_confirmed")
        or (unit_confirmation or {}).get("all_available_confirmed")
        or (unit_confirmation or {}).get("status") == "confirmed"
    )

    fields: list[dict[str, Any]] = []

    # Level
    level_sources: list[str] = []
    if storey_ok:
        level_sources.append("spatial:storey")
    if project_level:
        level_sources.append("prop:Identity Data.Project Level")
    fields.append(
        {
            "field_key": "level",
            "label": "Level",
            "recommended_sources": [
                "spatial:storey",
                "prop:Identity Data.Project Level",
                "user-selected property",
            ],
            "available_sources": level_sources,
            "selected_source": level_sources[0]
            if len(level_sources) == 1
            else (level_sources[0] if level_sources else ""),
            "readiness_status": STATUS_AVAILABLE if level_sources else STATUS_MISSING,
            "message": (
                "Level evidence available from spatial storey and/or Project Level."
                if level_sources
                else "No level evidence found in this IFC export yet."
            ),
        }
    )

    # Zone — SEM-4A does not index struct:zone; honest missing unless zone-like prop.
    zone_sources: list[str] = []
    if zone_prop:
        zone_sources.append("user-selected zone-like property")
    fields.append(
        {
            "field_key": "zone",
            "label": "Zone",
            "recommended_sources": [
                "struct:zone",
                "user-selected property",
                "manual mapping",
            ],
            "available_sources": zone_sources,
            "selected_source": "",
            "readiness_status": STATUS_AVAILABLE if zone_sources else STATUS_MISSING,
            "message": (
                "Zone-like property evidence detected. Select a suitable exported property "
                "as the Zone source during preparation before freezing a new snapshot."
                if zone_sources
                else (
                    "No Zone evidence was found in this IFC export. If a suitable exported "
                    "property exists, select it as the Zone source during preparation "
                    "before freezing a new snapshot."
                )
            ),
        }
    )

    # Classification evidence
    class_sources: list[str] = []
    if classref_ok:
        class_sources.append("classref:ifc")
    if omni_num or omni_title:
        class_sources.append("prop:Identity Data.OmniClass")
    if assembly:
        class_sources.append("prop:Identity Data.Assembly Code")
    fields.append(
        {
            "field_key": "classification",
            "label": "Classification",
            "recommended_sources": [
                "classref:ifc",
                "prop:Identity Data.OmniClass Number",
                "prop:Identity Data.Assembly Code",
                "manual Castor schema mapping",
            ],
            "available_sources": class_sources,
            "selected_source": "classref:ifc"
            if classref_ok
            else (class_sources[0] if class_sources else ""),
            "readiness_status": STATUS_AVAILABLE if class_sources else STATUS_MISSING,
            "message": (
                "Classification evidence available (IFC classification reference "
                "and/or authoring properties). Separate from Castor schema mapping."
                if class_sources
                else "No classification evidence found in this IFC export yet."
            ),
        }
    )

    # Package / Work package — target mapping
    for key, label in (
        ("package", "Package"),
        ("work_package", "Work Package"),
    ):
        fields.append(
            {
                "field_key": key,
                "label": label,
                "recommended_sources": [
                    "Castor schema mapping",
                    "user-selected property",
                ],
                "available_sources": ["Castor schema mapping"],
                "selected_source": "",
                "readiness_status": STATUS_USER_MAPPING,
                "message": (
                    f"{label} uses Castor schema mapping (target nodes). "
                    "Not auto-filled from IFC classification references."
                ),
            }
        )

    # Project unit declaration (IFC project_units) — distinct from quantity-row units.
    fields.append(
        {
            "field_key": "unit",
            "label": "Project unit declaration",
            "recommended_sources": ["IFC project_units + user confirmation"],
            "available_sources": ["IFC project_units"],
            "selected_source": "IFC project_units",
            "readiness_status": STATUS_CONFIRMED if unit_confirmed else STATUS_AVAILABLE,
            "message": (
                "IFC project unit declaration was confirmed for this frozen preparation state."
                if unit_confirmed
                else (
                    "IFC project_units are available; confirm the project unit declaration "
                    "in Quantity units before freezing when needed."
                )
            ),
        }
    )

    # Measurement basis
    fields.append(
        {
            "field_key": "measurement_basis",
            "label": "Measurement Basis",
            "recommended_sources": ["selected QTO / basis rules"],
            "available_sources": ["selected QTO / basis rules"],
            "selected_source": "selected QTO / basis rules",
            "readiness_status": STATUS_AVAILABLE,
            "message": "Measurement Basis comes from selected quantity basis rules.",
        }
    )

    return {
        "fields": fields,
        "helper": (
            "Review which model fields can feed the 5D data model. "
            "Missing evidence is an IFC export gap — if a suitable exported property "
            "exists, map it during a later preparation pass before freezing a new snapshot."
        ),
        "future_bridge_note": (
            "Missing IFC export evidence can be addressed by mapping a suitable "
            "exported property during preparation, then freezing a new snapshot."
        ),
    }


def apply_semantic_profile_to_qty_prep(
    qty_prep: dict[str, Any],
    *,
    project: Any,
    unit_confirmation: Mapping[str, Any] | None = None,
) -> None:
    """Attach semantic_source_readiness onto qty_prep (mutates in place)."""
    enrichment = (qty_prep.get("semantic_filters") or {}).get("entity_enrichment") or {}
    scan = enrichment.get("scan_summary")
    if not isinstance(scan, Mapping) or not scan or not (scan.get("key_nonempty") or {}):
        # PERF-15C1: deferred catalogue leaves empty scan_summary — do not
        # re-run a full IFCEntity catalogue iterator for readiness alone.
        try:
            scan = _lightweight_scan_summary(project)
        except Exception as exc:  # noqa: BLE001
            logger.debug("semantic profile lightweight scan failed: %s", exc)
            scan = {"key_nonempty": {}, "spatial_nonempty": {}}

    qty_prep["semantic_source_readiness"] = build_semantic_source_readiness(
        project=project,
        scan=scan,
        unit_confirmation=unit_confirmation,
    )
