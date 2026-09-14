# takeoff/services/quantity_field_catalogue.py
"""QTO-REVIEW-08B — organise IFC catalogue fields into a real picker hierarchy.

Groups by indexed source context (Element / Type / Quantities / Spatial /
Classification). Does not invent properties; Category/Family stay excluded.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

FAMILY_ELEMENT = "Element properties"
FAMILY_TYPE = "Type properties"
FAMILY_QTO = "Quantities"
FAMILY_SPATIAL = "Spatial / location"
FAMILY_CLASSREF = "Classification"
FAMILY_TABLE = "Table fields"
FAMILY_OTHER = "Other indexed fields"

_EXCLUDED_LABELS = frozenset({"category", "family"})
_EXCLUDED_KEYS = frozenset(
    {
        "semantic_category",
        "semantic_family",
        "category",
        "family",
        "prop:Other.Category",
        "prop:Other.Family",
        "prop:Type.Other.Category",
        "prop:Type.Other.Family",
    }
)


def _str(value: Any) -> str:
    return str(value or "").strip()


def _is_excluded(field: Mapping[str, Any]) -> bool:
    key = _str(field.get("key"))
    label = _str(field.get("label")).lower()
    src = _str(field.get("source_property"))
    if key in _EXCLUDED_KEYS or label in _EXCLUDED_LABELS:
        return True
    if src in {"Other.Category", "Other.Family", "Type.Other.Category", "Type.Other.Family"}:
        return True
    return False


def classify_field_family(field: Mapping[str, Any]) -> tuple[str, str]:
    """Return (top_family, subgroup_name) for one catalogue field."""
    key = _str(field.get("key"))
    group = _str(field.get("group") or field.get("source_context"))
    src = _str(field.get("source_property"))
    kind = _str(field.get("kind") or field.get("source") or "")

    if key.startswith("spatial:") or group == "Model structure" or "spatial" in kind.lower():
        return FAMILY_SPATIAL, group or "Model location"
    if key.startswith("classref:") or "classification" in group.lower():
        return FAMILY_CLASSREF, group or "Classification"
    if kind == "table" or key in {
        "quantity",
        "measurement",
        "ifc_source",
        "unit",
        "classification_code",
        "package_boq_mapping",
        "work_package",
        "ifc_class",
        "type_name",
        "name",
    }:
        return FAMILY_TABLE, group or "Derived / assigned"

    if src.startswith("Qto_") or group == "Quantity sets" or key.startswith("prop:Qto_"):
        qto = src.split(".", 1)[0] if src else (group or "Qto")
        return FAMILY_QTO, qto

    if src.startswith("Type.") or key.startswith("prop:Type."):
        # Type.Identity Data.Keynote → Identity Data
        rest = src[5:] if src.startswith("Type.") else src
        pset = rest.split(".", 1)[0] if "." in rest else (rest or group or "Type")
        return FAMILY_TYPE, pset

    if key.startswith("prop:") or src:
        pset = src.split(".", 1)[0] if "." in src else (group or "Properties")
        return FAMILY_ELEMENT, pset

    return FAMILY_OTHER, group or "Other"


def build_picker_hierarchy(
    fields: Sequence[Mapping[str, Any]],
    *,
    exclude_keys: Sequence[str] | None = None,
    include_table_fields: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build nested family → subgroup → fields for the shared picker UI."""
    excluded = {_str(k) for k in (exclude_keys or []) if _str(k)}
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for raw in include_table_fields or []:
        if not isinstance(raw, Mapping):
            continue
        entry = dict(raw)
        key = _str(entry.get("key"))
        if not key or key in excluded or _is_excluded(entry):
            continue
        family, subgroup = FAMILY_TABLE, _str(entry.get("group")) or "Derived / assigned"
        buckets[family][subgroup].append(entry)

    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        entry = dict(raw)
        key = _str(entry.get("key"))
        if not key or key in excluded or _is_excluded(entry):
            continue
        family, subgroup = classify_field_family(entry)
        buckets[family][subgroup].append(entry)

    family_order = (
        FAMILY_TABLE,
        FAMILY_ELEMENT,
        FAMILY_TYPE,
        FAMILY_QTO,
        FAMILY_SPATIAL,
        FAMILY_CLASSREF,
        FAMILY_OTHER,
    )
    out: list[dict[str, Any]] = []
    for family in family_order:
        subgroups = buckets.get(family) or {}
        if not subgroups:
            continue
        group_list: list[dict[str, Any]] = []
        for name in sorted(subgroups.keys(), key=lambda s: s.lower()):
            items = sorted(
                subgroups[name],
                key=lambda f: (_str(f.get("label")).lower(), _str(f.get("key"))),
            )
            group_list.append(
                {
                    "name": name,
                    "fields": items,
                    "count": len(items),
                }
            )
        out.append(
            {
                "family": family,
                "groups": group_list,
                "count": sum(g["count"] for g in group_list),
            }
        )
    return out


def samples_json_by_key(fields: Sequence[Mapping[str, Any]]) -> str:
    """Compact JSON map field_key → sample_values for the value combobox."""
    payload: dict[str, list[str]] = {}
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        key = _str(raw.get("key"))
        if not key:
            continue
        samples = [str(v) for v in (raw.get("sample_values") or []) if str(v).strip() != ""]
        payload[key] = samples
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def field_meta_json_by_key(fields: Sequence[Mapping[str, Any]]) -> str:
    """Compact JSON map field_key → operators/value_type/unit/scope for filter UI."""
    payload: dict[str, dict[str, Any]] = {}
    for raw in fields:
        if not isinstance(raw, Mapping):
            continue
        key = _str(raw.get("key"))
        if not key:
            continue
        ops = raw.get("operators") or []
        op_keys = []
        for op in ops:
            if isinstance(op, Mapping) and op.get("key"):
                op_keys.append(str(op["key"]))
            elif isinstance(op, str):
                op_keys.append(op)
        payload[key] = {
            "value_type": _str(raw.get("value_type")) or "text",
            "unit_label": _str(raw.get("unit_label")),
            "operators": op_keys,
            "scope_note": _str(raw.get("scope_note")),
            "partial_in_scope": bool(raw.get("partial_in_scope")),
            "label": _str(raw.get("label")) or key,
            "source_context": _str(raw.get("source_context") or raw.get("group")),
            "samples_capped": True,
            "sample_cap": len(list(raw.get("sample_values") or [])),
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
