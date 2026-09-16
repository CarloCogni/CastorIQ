# takeoff/services/quantity_column_calc.py
"""QTO-COLUMNS-10 — per-column numeric calculations on hierarchy parents.

Sorting, grouping and calculation are separate. Calculations always use
matching leaf instance values after IFC-backed filtering — never visible DOM
rows or loaded pages alone.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from takeoff.services.ifc_semantic_fields import (
    source_property_from_column_key,
)
from takeoff.services.model_quantities import RESOLVER_INVENTORY_KEYS

logger = logging.getLogger(__name__)

COL_CALC_PARAM = "col_calc"
CALC_NONE = "none"
CALC_SUM = "sum"
CALC_AVG = "avg"
CALC_MIN = "min"
CALC_MAX = "max"

NUMERIC_OPS: tuple[str, ...] = (CALC_NONE, CALC_SUM, CALC_AVG, CALC_MIN, CALC_MAX)
OPS_WITHOUT_NONE: tuple[str, ...] = (CALC_SUM, CALC_AVG, CALC_MIN, CALC_MAX)


def _str(value: Any) -> str:
    return str(value or "").strip()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    text = _str(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def parse_col_calc(query: Mapping[str, Any] | None) -> dict[str, str]:
    """Parse ``col_calc=key:op,key2:op2`` into ``{key: op}``."""
    raw = ""
    if query is not None:
        raw = _str(query.get(COL_CALC_PARAM) if hasattr(query, "get") else "")
        if not raw and hasattr(query, "getlist"):
            parts = [str(v) for v in query.getlist(COL_CALC_PARAM) if str(v).strip()]  # type: ignore[attr-defined]
            raw = ",".join(parts)
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        key, op = piece.rsplit(":", 1)
        key_s = key.strip()
        op_s = op.strip().lower()
        if not key_s or op_s not in NUMERIC_OPS:
            continue
        out[key_s] = op_s
    return out


def col_calc_param(mapping: Mapping[str, str]) -> str:
    """Serialize calculation map for URL / saved query.

    Includes explicit ``none`` so users can override default Sum on Qto columns.
    Callers should pass overrides only (not every default) when possible.
    """
    parts = [f"{k}:{v}" for k, v in sorted(mapping.items()) if v]
    return ",".join(parts)


def is_qto_additive_source(source_property: str) -> bool:
    """True when the property leaf is a recognized inventory Qto measure."""
    src = _str(source_property)
    if not src:
        return False
    leaf = src.rsplit(".", 1)[-1]
    return leaf in set(RESOLVER_INVENTORY_KEYS)


def eligible_calc_ops(
    *,
    column_key: str,
    value_type: str = "",
    source_property: str = "",
) -> list[str]:
    """Return operations supported for a column (including none)."""
    key = _str(column_key)
    if key in {"quantity"}:
        # Measurement engine owns Quantity subtotals — no alternate ops here.
        return [CALC_NONE]
    if key in {
        "ifc_class",
        "name",
        "status",
        "actions",
        "measurement",
        "ifc_source",
        "unit",
        "classification_code",
        "package_boq_mapping",
        "work_package",
    }:
        return [CALC_NONE]
    src = _str(source_property) or source_property_from_column_key(key)
    vt = _str(value_type).lower()
    if is_qto_additive_source(src) or vt in {
        "numeric",
        "number",
        "measure",
        "real",
        "integer",
        "float",
    }:
        return list(NUMERIC_OPS)
    return [CALC_NONE]


def default_calc_op(
    *,
    column_key: str,
    value_type: str = "",
    source_property: str = "",
) -> str:
    """Default calculation when the user has not set one."""
    key = _str(column_key)
    if key == "quantity":
        return CALC_SUM  # display-only marker; engine already subtotals
    src = _str(source_property) or source_property_from_column_key(key)
    if is_qto_additive_source(src):
        return CALC_SUM
    return CALC_NONE


def resolve_calc_ops(
    *,
    column_keys: Sequence[str],
    explicit: Mapping[str, str] | None = None,
    field_meta_by_key: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Merge explicit ops with defaults for selected columns."""
    explicit = dict(explicit or {})
    meta = field_meta_by_key or {}
    out: dict[str, str] = {}
    for key in column_keys:
        k = _str(key)
        if not k:
            continue
        info = meta.get(k) or {}
        src = _str(info.get("source_property")) or source_property_from_column_key(k)
        vt = _str(info.get("value_type"))
        allowed = set(eligible_calc_ops(column_key=k, value_type=vt, source_property=src))
        if k in explicit and explicit[k] in allowed:
            out[k] = explicit[k]
        else:
            out[k] = default_calc_op(column_key=k, value_type=vt, source_property=src)
    return out


def _leaf_numeric(row: Mapping[str, Any], column_key: str) -> tuple[Decimal | None, str]:
    """Return (value, status) for one instance leaf cell."""
    by_key = (
        row.get("prop_column_by_key") if isinstance(row.get("prop_column_by_key"), dict) else {}
    )
    cell = by_key.get(column_key) if isinstance(by_key, dict) else None
    if isinstance(cell, dict):
        status = _str(cell.get("status")) or "missing"
        if status in {"missing", "unsupported", "mixed"}:
            return None, status
        num = cell.get("numeric_value")
        dec = _to_decimal(num)
        if dec is None:
            dec = _to_decimal(cell.get("display") or cell.get("value"))
        return dec, status if dec is not None else "invalid"
    # Fallback: inventory for additive Qto
    src = source_property_from_column_key(column_key)
    leaf = src.rsplit(".", 1)[-1] if src else ""
    if leaf in set(RESOLVER_INVENTORY_KEYS):
        inv = row.get("measure_inventory") if isinstance(row.get("measure_inventory"), dict) else {}
        if leaf in inv:
            return _to_decimal(inv.get(leaf)), "ok"
    return None, "missing"


def _format_decimal(value: Decimal) -> str:
    """Display rounding only — computation stays full precision."""
    q = value.quantize(Decimal("0.01")) if abs(value) >= Decimal("0.01") or value == 0 else value
    text = format(q, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def compute_parent_none(
    leaf_displays: Sequence[str | None],
) -> dict[str, Any]:
    """No-calculation parent: common value, Multiple values, or missing."""
    usable = [
        _str(v) for v in leaf_displays if _str(v) and _str(v) not in {"—", "-", "Multiple values"}
    ]
    if not usable:
        return {
            "status": "missing",
            "display": "—",
            "coverage": "0/0",
            "usable": 0,
            "total": len(leaf_displays),
            "op": CALC_NONE,
            "title": "No usable values among matching instances",
        }
    distinct = sorted(set(usable))
    if len(distinct) == 1:
        return {
            "status": "single",
            "display": distinct[0],
            "value": distinct[0],
            "coverage": f"{len(usable)}/{len(leaf_displays)}",
            "usable": len(usable),
            "total": len(leaf_displays),
            "op": CALC_NONE,
            "title": "Common value across matching instances",
        }
    return {
        "status": "mixed",
        "display": "Multiple values",
        "value": "Multiple values",
        "coverage": f"{len(usable)}/{len(leaf_displays)}",
        "usable": len(usable),
        "total": len(leaf_displays),
        "op": CALC_NONE,
        "title": "Multiple values across matching instances",
        "detail_values": distinct[:12],
    }


def compute_parent_calc(
    values: Sequence[Decimal | None],
    *,
    op: str,
    total_leaves: int,
) -> dict[str, Any]:
    """Compute Sum/Avg/Min/Max over usable leaf numbers."""
    usable = [v for v in values if v is not None]
    n = len(usable)
    total_n = int(total_leaves)
    if op == CALC_NONE:
        return {
            "status": "none",
            "display": "",
            "coverage": f"0/{total_n}" if total_n else "0/0",
            "usable": 0,
            "total": total_n,
        }
    if not usable:
        return {
            "status": "unavailable",
            "display": "—",
            "coverage": f"0/{total_n}" if total_n else "0/0",
            "usable": 0,
            "total": total_n,
            "title": "No usable numeric values among matching instances",
        }
    if op == CALC_SUM:
        result = sum(usable, Decimal("0"))
    elif op == CALC_AVG:
        result = sum(usable, Decimal("0")) / Decimal(n)
    elif op == CALC_MIN:
        result = min(usable)
    elif op == CALC_MAX:
        result = max(usable)
    else:
        return {
            "status": "unavailable",
            "display": "—",
            "coverage": f"{n}/{total_n}",
            "usable": n,
            "total": total_n,
        }
    shown = _format_decimal(result)
    partial = n < total_n
    status = "partial" if partial else "ok"
    display = f"{shown} ({n} of {total_n} values)" if partial else shown
    return {
        "status": status,
        "display": display,
        "value": result,
        "coverage": f"{n}/{total_n}",
        "usable": n,
        "total": total_n,
        "op": op,
        "title": f"{op} across {n} of {total_n} matching instances",
    }


def apply_column_calculations(
    qty_prep: MutableMapping[str, Any],
    *,
    col_calc: Mapping[str, str] | None = None,
) -> None:
    """Overwrite Class/Type cells for columns with calculation ops.

    Instance rows keep leaf values. Quantity column is left to the measurement engine.
    Additive inventory Qto with default sum may already be filled by
    ``apply_additive_qto_hierarchy_cells``; explicit ops still recompute from leaves.

    COLUMNS-10B: every op — including No calculation — scopes leaves by
    ``parent_key`` / ``class_key`` identity, never by display name.
    """
    ops = dict(col_calc or {})
    layout = qty_prep.get("table_layout") or {}
    sem_cols = list((layout.get("sem_cols") if isinstance(layout, dict) else None) or [])
    if not sem_cols:
        sem = qty_prep.get("semantic_filters") or {}
        sem_cols = list((sem.get("selected_prop_columns") if isinstance(sem, dict) else None) or [])
    meta_list = []
    sem = qty_prep.get("semantic_filters") or {}
    if isinstance(sem, dict):
        meta_list = list(sem.get("selected_prop_column_meta") or [])
    meta_by_key = {str(m.get("key")): m for m in meta_list if isinstance(m, Mapping)}
    resolved = resolve_calc_ops(
        column_keys=sem_cols,
        explicit=ops,
        field_meta_by_key=meta_by_key,
    )
    defaults = resolve_calc_ops(
        column_keys=sem_cols,
        explicit={},
        field_meta_by_key=meta_by_key,
    )
    # Persist only overrides (including explicit none over default sum).
    overrides = {k: v for k, v in resolved.items() if v != defaults.get(k)}
    qty_prep["column_calculations"] = resolved
    qty_prep["col_calc_ops"] = resolved
    qty_prep["col_calc_param"] = col_calc_param(overrides)
    # Apply numeric ops + none (identity-correct parent display).
    active = {k: v for k, v in resolved.items() if k != "quantity"}
    if not active:
        return

    export_rows = [
        r
        for r in (qty_prep.get("prep_rows_export") or [])
        if isinstance(r, dict) and not r.get("is_load_more") and r.get("level") == "instance"
    ]
    if not export_rows:
        export_rows = [
            r
            for r in (qty_prep.get("prep_rows") or [])
            if isinstance(r, dict) and not r.get("is_load_more") and r.get("level") == "instance"
        ]

    def _leaves_for_parent(parent: Mapping[str, Any]) -> list[dict[str, Any]]:
        level = _str(parent.get("level"))
        node_key = _str(parent.get("node_key"))
        ifc_class = _str(parent.get("ifc_class"))
        if level == "class":
            return [
                r
                for r in export_rows
                if _str(r.get("class_key")) == node_key
                or (not _str(r.get("class_key")) and _str(r.get("ifc_class")) == ifc_class)
            ]
        if level == "type":
            # Exact type node only — never match by shared display name.
            return [r for r in export_rows if _str(r.get("parent_key")) == node_key]
        return []

    def _leaf_display(leaf: Mapping[str, Any], column_key: str) -> str | None:
        by_key = (
            leaf.get("prop_column_by_key")
            if isinstance(leaf.get("prop_column_by_key"), dict)
            else {}
        )
        cell = by_key.get(column_key) if isinstance(by_key, dict) else None
        if isinstance(cell, dict):
            disp = _str(cell.get("display"))
            if disp and disp not in {"—", "-"}:
                return disp
            return None
        raw = leaf.get(column_key)
        text = _str(raw)
        return text or None

    def _apply_to_parent(row: MutableMapping[str, Any]) -> None:
        leaves = _leaves_for_parent(row)
        by_key = row.get("prop_column_by_key")
        if not isinstance(by_key, dict):
            by_key = {}
            row["prop_column_by_key"] = by_key
        for col_key, op in active.items():
            src = source_property_from_column_key(col_key)
            allowed = set(
                eligible_calc_ops(
                    column_key=col_key,
                    value_type=_str((meta_by_key.get(col_key) or {}).get("value_type")),
                    source_property=src,
                )
            )
            if op not in allowed:
                continue
            # Additive Qto Sum is already correct from measure_inventory.
            if is_qto_additive_source(src) and op == CALC_SUM:
                continue
            if op == CALC_NONE:
                displays = [_leaf_display(leaf, col_key) for leaf in leaves]
                result = compute_parent_none(displays)
                cell = dict(by_key.get(col_key) or {"key": col_key})
                cell.update(
                    {
                        "key": col_key,
                        "display": result["display"],
                        "status": result["status"],
                        "coverage": result.get("coverage"),
                        "calc_op": CALC_NONE,
                        "title": result.get("title") or result["display"],
                        "numeric_value": None,
                        "detail_values": list(result.get("detail_values") or []),
                    }
                )
                by_key[col_key] = cell
                row[col_key] = result["display"]
                props = row.get("prop_columns")
                if isinstance(props, dict):
                    props[col_key] = {
                        "status": result["status"],
                        "display": result["display"],
                        "value": result["display"],
                        "distinct_count": len(result.get("detail_values") or []),
                        "detail_values": list(result.get("detail_values") or []),
                        "numeric_value": None,
                    }
                continue
            nums: list[Decimal | None] = []
            for leaf in leaves:
                val, _status = _leaf_numeric(leaf, col_key)
                if val is None and is_qto_additive_source(src):
                    leaf_name = src.rsplit(".", 1)[-1] if src else ""
                    inv = (
                        leaf.get("measure_inventory")
                        if isinstance(leaf.get("measure_inventory"), dict)
                        else {}
                    )
                    val = _to_decimal(inv.get(leaf_name)) if leaf_name else None
                nums.append(val)
            result = compute_parent_calc(nums, op=op, total_leaves=len(leaves))
            cell = dict(by_key.get(col_key) or {"key": col_key})
            cell.update(
                {
                    "key": col_key,
                    "display": result["display"],
                    "status": result["status"],
                    "coverage": result.get("coverage"),
                    "calc_op": op,
                    "title": result.get("title") or result["display"],
                    "numeric_value": float(result["value"])
                    if isinstance(result.get("value"), Decimal)
                    else None,
                }
            )
            by_key[col_key] = cell
            row[col_key] = result["display"]
            props = row.get("prop_columns")
            if isinstance(props, dict):
                props[col_key] = {
                    "status": result["status"],
                    "display": result["display"],
                    "value": result["display"],
                    "distinct_count": 0,
                    "detail_values": [],
                    "numeric_value": cell.get("numeric_value"),
                }
        row["prop_column_by_key"] = by_key

    for row in qty_prep.get("prep_rows") or []:
        if not isinstance(row, dict) or not row.get("hierarchy"):
            continue
        if row.get("is_load_more") or _str(row.get("level")) == "instance":
            continue
        _apply_to_parent(row)

    for row in qty_prep.get("prep_rows_export") or []:
        if not isinstance(row, dict) or not row.get("hierarchy"):
            continue
        if row.get("is_load_more") or _str(row.get("level")) == "instance":
            continue
        _apply_to_parent(row)

    logger.debug("column calculations applied ops=%s", sorted(active.items()))


CALC_LABELS: dict[str, str] = {
    CALC_NONE: "No calculation",
    CALC_SUM: "Sum",
    CALC_AVG: "Average",
    CALC_MIN: "Minimum",
    CALC_MAX: "Maximum",
}


def _query_pairs_without(query: Mapping[str, Any], drop: set[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if hasattr(query, "lists"):
        for key, values in query.lists():  # type: ignore[attr-defined]
            if key in drop:
                continue
            for value in values:
                pairs.append((str(key), str(value)))
        return pairs
    for key, value in query.items():
        if key in drop:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                pairs.append((str(key), str(item)))
        else:
            pairs.append((str(key), str(value)))
    return pairs


def attach_column_header_menus(
    qty_prep: MutableMapping[str, Any],
    *,
    query: Mapping[str, Any],
) -> None:
    """Attach compact header-menu descriptors onto ``table_columns`` (COLUMNS-10B)."""
    from urllib.parse import urlencode

    calc_ops = qty_prep.get("col_calc_ops") or qty_prep.get("column_calculations") or {}
    if not isinstance(calc_ops, Mapping):
        calc_ops = {}
    hierarchy_sort = qty_prep.get("hierarchy_sort") or {}
    sort_key = _str(hierarchy_sort.get("key"))
    sort_dir = _str(hierarchy_sort.get("dir")) or "asc"
    meta_list = []
    sem = qty_prep.get("semantic_filters") or {}
    if isinstance(sem, dict):
        meta_list = list(sem.get("selected_prop_column_meta") or [])
    meta_by_key = {str(m.get("key")): m for m in meta_list if isinstance(m, Mapping)}

    for col in qty_prep.get("table_columns") or []:
        if not isinstance(col, MutableMapping):
            continue
        key = _str(col.get("key"))
        info = meta_by_key.get(key) or {}
        src = _str(info.get("source_property")) or source_property_from_column_key(key)
        vt = _str(info.get("value_type") or col.get("value_type"))
        allowed = eligible_calc_ops(column_key=key, value_type=vt, source_property=src)
        current_op = _str(calc_ops.get(key)) or default_calc_op(
            column_key=key, value_type=vt, source_property=src
        )
        calc_eligible = len(allowed) > 1
        col["calc_eligible"] = calc_eligible
        col["calc_op"] = current_op
        col["calc_label"] = CALC_LABELS.get(current_op, current_op)
        col["value_type"] = vt
        col["sort_active"] = sort_key == key
        col["sort_dir"] = sort_dir if sort_key == key else ""

        calc_options: list[dict[str, Any]] = []
        if calc_eligible:
            for op in NUMERIC_OPS:
                if op not in allowed:
                    continue
                next_ops = dict(calc_ops)
                next_ops[key] = op
                # Compact: only non-default ops
                compact: dict[str, str] = {}
                for ck, cv in next_ops.items():
                    cinfo = meta_by_key.get(ck) or {}
                    csrc = _str(cinfo.get("source_property")) or source_property_from_column_key(ck)
                    cvt = _str(cinfo.get("value_type"))
                    if cv != default_calc_op(column_key=ck, value_type=cvt, source_property=csrc):
                        compact[ck] = cv
                pairs = _query_pairs_without(
                    query,
                    {
                        "col_calc",
                        "col_calc_add",
                        "col_order_add",
                        "col_order_remove",
                        "col_order_move",
                        "sem_cols_add",
                    },
                )
                serialized = col_calc_param(compact)
                if serialized:
                    pairs.append(("col_calc", serialized))
                calc_options.append(
                    {
                        "op": op,
                        "label": CALC_LABELS[op],
                        "selected": op == current_op,
                        "href": "?" + urlencode(pairs),
                    }
                )
        col["calc_options"] = calc_options

        sort_pairs_base = _query_pairs_without(
            query,
            {
                "hierarchy_sort",
                "hierarchy_sort_dir",
                "col_order_add",
                "col_order_remove",
                "col_order_move",
                "sem_cols_add",
                "col_calc_add",
            },
        )
        col["sort_asc_href"] = "?" + urlencode(
            sort_pairs_base + [("hierarchy_sort", key), ("hierarchy_sort_dir", "asc")]
        )
        col["sort_desc_href"] = "?" + urlencode(
            sort_pairs_base + [("hierarchy_sort", key), ("hierarchy_sort_dir", "desc")]
        )
        col["sort_clear_href"] = "?" + urlencode(sort_pairs_base)

        removable = bool(col.get("removable"))
        col["can_remove"] = removable and key not in {
            "ifc_class",
            "name",
            "status",
            "actions",
        }
        if col["can_remove"]:
            rem_pairs = _query_pairs_without(
                query,
                {
                    "col_order_remove",
                    "col_order_add",
                    "col_order_move",
                    "sem_cols_add",
                    "col_calc_add",
                },
            )
            rem_pairs.append(("col_order_remove", key))
            col["remove_href"] = "?" + urlencode(rem_pairs)
        else:
            col["remove_href"] = ""
