# takeoff/services/quantity_entity_filter.py
"""QTO-DYNAMIC-07 — entity-level semantic filter predicates (before By Type agg).

Evaluates IFC-backed fields against indexed entity properties / spatial names.
Prep-native derived fields (measurement/source) stay row-level elsewhere.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

OP_EQ = "eq"
OP_NE = "ne"
OP_CONTAINS = "contains"
OP_GT = "gt"
OP_LT = "lt"
OP_GTE = "gte"
OP_LTE = "lte"
OP_IS_MISSING = "is_missing"
OP_IS_PRESENT = "is_present"

TEXT_OPS: tuple[str, ...] = (OP_EQ, OP_NE, OP_CONTAINS, OP_IS_MISSING, OP_IS_PRESENT)
NUMERIC_OPS: tuple[str, ...] = (
    OP_EQ,
    OP_NE,
    OP_GT,
    OP_LT,
    OP_GTE,
    OP_LTE,
    OP_IS_MISSING,
    OP_IS_PRESENT,
)
BOOLEAN_OPS: tuple[str, ...] = (OP_EQ, OP_IS_MISSING, OP_IS_PRESENT)

OP_LABELS: dict[str, str] = {
    OP_EQ: "equals",
    OP_NE: "does not equal",
    OP_CONTAINS: "contains",
    OP_GT: "greater than",
    OP_LT: "less than",
    OP_GTE: "greater than or equal",
    OP_LTE: "less than or equal",
    OP_IS_MISSING: "is missing",
    OP_IS_PRESENT: "is present",
}

# Fields evaluated on entities before By Type aggregation.
ENTITY_LEVEL_FIELD_PREFIXES: tuple[str, ...] = ("prop:", "spatial:", "classref:")
ENTITY_LEVEL_NATIVE_KEYS: frozenset[str] = frozenset({"ifc_class", "type_name"})

SEMANTIC_CLASSES_PARAM = "semantic_classes"

_COMPLEX_UNSUPPORTED = object()


def parse_selected_classes(query: Mapping[str, Any] | None) -> list[str]:
    """Parse multi-select IFC class scope. Empty list means all classes."""
    if query is None:
        return []
    raw_list: list[str] = []
    if hasattr(query, "getlist"):
        raw_list = [str(v) for v in query.getlist(SEMANTIC_CLASSES_PARAM)]  # type: ignore[attr-defined]
    raw = query.get(SEMANTIC_CLASSES_PARAM) if hasattr(query, "get") else None
    if raw is None and not raw_list:
        return []
    if isinstance(raw, (list, tuple)):
        raw_list.extend(str(v) for v in raw)
    elif raw is not None and str(raw).strip():
        raw_list.append(str(raw))
    out: list[str] = []
    seen: set[str] = set()
    for part in raw_list:
        for piece in str(part).split(","):
            cls = piece.strip()
            if not cls or cls in seen:
                continue
            seen.add(cls)
            out.append(cls)
    return out


def is_entity_level_field(field_key: str) -> bool:
    """True when the filter must run against entities before aggregation."""
    key = str(field_key or "").strip()
    if key in ENTITY_LEVEL_NATIVE_KEYS:
        return True
    return any(key.startswith(p) for p in ENTITY_LEVEL_FIELD_PREFIXES)


def operators_for_value_type(value_type: str) -> tuple[str, ...]:
    """Return operators compatible with a catalogue value_type."""
    vt = str(value_type or "text").strip().lower()
    if vt == "numeric":
        return NUMERIC_OPS
    if vt == "boolean":
        return BOOLEAN_OPS
    if vt == "unsupported":
        return (OP_IS_MISSING, OP_IS_PRESENT)
    return TEXT_OPS


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def coerce_property_text(raw: Any) -> tuple[str | object, bool]:
    """Return (display_or_sentinel, is_complex_unsupported).

    Numeric zero stays ``\"0\"``. Missing stays empty. Complex values are not
    stringified into fake filterable text.
    """
    if raw is None:
        return "", False
    if isinstance(raw, bool):
        return ("true" if raw else "false"), False
    if isinstance(raw, (dict, list, tuple, set)):
        return _COMPLEX_UNSUPPORTED, True
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Preserve 0; avoid scientific noise for ordinary floats.
        if float(raw) == int(raw) and abs(raw) < 1e15:
            return str(int(raw)), False
        return str(raw), False
    text = str(raw).strip()
    return text, False


def try_parse_number(text: str) -> float | None:
    """Parse a numeric filter threshold; reject empty/non-numeric."""
    raw = str(text or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def compare_values(
    *,
    actual: Any,
    op: str,
    expected: str,
    value_type: str,
) -> bool:
    """Evaluate one operator against a raw entity/cell value."""
    op_key = str(op or OP_EQ).strip() or OP_EQ
    text, is_complex = coerce_property_text(actual)
    if is_complex:
        if op_key == OP_IS_MISSING:
            return True
        if op_key == OP_IS_PRESENT:
            return False
        return False

    present = text != ""
    if op_key == OP_IS_MISSING:
        return not present
    if op_key == OP_IS_PRESENT:
        return present

    vt = str(value_type or "text").strip().lower()
    if vt == "numeric":
        actual_num = try_parse_number(text) if present else None
        expected_num = try_parse_number(expected)
        if op_key in {OP_EQ, OP_NE, OP_GT, OP_LT, OP_GTE, OP_LTE}:
            if actual_num is None or expected_num is None:
                return False
            if op_key == OP_EQ:
                return actual_num == expected_num
            if op_key == OP_NE:
                return actual_num != expected_num
            if op_key == OP_GT:
                return actual_num > expected_num
            if op_key == OP_LT:
                return actual_num < expected_num
            if op_key == OP_GTE:
                return actual_num >= expected_num
            if op_key == OP_LTE:
                return actual_num <= expected_num
        return False

    if vt == "boolean":
        left = text.lower()
        right = _str_val(expected).lower()
        if op_key == OP_EQ:
            return left == right
        return False

    left = text
    right = _str_val(expected)
    if op_key == OP_EQ:
        return left == right
    if op_key == OP_NE:
        return left != right
    if op_key == OP_CONTAINS:
        return bool(right) and right.lower() in left.lower()
    return False


def resolve_entity_field_value(
    *,
    field_key: str,
    props: Mapping[str, Any] | None,
    ifc_class: str,
    type_name: str,
    storey_name: str = "",
    container_name: str = "",
) -> Any:
    """Resolve the raw value used for an entity-level filter field."""
    from takeoff.services.ifc_semantic_fields import (
        SPATIAL_CONTAINER_KEY,
        SPATIAL_STOREY_KEY,
        classref_source_property,
        is_classref_column_key,
        is_spatial_column_key,
        source_property_from_column_key,
    )

    key = str(field_key or "").strip()
    props = props if isinstance(props, Mapping) else {}
    if key == "ifc_class":
        return ifc_class
    if key == "type_name":
        return type_name
    if key == SPATIAL_STOREY_KEY or (is_spatial_column_key(key) and key == SPATIAL_STOREY_KEY):
        return storey_name
    if key == SPATIAL_CONTAINER_KEY:
        return container_name
    if is_classref_column_key(key):
        return props.get(classref_source_property(key))
    if key.startswith("prop:"):
        src = source_property_from_column_key(key)
        return props.get(src) if src else None
    return None


def build_entity_predicate(
    *,
    field_key: str,
    op: str,
    value: str,
    value_type: str = "text",
) -> Callable[..., bool] | None:
    """Return a predicate ``(props, ifc_class, type_name, storey, container) -> bool``.

    Returns None when the field is not entity-level or the condition is incomplete
    (except missing/present ops).
    """
    key = str(field_key or "").strip()
    if not key or not is_entity_level_field(key):
        return None
    op_key = str(op or OP_EQ).strip() or OP_EQ
    allowed = operators_for_value_type(value_type)
    if op_key not in allowed:
        return None
    needs_value = op_key not in {OP_IS_MISSING, OP_IS_PRESENT}
    if needs_value and not str(value or "").strip():
        return None
    if needs_value and value_type == "numeric" and try_parse_number(value) is None:
        return None

    def _pred(
        props: Mapping[str, Any] | None,
        ifc_class: str,
        type_name: str,
        storey_name: str = "",
        container_name: str = "",
    ) -> bool:
        actual = resolve_entity_field_value(
            field_key=key,
            props=props,
            ifc_class=ifc_class,
            type_name=type_name,
            storey_name=storey_name,
            container_name=container_name,
        )
        return compare_values(actual=actual, op=op_key, expected=value, value_type=value_type)

    return _pred


def compose_entity_predicate(
    *,
    selected_classes: Sequence[str] | None = None,
    field_predicate: Callable[..., bool] | None = None,
) -> Callable[..., bool] | None:
    """Combine class-scope and optional property predicate (AND).

    Empty ``selected_classes`` means all classes (no class restriction).
    """
    classes = [str(c).strip() for c in (selected_classes or []) if str(c).strip()]
    class_set = set(classes)

    if not class_set and field_predicate is None:
        return None

    def _pred(
        props: Mapping[str, Any] | None,
        ifc_class: str,
        type_name: str,
        storey_name: str = "",
        container_name: str = "",
    ) -> bool:
        if class_set and str(ifc_class or "") not in class_set:
            return False
        if field_predicate is None:
            return True
        return field_predicate(props, ifc_class, type_name, storey_name, container_name)

    return _pred


def condition_chip_label(
    *,
    field_label: str,
    op: str,
    value: str,
    unit_label: str = "",
) -> str:
    """Human-readable active filter chip."""
    op_label = OP_LABELS.get(str(op or ""), str(op or ""))
    if op in {OP_IS_MISSING, OP_IS_PRESENT}:
        return f"{field_label} {op_label}"
    unit = f" {unit_label}" if unit_label else ""
    return f"{field_label} {op_label} {value}{unit}".strip()


_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def infer_value_type(sample_values: Sequence[str], *, source_property: str = "") -> str:
    """Infer catalogue value_type from samples and optional Qto property path."""
    src = str(source_property or "")
    if src.startswith("Qto_") or ".Qto_" in src:
        # Quantity measures are numeric when present.
        return "numeric"
    samples = [str(s).strip() for s in sample_values if str(s).strip()]
    if not samples:
        return "text"
    lower = {s.lower() for s in samples}
    if lower <= {"true", "false", "0", "1", "yes", "no"}:
        if lower <= {"true", "false"} or lower <= {"yes", "no"}:
            return "boolean"
    numeric_hits = 0
    for s in samples:
        if _NUMERIC_RE.match(s.replace(",", "")):
            numeric_hits += 1
    if samples and numeric_hits == len(samples):
        return "numeric"
    return "text"
