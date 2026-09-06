# takeoff/services/quantity_preparation_ui.py
"""UI-only Quantity Preparation Data Model (Slice 2a/2b + 3a + 3c-1 + 3c-2a).

No persistence, no writeback, no cost, no Ask/Modify integration.
Builds presentation payloads from ModelQuantitiesService output plus
session/UI schema includes, source mappings, and user-defined measurement rules.

Slice 3a: basis_* GET overrides regenerate prep/register/summary/insights.
Slice 3c-1: field_* GET includes/excludes optional schema fields (session-only).
Slice 3c-2a: source_* GET source-type intent for classification/package/work.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

MAX_PREP_ROWS = 50

ALLOWED_BASIS_VALUES = frozenset({"Unresolved", "NetVolume", "NetArea", "Length", "Count"})

RULE_MODEL_GROUPS: tuple[str, ...] = (
    "IfcBeam",
    "IfcColumn",
    "IfcDoor",
    "IfcPipeSegment",
    "IfcWall",
    "IfcSlab",
)

BASIS_QUERY_PREFIX = "basis_"
FIELD_QUERY_PREFIX = "field_"
SOURCE_QUERY_PREFIX = "source_"

# Slice 3c-2a: source-type intent for optional mapping fields (no values created).
EDITABLE_SOURCE_MAPPING_KEYS: tuple[str, ...] = (
    "classification_code",
    "package_boq_mapping",
    "work_package",
)
ALLOWED_SOURCE_MAPPING_VALUES: frozenset[str] = frozenset(
    {"future_modify_handoff", "manual_field", "not_mapped"}
)
# Sources that count as unresolved mapping gaps when the field is included.
COUNTING_SOURCE_MAPPING_VALUES: frozenset[str] = frozenset(
    {"future_modify_handoff", "manual_field"}
)
SOURCE_MAPPING_OPTION_LABELS: dict[str, str] = {
    "future_modify_handoff": "Future Modify handoff",
    "manual_field": "Manual field",
    "not_mapped": "Not mapped",
}
SOURCE_MAPPING_CELL_HINTS: dict[str, str] = {
    "future_modify_handoff": "Deferred to Modify",
    "manual_field": "Manual value not entered",
    "not_mapped": "Not mapped by session config",
}
DEFAULT_EDITABLE_SOURCE_MAPPING = "future_modify_handoff"

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

_UNIT_FOR_BASIS: dict[str, str] = {
    "NetVolume": "model volume units",
    "NetArea": "model area units",
    "Length": "model length units",
    "Count": "count",
}

# Slice 3c-1 schema include/exclude (session-only GET field_<key>=0|1).
# Defaults preserve approved baseline prep columns (no Level/Zone columns by default).
SCHEMA_FIELD_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "level_storey",
        "label": "Level / Storey",
        "required_label": "Optional",
        "availability": "Spatial source",
        "default_included": False,
        "locked": False,
        "note": "Optional spatial field — excluded by default (not in baseline prep columns).",
    },
    {
        "key": "zone",
        "label": "Zone",
        "required_label": "Optional",
        "availability": "Not indexed",
        "default_included": False,
        "locked": False,
        "note": "Optional — excluded by default (not indexed).",
    },
    {
        "key": "ifc_class",
        "label": "IFC Class",
        "required_label": "Required",
        "availability": "Available from IFC",
        "default_included": True,
        "locked": True,
        "note": "Core locked — required for the generated model.",
    },
    {
        "key": "type_name",
        "label": "Type Name",
        "required_label": "Optional",
        "availability": "Castor indexed field",
        "default_included": True,
        "locked": False,
        "note": "Optional — included by default.",
    },
    {
        "key": "quantity_source",
        "label": "Quantity Source",
        "required_label": "Required",
        "availability": "Available from Qto",
        "default_included": True,
        "locked": True,
        "note": "Core locked — required for measurement.",
    },
    {
        "key": "quantity_basis",
        "label": "Quantity Basis",
        "required_label": "Required",
        "availability": "Manual",
        "default_included": True,
        "locked": True,
        "note": "Core locked — required for measurement.",
    },
    {
        "key": "unit_basis",
        "label": "Unit Basis",
        "required_label": "Core output",
        "availability": "Castor indexed field",
        "default_included": True,
        "locked": True,
        "note": "Core locked — derived from Quantity Basis.",
    },
    {
        "key": "total_quantity",
        "label": "Total Quantity",
        "required_label": "Core output",
        "availability": "Castor indexed field",
        "default_included": True,
        "locked": True,
        "note": "Core locked — measurement output column.",
    },
    {
        "key": "classification_code",
        "label": "Classification Code",
        "required_label": "Optional",
        "availability": "Not indexed",
        "default_included": True,
        "locked": False,
        "note": "Optional mapping field — not indexed yet.",
    },
    {
        "key": "package_boq_mapping",
        "label": "Package / BOQ Mapping",
        "required_label": "Optional",
        "availability": "Not indexed",
        "default_included": True,
        "locked": False,
        "note": "Schema field only — not BOQ generation.",
    },
    {
        "key": "work_package",
        "label": "Work Package",
        "required_label": "Optional",
        "availability": "Not indexed",
        "default_included": True,
        "locked": False,
        "note": "Optional — Future Modify handoff candidate.",
    },
    {
        "key": "review_status",
        "label": "Review Status",
        "required_label": "Core output",
        "availability": "Castor indexed field",
        "default_included": True,
        "locked": True,
        "note": "Core locked — safety status always visible.",
    },
    {
        "key": "handoff_status",
        "label": "Handoff Status",
        "required_label": "Core output",
        "availability": "Future Modify handoff",
        "default_included": True,
        "locked": True,
        "note": "Core locked — safety status always visible.",
    },
)

SCHEMA_FIELD_KEYS: frozenset[str] = frozenset(spec["key"] for spec in SCHEMA_FIELD_SPECS)
LOCKED_SCHEMA_KEYS: frozenset[str] = frozenset(
    spec["key"] for spec in SCHEMA_FIELD_SPECS if spec["locked"]
)
EDITABLE_SCHEMA_KEYS: frozenset[str] = frozenset(
    spec["key"] for spec in SCHEMA_FIELD_SPECS if not spec["locked"]
)


def default_schema_includes() -> dict[str, bool]:
    """Return default include map (no query params)."""
    return {spec["key"]: bool(spec["default_included"]) for spec in SCHEMA_FIELD_SPECS}


def parse_schema_includes_from_query(query: Mapping[str, Any]) -> dict[str, bool]:
    """Parse field_<key>=0|1; locked keys always included; invalid ignored."""
    includes = default_schema_includes()
    for key in EDITABLE_SCHEMA_KEYS:
        param = f"{FIELD_QUERY_PREFIX}{key}"
        if param not in query:
            continue
        raw = query.get(param)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        value = str(raw or "").strip().lower()
        if value in {"1", "true", "yes", "on", "include"}:
            includes[key] = True
        elif value in {"0", "false", "no", "off", "exclude"}:
            includes[key] = False
        else:
            logger.info("Ignoring invalid schema include %s=%r", param, value)
    for key in LOCKED_SCHEMA_KEYS:
        includes[key] = True
    return includes


def build_schema_fields_ui(schema_includes: Mapping[str, bool]) -> list[dict[str, Any]]:
    """Schema Builder rows with session include state (3c-1)."""
    rows: list[dict[str, Any]] = []
    for spec in SCHEMA_FIELD_SPECS:
        key = str(spec["key"])
        included = bool(schema_includes.get(key, spec["default_included"]))
        if spec["locked"]:
            included = True
        rows.append(
            {
                "key": key,
                "label": spec["label"],
                "required": spec["required_label"],
                "required_label": spec["required_label"],
                "availability": spec["availability"],
                "included": included,
                "locked": bool(spec["locked"]),
                "editable": not bool(spec["locked"]),
                "note": spec["note"],
                "param_name": f"{FIELD_QUERY_PREFIX}{key}",
            }
        )
    return rows


def default_schema_fields() -> list[dict[str, Any]]:
    """Return Schema Builder fields for default includes (back-compat)."""
    return build_schema_fields_ui(default_schema_includes())


def default_source_mapping_intents() -> dict[str, str]:
    """Default source-type intents for editable mapping fields (baseline-preserving)."""
    return {key: DEFAULT_EDITABLE_SOURCE_MAPPING for key in EDITABLE_SOURCE_MAPPING_KEYS}


def parse_source_mappings_from_query(query: Mapping[str, Any]) -> dict[str, str]:
    """Parse source_<key>=enum for editable mapping fields; invalid ignored."""
    intents = default_source_mapping_intents()
    for key in EDITABLE_SOURCE_MAPPING_KEYS:
        param = f"{SOURCE_QUERY_PREFIX}{key}"
        if param not in query:
            continue
        raw = query.get(param)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        value = str(raw or "").strip().lower()
        if value in ALLOWED_SOURCE_MAPPING_VALUES:
            intents[key] = value
    return intents


def _source_counts_as_gap(source: str) -> bool:
    """True when source intent should count as an unresolved mapping gap."""
    return source in COUNTING_SOURCE_MAPPING_VALUES


def _mapping_cell_hint(source: str) -> str:
    """Session display hint for empty mapping cells (no values invented)."""
    return SOURCE_MAPPING_CELL_HINTS.get(source, SOURCE_MAPPING_CELL_HINTS["not_mapped"])


def build_source_mappings_ui(
    source_intents: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return Source Mapping rows; only classification/package/work are editable."""
    intents = dict(source_intents or default_source_mapping_intents())
    for key in EDITABLE_SOURCE_MAPPING_KEYS:
        if intents.get(key) not in ALLOWED_SOURCE_MAPPING_VALUES:
            intents[key] = DEFAULT_EDITABLE_SOURCE_MAPPING

    option_rows = [
        {"value": value, "label": SOURCE_MAPPING_OPTION_LABELS[value]}
        for value in (
            "future_modify_handoff",
            "manual_field",
            "not_mapped",
        )
    ]

    def editable_row(key: str, label: str, note: str) -> dict[str, Any]:
        source = intents[key]
        return {
            "key": key,
            "field": label,
            "label": label,
            "editable": True,
            "source": source,
            "source_label": SOURCE_MAPPING_OPTION_LABELS[source],
            "detail": note,
            "param_name": f"{SOURCE_QUERY_PREFIX}{key}",
            "options": option_rows,
        }

    locked_rows: list[dict[str, Any]] = [
        {
            "key": "level_storey",
            "field": "Level / Storey",
            "label": "Level / Storey",
            "editable": False,
            "source": "spatial_container",
            "source_label": "Spatial container",
            "detail": "Spatial container — Level source controls deferred.",
            "param_name": "",
            "options": [],
        },
        {
            "key": "zone",
            "field": "Zone",
            "label": "Zone",
            "editable": False,
            "source": "not_mapped",
            "source_label": "Not mapped",
            "detail": "Not indexed — Zone source controls deferred.",
            "param_name": "",
            "options": [],
        },
        {
            "key": "ifc_class",
            "field": "IFC Class",
            "label": "IFC Class",
            "editable": False,
            "source": "ifc_property",
            "source_label": "IFC property",
            "detail": "Core locked — ifc_type",
            "param_name": "",
            "options": [],
        },
        {
            "key": "type_name",
            "field": "Type Name",
            "label": "Type Name",
            "editable": False,
            "source": "castor_indexed_field",
            "source_label": "Castor indexed field",
            "detail": "Core indexed — element_type.name (source type locked here)",
            "param_name": "",
            "options": [],
        },
        {
            "key": "quantity_source",
            "field": "Quantity Source",
            "label": "Quantity Source",
            "editable": False,
            "source": "qto_property",
            "source_label": "Qto property",
            "detail": "Core locked — named Qto measure when rule selected",
            "param_name": "",
            "options": [],
        },
        {
            "key": "quantity_basis",
            "field": "Quantity Basis",
            "label": "Quantity Basis",
            "editable": False,
            "source": "manual_field",
            "source_label": "Manual field",
            "detail": "Core locked — user-defined measurement rule (basis_*)",
            "param_name": "",
            "options": [],
        },
        {
            "key": "unit_basis",
            "field": "Unit Basis",
            "label": "Unit Basis",
            "editable": False,
            "source": "castor_indexed_field",
            "source_label": "Castor indexed field",
            "detail": "Core locked — derived from Quantity Basis",
            "param_name": "",
            "options": [],
        },
        {
            "key": "total_quantity",
            "field": "Total Quantity",
            "label": "Total Quantity",
            "editable": False,
            "source": "castor_indexed_field",
            "source_label": "Castor indexed field",
            "detail": "Core locked — derived only when source + basis selected",
            "param_name": "",
            "options": [],
        },
    ]

    mapping_rows = [
        editable_row(
            "classification_code",
            "Classification Code",
            "Mapping intent only — no classification value created in this slice.",
        ),
        editable_row(
            "package_boq_mapping",
            "Package / BOQ Mapping",
            "Schema field only — not BOQ generation. Mapping intent only.",
        ),
        editable_row(
            "work_package",
            "Work Package",
            "Mapping intent only — Modify handoff remains disabled.",
        ),
    ]

    trailing_locked: list[dict[str, Any]] = [
        {
            "key": "review_status",
            "field": "Review Status",
            "label": "Review Status",
            "editable": False,
            "source": "castor_indexed_field",
            "source_label": "Castor indexed field",
            "detail": "Core locked — computed from preparation row",
            "param_name": "",
            "options": [],
        },
        {
            "key": "handoff_status",
            "field": "Handoff Status",
            "label": "Handoff Status",
            "editable": False,
            "source": "future_modify_handoff",
            "source_label": "Future Modify handoff",
            "detail": "Core locked — Eligible / Not eligible (send remains disabled)",
            "param_name": "",
            "options": [],
        },
    ]

    # Preserve prior display order: spatial → type/core measures → mapping → status.
    return [
        locked_rows[0],  # level
        locked_rows[1],  # zone
        locked_rows[2],  # ifc_class
        locked_rows[3],  # type_name
        locked_rows[4],  # quantity_source
        locked_rows[5],  # quantity_basis
        locked_rows[6],  # unit_basis
        locked_rows[7],  # total_quantity
        *mapping_rows,
        *trailing_locked,
    ]


def default_source_mappings() -> list[dict[str, Any]]:
    """Return Source Mapping rows for default intents (back-compat)."""
    return build_source_mappings_ui(default_source_mapping_intents())


def unit_basis_for(basis: str) -> str:
    """Return display unit label for a selected basis."""
    if basis == "Unresolved" or not basis:
        return "—"
    return _UNIT_FOR_BASIS.get(basis, "—")


def default_basis_label_for_group(model_group: str) -> str:
    """Return Slice 2 default basis label for a starter model group."""
    if model_group in _UNRESOLVED_BY_DEFAULT:
        return "Unresolved"
    selected = _USER_DEFINED_BASIS_BY_CLASS.get(model_group)
    if selected is None:
        return "Unresolved"
    return selected.get("quantity_basis") or "Unresolved"


def parse_basis_overrides_from_query(query: Mapping[str, Any]) -> dict[str, str]:
    """Parse basis_<IfcClass>=Value query params; ignore invalid values safely.

    Missing keys are omitted (caller applies Slice 2 defaults).
    Invalid values are omitted (treated as no override → default).
    """
    overrides: dict[str, str] = {}
    for group in RULE_MODEL_GROUPS:
        key = f"{BASIS_QUERY_PREFIX}{group}"
        if key not in query:
            continue
        raw = query.get(key)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        value = str(raw or "").strip()
        if value not in ALLOWED_BASIS_VALUES:
            logger.info("Ignoring invalid basis override %s=%r", key, value)
            continue
        overrides[group] = value
    return overrides


def _basis_dict_from_label(basis_label: str) -> dict[str, Any]:
    """Build internal basis dict from an allowed basis label."""
    if basis_label == "Unresolved" or not basis_label:
        return {
            "quantity_source": "",
            "quantity_basis": "",
            "unit_basis": "",
            "basis_unresolved": True,
        }
    return {
        "quantity_source": basis_label,
        "quantity_basis": basis_label,
        "unit_basis": unit_basis_for(basis_label),
        "basis_unresolved": False,
    }


def _basis_for_class(
    ifc_class: str,
    basis_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return selected basis for a class, applying optional overrides."""
    overrides = basis_overrides or {}
    if ifc_class in overrides:
        return _basis_dict_from_label(overrides[ifc_class])

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
    out: dict[str, Any] = dict(selected)
    out["basis_unresolved"] = False
    return out


def _measure_value(row: dict[str, Any], source: str) -> float | int | None:
    """Return raw aggregate measure for a selected source, or None if unavailable."""
    if source == "Count":
        # Count is always available from element_count (may be zero).
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


def _total_for_basis(row: dict[str, Any], basis: dict[str, Any]) -> float | int | None:
    """Return derived total only when a basis/source is selected and available."""
    if basis.get("basis_unresolved"):
        return None
    source = (basis.get("quantity_source") or "").strip()
    if not source:
        return None
    return _measure_value(row, source)


def _review_status(row: dict[str, Any]) -> str:
    if row.get("basis_unresolved"):
        return "Missing basis rule"
    if row.get("missing_quantity_source"):
        return "Missing selected quantity source"
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
        return "Eligible for Modify handoff"
    return "Not eligible"


def available_indexed_measures_by_class(
    quantities: Mapping[str, Any] | None,
) -> dict[str, set[str]]:
    """Return indexed measure names present per IFC class from aggregates.

    Used only to label availability — never as a recommended basis.
    Count is available whenever the class has at least one indexed element.
    """
    if not quantities:
        return {}
    found: dict[str, set[str]] = {}
    for raw in list(quantities.get("by_ifc_class") or []) + list(quantities.get("by_type") or []):
        ifc_class = str(raw.get("ifc_class") or raw.get("ifc_type") or "").strip()
        if not ifc_class:
            continue
        measures = found.setdefault(ifc_class, set())
        if raw.get("net_volume") is not None:
            measures.add("NetVolume")
        if raw.get("net_area") is not None:
            measures.add("NetArea")
        if raw.get("length") is not None:
            measures.add("Length")
        if raw.get("element_count") is not None:
            measures.add("Count")
    return found


def _basis_option_entries(*, available: set[str]) -> list[dict[str, Any]]:
    """Build select options with availability labels (all remain selectable).

    Unavailable measures stay enabled so users can still declare a basis and see
    Missing selected quantity source. Disabling would block that preparation gap
    workflow (e.g. choosing NetArea when only NetVolume is indexed).
    """
    entries: list[dict[str, Any]] = []
    for value in ("Unresolved", "NetVolume", "NetArea", "Length", "Count"):
        if value == "Unresolved":
            available_here = True
        else:
            available_here = value in available
        label = value if available_here or value == "Unresolved" else f"{value} (not indexed)"
        entries.append(
            {
                "value": value,
                "label": label,
                "available": available_here,
                "disabled": False,
            }
        )
    return entries


def user_defined_measurement_rules(
    basis_overrides: Mapping[str, str] | None = None,
    quantities: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return measurement rules for the six starter groups (session-only UI)."""
    overrides = basis_overrides or {}
    availability = available_indexed_measures_by_class(quantities)
    rules: list[dict[str, Any]] = []
    for group in RULE_MODEL_GROUPS:
        label = overrides.get(group) or default_basis_label_for_group(group)
        if label not in ALLOWED_BASIS_VALUES:
            label = "Unresolved"
        unresolved = label == "Unresolved"
        available = availability.get(group, set())
        available_sorted = sorted(available)
        measure_missing = (not unresolved) and label not in available
        if unresolved:
            note = "Select a basis, then Generate"
            status = "Needs basis"
        elif measure_missing:
            note = (
                "User-selected for the current configuration — selected measure "
                "is not indexed for this model group (Missing selected quantity source)."
            )
            status = "Missing selected quantity source"
        else:
            note = (
                "User-selected for the current configuration — not a Castor "
                "recommendation of measurement method."
            )
            status = "Basis selected"
        rules.append(
            {
                "model_group": group,
                "quantity_source": "Unresolved" if unresolved else label,
                "quantity_basis": "Unresolved" if unresolved else label,
                "unit_basis": unit_basis_for(label),
                "note": note,
                "status": status,
                "needs_basis_action": unresolved,
                "available_indexed_measures": available_sorted,
                "available_measures_label": (
                    " / ".join(available_sorted) if available_sorted else "None indexed"
                ),
                "basis_options": _basis_option_entries(available=available),
                "param_name": f"{BASIS_QUERY_PREFIX}{group}",
            }
        )
    return rules


def build_prep_rows(
    quantities: dict[str, Any],
    basis_overrides: Mapping[str, str] | None = None,
    schema_includes: Mapping[str, bool] | None = None,
    source_mappings: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build Generated Preparation Data Model rows from indexed aggregates."""
    includes = dict(schema_includes or default_schema_includes())
    for key in LOCKED_SCHEMA_KEYS:
        includes[key] = True
    intents = dict(source_mappings or default_source_mapping_intents())
    for key in EDITABLE_SOURCE_MAPPING_KEYS:
        if intents.get(key) not in ALLOWED_SOURCE_MAPPING_VALUES:
            intents[key] = DEFAULT_EDITABLE_SOURCE_MAPPING

    class_src = intents["classification_code"]
    package_src = intents["package_boq_mapping"]
    work_src = intents["work_package"]

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
        basis = _basis_for_class(ifc_class, basis_overrides)
        basis_unresolved = bool(basis.get("basis_unresolved"))
        source = (basis.get("quantity_source") or "").strip()
        total = _total_for_basis(raw, basis)

        if basis_unresolved:
            missing_source = True
            total_display: float | int | str = "Unresolved"
            quantity_source = ""
            quantity_basis = ""
            unit_basis = ""
        else:
            # Selected basis shown even when measure is missing on the row.
            quantity_source = source
            quantity_basis = basis.get("quantity_basis") or source
            unit_basis = basis.get("unit_basis") or unit_basis_for(source)
            missing_source = total is None
            total_display = "—" if missing_source else total

        # Mapping gaps only when field included AND source intent counts as unresolved.
        include_classification = bool(includes.get("classification_code"))
        include_package = bool(includes.get("package_boq_mapping"))
        include_work = bool(includes.get("work_package"))
        missing_classification = include_classification and _source_counts_as_gap(class_src)
        missing_package = include_package and _source_counts_as_gap(package_src)
        missing_work = include_work and _source_counts_as_gap(work_src)

        row: dict[str, Any] = {
            "model_group": model_group,
            "ifc_class": ifc_class,
            "type_name": type_name,
            "level_storey": "",
            "zone": "",
            "quantity_source": quantity_source,
            "quantity_basis": quantity_basis,
            "unit_basis": unit_basis,
            "total": total,
            "total_display": total_display,
            "basis_unresolved": basis_unresolved,
            "classification_code": "",
            "package_boq_mapping": "",
            "work_package": "",
            "classification_source": class_src if include_classification else "",
            "package_boq_mapping_source": package_src if include_package else "",
            "work_package_source": work_src if include_work else "",
            "classification_hint": (
                _mapping_cell_hint(class_src) if include_classification else ""
            ),
            "package_boq_mapping_hint": (
                _mapping_cell_hint(package_src) if include_package else ""
            ),
            "work_package_hint": _mapping_cell_hint(work_src) if include_work else "",
            "element_count": raw.get("element_count"),
            "missing_quantity_source": missing_source,
            "missing_classification": missing_classification,
            "missing_package": missing_package,
            "missing_work_package": missing_work,
        }
        row["review_status"] = _review_status(row)
        row["handoff_status"] = _handoff_status(row)
        row["eligible_for_handoff"] = row["handoff_status"] == "Eligible for Modify handoff"
        # Back-compat alias used by earlier Slice 2a/2b tests and register keys.
        row["ready_for_handoff"] = row["eligible_for_handoff"]
        rows_out.append(row)
    return rows_out


def build_unresolved_register(prep_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts derived only from the Generated Preparation Data Model."""
    eligible = sum(
        1 for r in prep_rows if r.get("eligible_for_handoff") or r.get("ready_for_handoff")
    )
    return {
        "missing_quantity_basis_rule": sum(1 for r in prep_rows if r.get("basis_unresolved")),
        "missing_selected_quantity_source": sum(
            1 for r in prep_rows if r.get("missing_quantity_source")
        ),
        "missing_classification": sum(1 for r in prep_rows if r.get("missing_classification")),
        "missing_package_boq_mapping": sum(1 for r in prep_rows if r.get("missing_package")),
        "missing_work_package": sum(1 for r in prep_rows if r.get("missing_work_package")),
        "eligible_for_modify_handoff": eligible,
        "not_eligible_for_handoff": len(prep_rows) - eligible,
        # Back-compat aliases
        "ready_for_modify_handoff": eligible,
        "not_ready_for_handoff": len(prep_rows) - eligible,
        "row_count": len(prep_rows),
    }


def _count_bar_items(
    counts: dict[str, int],
    *,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Return count-first bar items with relative width pct (not readiness %)."""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if max_items is not None:
        ordered = ordered[:max_items]
    peak = max((count for _, count in ordered), default=0) or 1
    return [
        {
            "label": label,
            "count": count,
            "bar_pct": int(round(100 * count / peak)),
        }
        for label, count in ordered
    ]


def _top_unresolved_model_groups(
    prep_rows: list[dict[str, Any]], *, limit: int = 8
) -> list[dict[str, Any]]:
    """Count prep rows with any unresolved schema field, grouped by model group."""
    counts: dict[str, int] = {}
    for row in prep_rows:
        if not _has_unresolved_schema_field(row):
            continue
        group = (row.get("model_group") or row.get("ifc_class") or "Unknown").strip() or "Unknown"
        counts[group] = counts.get(group, 0) + 1
    return _count_bar_items(counts, max_items=limit)


def build_visual_summary(
    prep_rows: list[dict[str, Any]],
    unresolved_register: dict[str, int],
) -> dict[str, Any]:
    """Slice 2b dashboard blocks from prep rows + unresolved register only."""
    status_counts: dict[str, int] = {}
    basis_counts: dict[str, int] = {}
    for row in prep_rows:
        status = (row.get("review_status") or "Unknown").strip() or "Unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        if row.get("basis_unresolved") or not (row.get("quantity_basis") or "").strip():
            basis_label = "Unresolved"
        else:
            basis_label = str(row.get("quantity_basis")).strip()
        basis_counts[basis_label] = basis_counts.get(basis_label, 0) + 1

    source_selected = sum(1 for r in prep_rows if not r.get("missing_quantity_source"))
    source_missing = sum(1 for r in prep_rows if r.get("missing_quantity_source"))

    return {
        "helper_copy": (
            "Visual summary derived from the Generated Preparation Data Model and "
            "Unresolved Data Register. Not BOQ readiness, not 5D readiness, not QS "
            "readiness, and not measurement certification."
        ),
        "rows_by_status": _count_bar_items(status_counts),
        "quantity_basis_distribution": _count_bar_items(basis_counts),
        "source_mapping_completeness": _count_bar_items(
            {
                "Source selected": source_selected,
                "Source missing": source_missing,
            }
        ),
        "unresolved_by_field": _count_bar_items(
            {
                k: v
                for k, v in {
                    "Quantity basis rule": int(
                        unresolved_register.get("missing_quantity_basis_rule") or 0
                    ),
                    "Selected quantity source": int(
                        unresolved_register.get("missing_selected_quantity_source") or 0
                    ),
                    "Classification": int(unresolved_register.get("missing_classification") or 0),
                    "Package / BOQ mapping": int(
                        unresolved_register.get("missing_package_boq_mapping") or 0
                    ),
                    "Work package": int(unresolved_register.get("missing_work_package") or 0),
                }.items()
                if v > 0
                or k
                in {
                    "Quantity basis rule",
                    "Selected quantity source",
                }
            }
        ),
        "modify_handoff_status": _count_bar_items(
            {
                "Eligible for Modify handoff": int(
                    unresolved_register.get("eligible_for_modify_handoff")
                    or unresolved_register.get("ready_for_modify_handoff")
                    or 0
                ),
                "Not eligible for handoff": int(
                    unresolved_register.get("not_eligible_for_handoff")
                    or unresolved_register.get("not_ready_for_handoff")
                    or 0
                ),
            }
        ),
        "top_unresolved_model_groups": _top_unresolved_model_groups(prep_rows),
        "row_count": len(prep_rows),
    }


def build_preparation_insights(
    prep_rows: list[dict[str, Any]],
    unresolved_register: dict[str, int],
) -> list[dict[str, Any]]:
    """Deterministic operational insights from the preparation data model (not AI)."""
    missing_basis = int(unresolved_register.get("missing_quantity_basis_rule") or 0)
    missing_source = int(unresolved_register.get("missing_selected_quantity_source") or 0)
    missing_classification = int(unresolved_register.get("missing_classification") or 0)
    missing_package = int(unresolved_register.get("missing_package_boq_mapping") or 0)
    missing_work_package = int(unresolved_register.get("missing_work_package") or 0)
    eligible = int(
        unresolved_register.get("eligible_for_modify_handoff")
        or unresolved_register.get("ready_for_modify_handoff")
        or 0
    )

    top_groups = _top_unresolved_model_groups(prep_rows, limit=5)
    top_names = ", ".join(item["label"] for item in top_groups) if top_groups else "None"

    mapping_parts: list[str] = []
    if missing_classification:
        mapping_parts.append("Classification")
    if missing_package:
        mapping_parts.append("Package / BOQ Mapping")
    if missing_work_package:
        mapping_parts.append("Work Package")
    if mapping_parts:
        mapping_body = f"{', '.join(mapping_parts)} still unmapped for included schema fields."
    else:
        mapping_body = (
            "No included classification / package / work-package mapping gaps "
            "(those fields may be excluded from the schema)."
        )

    return [
        {
            "id": "measurement_rules_needed",
            "title": "Measurement rules needed",
            "count": missing_basis,
            "body": (
                f"{missing_basis} rows need a selected basis. Start with IfcWall and IfcSlab."
            ),
            "next": "Next: select a user-owned measurement basis for ambiguous groups.",
        },
        {
            "id": "selected_source_gaps",
            "title": "Selected source gaps",
            "count": missing_source,
            "body": (
                f"{missing_source} rows are missing the selected quantity source for the "
                "current rules."
            ),
            "next": "Next: map source after basis is chosen.",
        },
        {
            "id": "mapping_gaps",
            "title": "Mapping gaps",
            "count": missing_classification + missing_package + missing_work_package,
            "body": mapping_body,
            "next": "Next: complete included mapping fields or send eligible rows to Modify later.",
        },
        {
            "id": "modify_handoff_candidates",
            "title": "Modify handoff candidates",
            "count": eligible,
            "body": (
                f"{eligible} rows are eligible for Modify handoff because they have enough "
                "target context."
            ),
            "next": "Next: handoff stays disabled in this slice.",
        },
        {
            "id": "raw_quantity_warning",
            "title": "Raw quantities are evidence only",
            "count": None,
            "body": "Define source + basis before using totals.",
            "next": "Next: treat Raw Indexed Quantity Inventory as reference only.",
        },
        {
            "id": "top_unresolved_model_groups",
            "title": "Top unresolved groups",
            "count": top_groups[0]["count"] if top_groups else 0,
            "body": f"{top_names} drive most unresolved rows.",
            "next": "Next: prioritize user-defined rules for those groups.",
        },
    ]


def build_setup_summary(
    schema_fields: list[dict[str, Any]],
    basis_rules: list[dict[str, Any]],
    prep_rows: list[dict[str, Any]],
    unresolved_register: dict[str, int],
) -> dict[str, int]:
    """Compact Preparation Setup Summary counts (UI-only overview)."""
    return {
        "schema_fields": len(schema_fields),
        "schema_fields_included": sum(1 for f in schema_fields if f.get("included")),
        "required_fields": sum(
            1
            for f in schema_fields
            if f.get("required") == "Required" or f.get("required_label") == "Required"
        ),
        "measurement_rules": len(basis_rules),
        "generated_rows": len(prep_rows),
        "rows_with_unresolved_basis": int(
            unresolved_register.get("missing_quantity_basis_rule") or 0
        ),
        "rows_eligible_for_modify_handoff": int(
            unresolved_register.get("eligible_for_modify_handoff")
            or unresolved_register.get("ready_for_modify_handoff")
            or 0
        ),
    }


def build_preparation_ui(
    quantities: dict[str, Any],
    basis_overrides: Mapping[str, str] | None = None,
    schema_includes: Mapping[str, bool] | None = None,
    source_mappings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble Quantities preparation UI (Slice 2–3a + 3c-1 + 3c-2a)."""
    overrides = dict(basis_overrides or {})
    includes = dict(schema_includes or default_schema_includes())
    for key in LOCKED_SCHEMA_KEYS:
        includes[key] = True
    intents = dict(source_mappings or default_source_mapping_intents())
    for key in EDITABLE_SOURCE_MAPPING_KEYS:
        if intents.get(key) not in ALLOWED_SOURCE_MAPPING_VALUES:
            intents[key] = DEFAULT_EDITABLE_SOURCE_MAPPING
    schema_fields = build_schema_fields_ui(includes)
    basis_rules = user_defined_measurement_rules(overrides, quantities)
    prep_rows = (
        build_prep_rows(quantities, overrides, includes, intents)
        if quantities.get("has_ifc")
        else []
    )
    unresolved_register = build_unresolved_register(prep_rows)
    return {
        "schema_fields": schema_fields,
        "schema_includes": includes,
        "source_mapping_intents": intents,
        "show": {
            "level_storey": bool(includes.get("level_storey")),
            "zone": bool(includes.get("zone")),
            "type_name": bool(includes.get("type_name")),
            "ifc_class": True,
            "quantity_source": True,
            "quantity_basis": True,
            "unit_basis": True,
            "total_quantity": True,
            "classification_code": bool(includes.get("classification_code")),
            "package_boq_mapping": bool(includes.get("package_boq_mapping")),
            "work_package": bool(includes.get("work_package")),
            "review_status": True,
            "handoff_status": True,
        },
        "source_mappings": build_source_mappings_ui(intents),
        "source_mapping_session_note": (
            "Source mapping controls are session-only until saved as a preparation "
            "configuration draft. They define mapping intent for the generated "
            "preparation model. They do not create values, create Modify proposals, "
            "or send data to Modify in this slice. Exact property picking and "
            "free-text values remain out of scope."
        ),
        "source_mapping_option_note": (
            "Future Modify handoff: deferred mapping gap; counted as unresolved. "
            "Manual field: manual value expected later; counted as unresolved. "
            "Not mapped: intentionally blank; not counted as unresolved."
        ),
        "schema_session_note": (
            "Schema selection edits are session-only until saved as a preparation "
            "configuration draft. Generate rebuilds the preparation model from the "
            "selected fields and measurement rules. Required/core fields are locked. "
            "Saving stores preparation settings only, not generated quantity rows."
        ),
        "basis_rules": basis_rules,
        "basis_options": sorted(ALLOWED_BASIS_VALUES),
        "basis_overrides": overrides,
        "session_only_note": (
            "Current measurement-rule edits are session-only until saved as a "
            "preparation configuration draft. Refresh without parameters restores "
            "defaults unless a prep_config draft is loaded. Generate may continue "
            "as session GET params after a draft is loaded."
        ),
        "basis_rules_banner": (
            "No rule = unresolved. No selected basis = no measurement claim. "
            "Quantity Basis is user-selected — not a Castor recommendation. "
            "Raw IFC quantity values do not mean correct BOQ, 5D, or QS measurement. "
            "Saving stores preparation settings only — not generated quantity rows."
        ),
        "source_vs_basis_note": (
            "Quantity Source is the IFC/Qto property used when selected. "
            "Quantity Basis is the user-selected measurement method for the model group."
        ),
        "unit_basis_derivation_note": (
            "Unit Basis is derived from the selected Quantity Basis: "
            "NetVolume → model volume units; "
            "NetArea → model area units; "
            "Length → model length units; "
            "Count → count; "
            "Unresolved → —. "
            "Unit Basis is not manually edited in this slice. No SI normalization."
        ),
        "user_selected_basis_note": (
            "Selecting a Quantity Basis is a user choice for the current configuration. "
            "Castor does not recommend a measurement method. Large totals can appear "
            "when an indexed measure exists for the selected basis — that is not "
            "readiness or QS verification."
        ),
        "prep_rows": prep_rows,
        "prep_row_grain": (
            "type" if quantities.get("by_type_shown") and quantities.get("by_type") else "ifc_class"
        ),
        "unresolved_register": unresolved_register,
        # Back-compat alias for any leftover template references during Slice 2a.
        "missing_summary": unresolved_register,
        "column_config": schema_fields,
        "prep_helper_note": (
            "Rows are generated only from the selected schema, source mapping intents, "
            "and user-defined measurement rules. If quantity basis is unresolved, "
            "Total Quantity shows Unresolved — not a raw IFC number. "
            "Unit Basis is derived from selected Quantity Basis; it is not manually "
            "edited in this slice. Totals reflect the user-selected basis, not a "
            "Castor recommendation. Excluded schema fields are omitted from the table "
            "and are not counted as unresolved schema gaps. "
            "Mapping cells show intent hints only — no values are invented."
        ),
        "prep_unit_basis_note": (
            "Unit Basis is derived from selected Quantity Basis; "
            "it is not manually edited in this slice."
        ),
        "setup_summary": build_setup_summary(
            schema_fields, basis_rules, prep_rows, unresolved_register
        ),
        "visual_summary": build_visual_summary(prep_rows, unresolved_register),
        "preparation_insights": build_preparation_insights(prep_rows, unresolved_register),
    }
