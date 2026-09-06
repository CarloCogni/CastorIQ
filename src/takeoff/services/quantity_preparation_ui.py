# takeoff/services/quantity_preparation_ui.py
"""UI-only Quantity Preparation Data Model defaults (Slice 2a).

No persistence, no writeback, no cost, no Ask/Modify integration.
Builds presentation payloads from ModelQuantitiesService output plus
session/UI default schema, source mappings, and user-defined measurement rules.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_PREP_ROWS = 50

# Explicit user-owned defaults only. Wall/Slab stay unresolved (no auto basis).
# Unknown classes also stay unresolved — never invent a basis from raw Qto.
_USER_DEFINED_BASIS_BY_CLASS: dict[str, dict[str, str]] = {
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
    "IfcDoor": {
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

_UNRESOLVED_BY_DEFAULT = frozenset({"IfcWall", "IfcSlab"})


def default_schema_fields() -> list[dict[str, str]]:
    """Return Schema Builder fields (not persisted)."""
    return [
        {
            "label": "Level / Storey",
            "required": "Optional",
            "availability": "Spatial source",
        },
        {
            "label": "Zone",
            "required": "Optional",
            "availability": "Not indexed",
        },
        {
            "label": "IFC Class",
            "required": "Required",
            "availability": "Available from IFC",
        },
        {
            "label": "Type Name",
            "required": "Optional",
            "availability": "Castor indexed field",
        },
        {
            "label": "Quantity Source",
            "required": "Required",
            "availability": "Available from Qto",
        },
        {
            "label": "Quantity Basis",
            "required": "Required",
            "availability": "Manual",
        },
        {
            "label": "Unit Basis",
            "required": "Optional",
            "availability": "Castor indexed field",
        },
        {
            "label": "Total Quantity",
            "required": "Optional",
            "availability": "Castor indexed field",
        },
        {
            "label": "Classification Code",
            "required": "Optional",
            "availability": "Not indexed",
        },
        {
            "label": "Package / BOQ Mapping",
            "required": "Optional",
            "availability": "Not indexed",
        },
        {
            "label": "Work Package",
            "required": "Optional",
            "availability": "Not indexed",
        },
        {
            "label": "Review Status",
            "required": "Optional",
            "availability": "Castor indexed field",
        },
        {
            "label": "Handoff Status",
            "required": "Optional",
            "availability": "Future Modify handoff",
        },
    ]


def default_source_mappings() -> list[dict[str, str]]:
    """Return Source Mapping rows (session defaults, not persisted)."""
    return [
        {
            "field": "Level / Storey",
            "source": "Spatial container",
            "detail": "spatial_container",
        },
        {
            "field": "Zone",
            "source": "Not mapped",
            "detail": "Not indexed",
        },
        {
            "field": "IFC Class",
            "source": "IFC property",
            "detail": "ifc_type",
        },
        {
            "field": "Type Name",
            "source": "Castor indexed field",
            "detail": "element_type.name",
        },
        {
            "field": "Quantity Source",
            "source": "Qto property",
            "detail": "Named Qto measure when rule selected",
        },
        {
            "field": "Quantity Basis",
            "source": "Manual field",
            "detail": "User-defined measurement rule",
        },
        {
            "field": "Unit Basis",
            "source": "Castor indexed field",
            "detail": "model volume / area / length units or count",
        },
        {
            "field": "Total Quantity",
            "source": "Castor indexed field",
            "detail": "Derived only when source + basis selected",
        },
        {
            "field": "Classification Code",
            "source": "Not mapped",
            "detail": "Unavailable — Future Modify handoff",
        },
        {
            "field": "Package / BOQ Mapping",
            "source": "Not mapped",
            "detail": "Schema field only — not BOQ generation",
        },
        {
            "field": "Work Package",
            "source": "Not mapped",
            "detail": "Future Modify handoff",
        },
        {
            "field": "Review Status",
            "source": "Castor indexed field",
            "detail": "Computed from preparation row",
        },
        {
            "field": "Handoff Status",
            "source": "Future Modify handoff",
            "detail": "Ready / Not ready for Castor Modify",
        },
    ]


def user_defined_measurement_rules() -> list[dict[str, str]]:
    """Return user-owned measurement rule defaults (not material-detected)."""
    return [
        {
            "model_group": "IfcBeam",
            "quantity_source": "NetVolume",
            "quantity_basis": "NetVolume",
            "unit_basis": "model volume units",
            "note": "User-defined starter default",
        },
        {
            "model_group": "IfcColumn",
            "quantity_source": "NetVolume",
            "quantity_basis": "NetVolume",
            "unit_basis": "model volume units",
            "note": "User-defined starter default",
        },
        {
            "model_group": "IfcDoor",
            "quantity_source": "Count",
            "quantity_basis": "Count",
            "unit_basis": "count",
            "note": "User-defined starter default",
        },
        {
            "model_group": "IfcPipeSegment",
            "quantity_source": "Length",
            "quantity_basis": "Length",
            "unit_basis": "model length units",
            "note": "User-defined starter default",
        },
        {
            "model_group": "IfcWall",
            "quantity_source": "Unresolved",
            "quantity_basis": "Unresolved",
            "unit_basis": "—",
            "note": "Unresolved until user selects basis",
        },
        {
            "model_group": "IfcSlab",
            "quantity_source": "Unresolved",
            "quantity_basis": "Unresolved",
            "unit_basis": "—",
            "note": "Unresolved until user selects basis",
        },
    ]


def _basis_for_class(ifc_class: str) -> dict[str, str]:
    """Return selected basis for a class, or empty unresolved dict."""
    if ifc_class in _UNRESOLVED_BY_DEFAULT:
        return {
            "quantity_source": "",
            "quantity_basis": "",
            "unit_basis": "",
            "basis_unresolved": True,
        }
    selected = _USER_DEFINED_BASIS_BY_CLASS.get(ifc_class)
    if selected is None:
        return {
            "quantity_source": "",
            "quantity_basis": "",
            "unit_basis": "",
            "basis_unresolved": True,
        }
    out = dict(selected)
    out["basis_unresolved"] = False
    return out


def _total_for_basis(row: dict[str, Any], basis: dict[str, Any]) -> float | int | None:
    """Return derived total only when a basis/source is selected; else None."""
    if basis.get("basis_unresolved"):
        return None
    source = (basis.get("quantity_source") or "").strip()
    if not source:
        return None
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
    return None


def _review_status(row: dict[str, Any]) -> str:
    if row.get("basis_unresolved"):
        return "Missing basis rule"
    if row.get("missing_quantity_source"):
        return "Missing source"
    if (
        row.get("missing_classification")
        or row.get("missing_package")
        or row.get("missing_work_package")
    ):
        return (
            "Missing classification"
            if row.get("missing_classification")
            else (
                "Missing package mapping" if row.get("missing_package") else "Missing work package"
            )
        )
    return "Resolved"


def _has_target_context(row: dict[str, Any]) -> bool:
    ifc_class = (row.get("ifc_class") or "").strip()
    if not ifc_class:
        return False
    type_name = (row.get("type_name") or "").strip()
    model_group = (row.get("model_group") or "").strip()
    return bool(type_name or model_group or ifc_class)


def _has_unresolved_schema_field(row: dict[str, Any]) -> bool:
    return bool(
        row.get("basis_unresolved")
        or row.get("missing_quantity_source")
        or row.get("missing_classification")
        or row.get("missing_package")
        or row.get("missing_work_package")
    )


def _handoff_status(row: dict[str, Any]) -> str:
    if _has_target_context(row) and _has_unresolved_schema_field(row):
        return "Ready for Modify handoff"
    return "Not ready"


def build_prep_rows(quantities: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Generated Preparation Data Model rows from indexed aggregates."""
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
        model_group = ifc_class or "Unknown"
        basis = _basis_for_class(ifc_class)
        basis_unresolved = bool(basis.get("basis_unresolved"))
        source = (basis.get("quantity_source") or "").strip()
        missing_source = basis_unresolved or not source
        total = _total_for_basis(raw, basis)

        row: dict[str, Any] = {
            "model_group": model_group,
            "ifc_class": ifc_class,
            "type_name": type_name,
            "quantity_source": "" if basis_unresolved else source,
            "quantity_basis": "" if basis_unresolved else (basis.get("quantity_basis") or ""),
            "unit_basis": "" if basis_unresolved else (basis.get("unit_basis") or ""),
            "total": total,
            "total_display": "Unresolved" if basis_unresolved else total,
            "basis_unresolved": basis_unresolved,
            "classification_code": "",
            "package_boq_mapping": "",
            "work_package": "",
            "element_count": raw.get("element_count"),
            "missing_quantity_source": missing_source,
            "missing_classification": True,
            "missing_package": True,
            "missing_work_package": True,
        }
        row["review_status"] = _review_status(row)
        row["handoff_status"] = _handoff_status(row)
        row["ready_for_handoff"] = row["handoff_status"] == "Ready for Modify handoff"
        rows_out.append(row)
    return rows_out


def build_unresolved_register(prep_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts derived only from the Generated Preparation Data Model."""
    return {
        "missing_quantity_basis_rule": sum(1 for r in prep_rows if r.get("basis_unresolved")),
        "missing_selected_quantity_source": sum(
            1 for r in prep_rows if r.get("missing_quantity_source")
        ),
        "missing_classification": sum(1 for r in prep_rows if r.get("missing_classification")),
        "missing_package_boq_mapping": sum(1 for r in prep_rows if r.get("missing_package")),
        "missing_work_package": sum(1 for r in prep_rows if r.get("missing_work_package")),
        "ready_for_modify_handoff": sum(1 for r in prep_rows if r.get("ready_for_handoff")),
        "not_ready_for_handoff": sum(1 for r in prep_rows if not r.get("ready_for_handoff")),
        "row_count": len(prep_rows),
    }


def build_preparation_ui(quantities: dict[str, Any]) -> dict[str, Any]:
    """Assemble Slice 2a UI context for the Quantities preparation screen."""
    prep_rows = build_prep_rows(quantities) if quantities.get("has_ifc") else []
    return {
        "schema_fields": default_schema_fields(),
        "source_mappings": default_source_mappings(),
        "basis_rules": user_defined_measurement_rules(),
        "basis_rules_banner": (
            "No rule = unresolved. No selected basis = no measurement claim. "
            "Raw IFC quantity values do not mean correct BOQ, 5D, or QS measurement."
        ),
        "source_vs_basis_note": (
            "Quantity Source is the IFC/Qto property used when selected. "
            "Quantity Basis is the user-selected measurement method for the model group."
        ),
        "prep_rows": prep_rows,
        "prep_row_grain": (
            "type" if quantities.get("by_type_shown") and quantities.get("by_type") else "ifc_class"
        ),
        "unresolved_register": build_unresolved_register(prep_rows),
        # Back-compat alias for any leftover template references during Slice 2a.
        "missing_summary": build_unresolved_register(prep_rows),
        "column_config": default_schema_fields(),
        "prep_helper_note": (
            "Rows are generated only from the selected schema, source mappings, "
            "and user-defined measurement rules. If quantity basis is unresolved, "
            "Total Quantity shows Unresolved — not a raw IFC number."
        ),
    }
