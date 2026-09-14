# takeoff/services/quantity_table_layout.py
"""QTO-REVIEW-08 — visible column layout for the Quantities working table.

New/unsaved tables default to IFC Class / Name / Status / Actions.
Optional computed/assigned columns and IFC props are added via col_order.
Existing saved tables without col_order keep a legacy-compatible layout.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from takeoff.services.ifc_semantic_fields import (
    PROP_KEY_PREFIX,
    is_classref_column_key,
    is_spatial_column_key,
    parse_sem_cols,
    source_property_from_column_key,
)

logger = logging.getLogger(__name__)

COL_ORDER_PARAM = "col_order"
COL_ORDER_ADD = "col_order_add"
COL_ORDER_REMOVE = "col_order_remove"
COL_ORDER_MOVE = "col_order_move"  # "key:up" | "key:down"
LEGACY_LAYOUT_MARKER = "table_layout"  # set to "v2" once col_order is explicit

# Non-removable identity/utility columns (reorderable).
CORE_COLUMN_KEYS: tuple[str, ...] = ("ifc_class", "name", "status", "actions")
CORE_COLUMN_SET: frozenset[str] = frozenset(CORE_COLUMN_KEYS)

# Optional table fields (computed / assigned) — not IFC props.
OPTIONAL_TABLE_COLUMNS: tuple[dict[str, Any], ...] = (
    {
        "key": "quantity",
        "label": "Quantity",
        "group": "Table fields",
        "kind": "table",
        "description": "Resolved quantity for the row measurement settings",
    },
    {
        "key": "measurement",
        "label": "Measurement",
        "group": "Table fields",
        "kind": "table",
        "description": "Count / Length / Area / Volume from measurement settings",
    },
    {
        "key": "ifc_source",
        "label": "IFC Source",
        "group": "Table fields",
        "kind": "table",
        "description": "Indexed quantity source chosen in measurement settings",
    },
    {
        "key": "unit",
        "label": "Unit",
        "group": "Table fields",
        "kind": "table",
        "description": "Display unit after output-unit conversion",
    },
    {
        "key": "classification_code",
        "label": "Classification",
        "group": "Assigned values",
        "kind": "table",
        "description": "Assigned classification value",
    },
    {
        "key": "package_boq_mapping",
        "label": "Package",
        "group": "Assigned values",
        "kind": "table",
        "description": "Assigned package mapping",
    },
    {
        "key": "work_package",
        "label": "Work Package",
        "group": "Assigned values",
        "kind": "table",
        "description": "Assigned work package",
    },
)

OPTIONAL_TABLE_KEYS: frozenset[str] = frozenset(c["key"] for c in OPTIONAL_TABLE_COLUMNS)
OPTIONAL_BY_KEY: dict[str, dict[str, Any]] = {c["key"]: c for c in OPTIONAL_TABLE_COLUMNS}

CORE_META: dict[str, dict[str, Any]] = {
    "ifc_class": {
        "key": "ifc_class",
        "label": "IFC Class",
        "group": "Identity",
        "kind": "core",
        "removable": False,
    },
    "name": {
        "key": "name",
        "label": "Name",
        "group": "Identity",
        "kind": "core",
        "removable": False,
        "name_provenance": (
            "By Type grain: Castor indexed element_type.name "
            "(blank → '(unnamed type)'). Not entity Name / Type.Name property."
        ),
    },
    "status": {
        "key": "status",
        "label": "Status",
        "group": "Utility",
        "kind": "core",
        "removable": False,
    },
    "actions": {
        "key": "actions",
        "label": "Actions",
        "group": "Utility",
        "kind": "core",
        "removable": False,
    },
}

DEFAULT_NEW_ORDER: tuple[str, ...] = CORE_COLUMN_KEYS

# Pre-REVIEW-08 sticky layout for saved tables that lack col_order.
LEGACY_DEFAULT_ORDER: tuple[str, ...] = (
    "ifc_class",
    "name",
    "quantity",
    "measurement",
    "ifc_source",
    "unit",
    "status",
    "actions",
)


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_ifc_column_key(key: str) -> bool:
    """True for prop:/spatial:/classref: keys."""
    k = _str_val(key)
    return k.startswith(PROP_KEY_PREFIX) or is_spatial_column_key(k) or is_classref_column_key(k)


def is_known_layout_key(key: str) -> bool:
    k = _str_val(key)
    if not k:
        return False
    if k in CORE_COLUMN_SET or k in OPTIONAL_TABLE_KEYS:
        return True
    return is_ifc_column_key(k)


def parse_col_order_raw(raw: str | None) -> list[str]:
    """Parse a comma-separated col_order string; drop unknowns/dupes."""
    out: list[str] = []
    seen: set[str] = set()
    for part in _str_val(raw).split(","):
        key = part.strip()
        if not key or key in seen or not is_known_layout_key(key):
            continue
        seen.add(key)
        out.append(key)
    return out


def ensure_core_columns(order: Sequence[str]) -> list[str]:
    """Ensure non-removable cores are present; preserve caller order otherwise."""
    final: list[str] = []
    seen: set[str] = set()
    for k in order:
        key = _str_val(k)
        if not key or key in seen or not is_known_layout_key(key):
            continue
        seen.add(key)
        final.append(key)
    for core in CORE_COLUMN_KEYS:
        if core not in seen:
            final.append(core)
            seen.add(core)
    return final


def legacy_order_from_query(query: Mapping[str, Any]) -> list[str]:
    """Build a legacy-compatible layout when col_order was never saved."""
    order = list(LEGACY_DEFAULT_ORDER)
    # Mapping columns that were included via field_* defaults historically.
    for key in ("classification_code", "package_boq_mapping", "work_package"):
        field_param = f"field_{key}"
        raw = _str_val(query.get(field_param))
        # Default included when param absent (historical schema default).
        included = raw != "0" if raw else True
        if included and key not in order:
            # Insert before status/actions
            if "status" in order:
                idx = order.index("status")
                order.insert(idx, key)
            else:
                order.append(key)
    sem = parse_sem_cols(query)
    for key in sem:
        if key not in order:
            if "status" in order:
                order.insert(order.index("status"), key)
            else:
                order.append(key)
    return ensure_core_columns(order)


def parse_table_layout(query: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve visible column order and derived flags from GET-like query.

    New empty query → four-column default.
    Explicit ``col_order`` → that layout (cores forced present).
    Saved legacy (sem_cols / no col_order) → LEGACY sticky + sem_cols.
    """
    raw_order = _str_val(query.get(COL_ORDER_PARAM))
    # has_explicit unused after simplify — kept via LEGACY_LAYOUT_MARKER branch

    if raw_order:
        order = ensure_core_columns(parse_col_order_raw(raw_order))
        layout_mode = "explicit"
    elif _str_val(query.get(LEGACY_LAYOUT_MARKER)) == "v2":
        order = list(DEFAULT_NEW_ORDER)
        layout_mode = "explicit_empty"
    else:
        # Legacy reopen: saved table or prior sem_cols selection without col_order.
        has_sem = bool(_str_val(query.get("sem_cols")))
        has_editable = bool(_str_val(query.get("editable_table")))
        if has_sem or has_editable:
            order = legacy_order_from_query(query)
            layout_mode = "legacy"
        else:
            order = list(DEFAULT_NEW_ORDER)
            layout_mode = "default_new"

    # Apply one-shot mutations from picker.
    add = _str_val(query.get(COL_ORDER_ADD))
    if add and is_known_layout_key(add) and add not in order:
        # Insert optional columns before status/actions when possible.
        if "status" in order:
            order.insert(order.index("status"), add)
        else:
            order.append(add)
        order = ensure_core_columns(order)

    remove = _str_val(query.get(COL_ORDER_REMOVE))
    if remove and remove not in CORE_COLUMN_SET and remove in order:
        order = [k for k in order if k != remove]
        order = ensure_core_columns(order)

    move = _str_val(query.get(COL_ORDER_MOVE))
    if move and ":" in move:
        key, direction = move.rsplit(":", 1)
        if key in order:
            idx = order.index(key)
            if direction == "up" and idx > 0:
                order[idx - 1], order[idx] = order[idx], order[idx - 1]
            elif direction == "down" and idx < len(order) - 1:
                order[idx + 1], order[idx] = order[idx], order[idx + 1]

    visible = set(order)
    sem_cols = [k for k in order if is_ifc_column_key(k)]
    return {
        "order": order,
        "visible": visible,
        "sem_cols": sem_cols,
        "layout_mode": layout_mode,
        "col_order_param": ",".join(order),
        "table_layout": "v2",
        "show": {
            "ifc_class": True,
            "name": True,
            "status": True,
            "actions": True,
            "quantity": "quantity" in visible,
            "measurement": "measurement" in visible,
            "ifc_source": "ifc_source" in visible,
            "unit": "unit" in visible,
            "classification_code": "classification_code" in visible,
            "package_boq_mapping": "package_boq_mapping" in visible,
            "work_package": "work_package" in visible,
            "type_name": True,  # data always present; column key is "name"
        },
        "name_provenance": CORE_META["name"]["name_provenance"],
        "optional_table_columns": list(OPTIONAL_TABLE_COLUMNS),
        "core_columns": [CORE_META[k] for k in CORE_COLUMN_KEYS],
    }


def column_meta_for_key(
    key: str,
    *,
    selected_prop_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Readable metadata for one layout key."""
    if key in CORE_META:
        return dict(CORE_META[key])
    if key in OPTIONAL_BY_KEY:
        meta = dict(OPTIONAL_BY_KEY[key])
        meta["removable"] = True
        return meta
    prop_map = selected_prop_meta or {}
    if key in prop_map:
        src = dict(prop_map[key])
        return {
            "key": key,
            "label": src.get("label") or source_property_from_column_key(key) or key,
            "group": src.get("group") or src.get("source_context") or "IFC",
            "kind": "ifc",
            "removable": True,
            "source_property": src.get("source_property"),
            "source_context": src.get("source_context") or src.get("group"),
        }
    if is_ifc_column_key(key):
        src_prop = source_property_from_column_key(key)
        label = src_prop.split(".", 1)[-1] if src_prop and "." in src_prop else (src_prop or key)
        return {
            "key": key,
            "label": label,
            "group": src_prop.rsplit(".", 1)[0] if src_prop and "." in src_prop else "IFC",
            "kind": "ifc",
            "removable": True,
            "source_property": src_prop,
            "source_context": src_prop.rsplit(".", 1)[0] if src_prop and "." in src_prop else "",
        }
    return {"key": key, "label": key, "group": "Other", "kind": "unknown", "removable": True}


def build_layout_column_descriptors(
    layout: Mapping[str, Any],
    *,
    selected_prop_column_meta: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Ordered descriptors for template rendering."""
    prop_map = {str(d.get("key")): d for d in (selected_prop_column_meta or []) if d.get("key")}
    out: list[dict[str, Any]] = []
    order = list(layout.get("order") or [])
    for i, key in enumerate(order):
        meta = column_meta_for_key(key, selected_prop_meta=prop_map)
        meta["index"] = i
        meta["can_move_up"] = i > 0
        meta["can_move_down"] = i < len(order) - 1
        out.append(meta)
    return out


def apply_layout_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach table_layout panel; sync show flags and sem_cols for enrichment.

    Call **before** semantic enrichment when possible so ``sem_cols`` drives
    property columns. Safe to call again after enrichment to attach descriptors.
    """
    layout = parse_table_layout(query)
    # Merge any sem_cols_add still present into order (picker may use either param).
    add_legacy = _str_val(query.get("sem_cols_add"))
    if add_legacy and is_ifc_column_key(add_legacy) and add_legacy not in layout["order"]:
        order = list(layout["order"])
        if "status" in order:
            order.insert(order.index("status"), add_legacy)
        else:
            order.append(add_legacy)
        layout["order"] = ensure_core_columns(order)
        layout["visible"] = set(layout["order"])
        layout["sem_cols"] = [k for k in layout["order"] if is_ifc_column_key(k)]
        layout["col_order_param"] = ",".join(layout["order"])

    qty_prep["table_layout"] = layout
    # Column visibility must not disable assignment/schema eligibility.
    # Mapping fields stay driven by preparation schema includes; optional measure
    # columns and type_name visibility come from the layout.
    show = dict(qty_prep.get("show") or {})
    layout_show = layout.get("show") or {}
    for key in (
        "quantity",
        "measurement",
        "ifc_source",
        "unit",
        "type_name",
        "ifc_class",
        "status",
        "actions",
    ):
        if key in layout_show:
            show[key] = bool(layout_show[key])
    # Name column is always present in core layout; keep type_name data available.
    show["type_name"] = True
    qty_prep["show"] = show
    return layout


def inject_sem_cols_into_query(
    query: Mapping[str, Any],
    layout: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a shallow query dict with ``sem_cols`` aligned to layout order."""
    merged = dict(query) if not hasattr(query, "lists") else {}
    if hasattr(query, "lists"):
        for key, values in query.lists():  # type: ignore[attr-defined]
            cleaned = [str(v) for v in values if str(v).strip() != ""]
            if cleaned:
                merged[str(key)] = cleaned[0] if len(cleaned) == 1 else cleaned
    sem = list(layout.get("sem_cols") or [])
    if sem:
        merged["sem_cols"] = ",".join(sem)
    elif "sem_cols" in merged:
        # Keep empty explicit so enrichment doesn't invent selection.
        merged["sem_cols"] = ""
    merged[COL_ORDER_PARAM] = layout.get("col_order_param") or ",".join(
        layout.get("order") or DEFAULT_NEW_ORDER
    )
    merged[LEGACY_LAYOUT_MARKER] = "v2"
    return merged
