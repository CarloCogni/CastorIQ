# takeoff/services/quantity_preparation_ui.py
"""UI-only Quantity Mapping / Preparation defaults for Quantities Slice 1.

No persistence, no writeback, no cost. Builds presentation payloads from
ModelQuantitiesService output plus starter column/basis profiles.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_PREP_ROWS = 50

# Starter / example basis only — not project-detected material logic.
_STARTER_BASIS_BY_CLASS: dict[str, dict[str, str]] = {
    "IfcWall": {
        "quantity_source": "NetVolume",
        "quantity_basis": "NetVolume",
        "unit_basis": "model volume units",
    },
    "IfcBeam": {
        "quantity_source": "NetVolume",
        "quantity_basis": "NetVolume",
        "unit_basis": "model volume units",
    },
    "IfcColumn": {
        "quantity_source": "NetVolume",
        "quantity_basis": "NetVolume",
        "unit_basis": "model volume units",
    },
    "IfcSlab": {
        "quantity_source": "NetArea",
        "quantity_basis": "NetArea",
        "unit_basis": "model area units",
    },
    "IfcDoor": {
        "quantity_source": "Count",
        "quantity_basis": "Count",
        "unit_basis": "count",
    },
    "IfcWindow": {
        "quantity_source": "Count",
        "quantity_basis": "Count",
        "unit_basis": "count",
    },
    "IfcPipeSegment": {
        "quantity_source": "Length",
        "quantity_basis": "Length",
        "unit_basis": "model length units",
    },
}


def default_column_config() -> list[dict[str, str]]:
    """Return starter column configuration rows (not persisted)."""
    return [
        {
            "label": "Level / Storey",
            "source_type": "Spatial container",
            "source_property": "spatial_container",
            "required": "Optional",
        },
        {
            "label": "Zone",
            "source_type": "Manual field",
            "source_property": "— (not indexed)",
            "required": "Optional",
        },
        {
            "label": "IFC Class",
            "source_type": "Castor field",
            "source_property": "ifc_type",
            "required": "Required",
        },
        {
            "label": "Type Name",
            "source_type": "Castor field",
            "source_property": "element_type.name",
            "required": "Optional",
        },
        {
            "label": "Quantity Source",
            "source_type": "Qto property",
            "source_property": "named Qto measure (e.g. NetVolume)",
            "required": "Required",
        },
        {
            "label": "Quantity Total",
            "source_type": "Castor field",
            "source_property": "aggregated named measure",
            "required": "Optional",
        },
        {
            "label": "Classification Code",
            "source_type": "Manual field",
            "source_property": "— (Unavailable)",
            "required": "Optional",
        },
        {
            "label": "Package / BOQ Code",
            "source_type": "Manual field",
            "source_property": "— (not mapped)",
            "required": "Optional",
        },
        {
            "label": "Work Package",
            "source_type": "Manual field",
            "source_property": "— (not mapped)",
            "required": "Optional",
        },
    ]


def starter_basis_rules() -> list[dict[str, str]]:
    """Return illustrative quantity basis rules (not material-detected)."""
    return [
        {
            "model_group": "IfcWall (example: concrete-style)",
            "quantity_source": "NetVolume",
            "quantity_basis": "NetVolume",
            "unit_basis": "model volume units",
            "note": "Example only — material not auto-detected",
        },
        {
            "model_group": "IfcWall (example: block-style)",
            "quantity_source": "NetArea",
            "quantity_basis": "NetArea",
            "unit_basis": "model area units",
            "note": "Example only — material not auto-detected",
        },
        {
            "model_group": "IfcBeam",
            "quantity_source": "NetVolume",
            "quantity_basis": "NetVolume",
            "unit_basis": "model volume units",
            "note": "Starter default",
        },
        {
            "model_group": "IfcDoor",
            "quantity_source": "Count",
            "quantity_basis": "Count",
            "unit_basis": "count",
            "note": "Starter default",
        },
        {
            "model_group": "IfcPipeSegment",
            "quantity_source": "Length",
            "quantity_basis": "Length",
            "unit_basis": "model length units",
            "note": "Starter default",
        },
    ]


def _basis_for_class(ifc_class: str) -> dict[str, str]:
    return dict(
        _STARTER_BASIS_BY_CLASS.get(
            ifc_class,
            {
                "quantity_source": "",
                "quantity_basis": "",
                "unit_basis": "",
            },
        )
    )


def _total_for_basis(row: dict[str, Any], basis: dict[str, str]) -> float | int | None:
    source = (basis.get("quantity_source") or "").strip()
    if source == "Count":
        return int(row.get("element_count") or 0)
    if source == "NetVolume":
        return row.get("net_volume")
    if source == "GrossVolume":
        return row.get("gross_volume")
    if source == "NetArea":
        return row.get("net_area")
    if source == "NetSideArea":
        return row.get("net_side_area")
    if source == "Length":
        return row.get("length")
    # Fallback: first available named measure.
    for key in ("net_volume", "net_area", "net_side_area", "length"):
        if row.get(key) is not None:
            return row.get(key)
    return None


def _pick_source_if_blank(row: dict[str, Any], basis: dict[str, str]) -> dict[str, str]:
    """Fill quantity source/basis from available measures when no class default."""
    if basis.get("quantity_source"):
        return basis
    if row.get("net_volume") is not None:
        return {
            "quantity_source": "NetVolume",
            "quantity_basis": "NetVolume",
            "unit_basis": "model volume units",
        }
    if row.get("net_area") is not None:
        return {
            "quantity_source": "NetArea",
            "quantity_basis": "NetArea",
            "unit_basis": "model area units",
        }
    if row.get("net_side_area") is not None:
        return {
            "quantity_source": "NetSideArea",
            "quantity_basis": "NetSideArea",
            "unit_basis": "model area units",
        }
    if row.get("length") is not None:
        return {
            "quantity_source": "Length",
            "quantity_basis": "Length",
            "unit_basis": "model length units",
        }
    if (row.get("has_ifc_qto") or 0) == 0 and (row.get("element_count") or 0) > 0:
        return {
            "quantity_source": "",
            "quantity_basis": "",
            "unit_basis": "",
        }
    return basis


def build_prep_rows(quantities: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Generated Preparation Table rows from indexed aggregates."""
    rows_out: list[dict[str, Any]] = []
    use_types = bool(quantities.get("by_type_shown")) and bool(quantities.get("by_type"))
    source_rows: list[dict[str, Any]] = (
        list(quantities.get("by_type") or [])
        if use_types
        else list(quantities.get("by_ifc_class") or [])
    )

    for raw in source_rows[:MAX_PREP_ROWS]:
        ifc_class = str(raw.get("ifc_class") or raw.get("ifc_type") or "")
        type_name = str(raw.get("type_name") or "") if use_types else ""
        basis = _pick_source_if_blank(raw, _basis_for_class(ifc_class))
        total = _total_for_basis(raw, basis)
        missing_source = not bool(basis.get("quantity_source"))
        rows_out.append(
            {
                "level": "",  # type/class grain is not a single storey
                "zone": "",
                "ifc_class": ifc_class,
                "type_name": type_name,
                "quantity_source": basis.get("quantity_source") or "",
                "total": total,
                "quantity_basis": basis.get("quantity_basis") or "",
                "unit_basis": basis.get("unit_basis") or "",
                "classification_code": "",
                "package_boq_code": "",
                "work_package": "",
                "element_count": raw.get("element_count"),
                "missing_quantity_source": missing_source,
                "missing_classification": True,
                "missing_package": True,
                "missing_work_package": True,
            }
        )
    return rows_out


def build_missing_summary(prep_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count blank / unmapped fields in the generated preparation rows."""
    return {
        "missing_quantity_source": sum(1 for r in prep_rows if r.get("missing_quantity_source")),
        "missing_classification_code": sum(1 for r in prep_rows if r.get("missing_classification")),
        "missing_package_boq": sum(1 for r in prep_rows if r.get("missing_package")),
        "missing_work_package": sum(1 for r in prep_rows if r.get("missing_work_package")),
        "unmapped_source_fields": sum(
            1
            for r in prep_rows
            if not r.get("zone")
            or not r.get("classification_code")
            or not r.get("package_boq_code")
            or not r.get("work_package")
        ),
        "row_count": len(prep_rows),
    }


def build_preparation_ui(quantities: dict[str, Any]) -> dict[str, Any]:
    """Assemble Slice 1 UI context for the Quantities builder screen."""
    prep_rows = build_prep_rows(quantities) if quantities.get("has_ifc") else []
    return {
        "column_config": default_column_config(),
        "basis_rules": starter_basis_rules(),
        "basis_rules_banner": (
            "Starter rules — review before using for enrichment. "
            "Material examples (concrete / block) are illustrative only; "
            "Castor has not detected material from the model."
        ),
        "prep_rows": prep_rows,
        "prep_row_grain": (
            "type" if quantities.get("by_type_shown") and quantities.get("by_type") else "ifc_class"
        ),
        "missing_summary": build_missing_summary(prep_rows),
        "source_vs_basis_note": (
            "Quantity Source is the IFC/Qto property used. "
            "Quantity Basis is the selected measurement method for the model group."
        ),
        "prep_helper_note": (
            "Rows are generated from selected column mappings. "
            "Missing values remain blank until mapped or enriched. "
            "Quantity Source is the IFC/Qto property used. "
            "Quantity Basis is the selected measurement method for the model group."
        ),
    }
