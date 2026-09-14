# takeoff/services/quantity_hierarchy_sort.py
"""QTO-COLUMNS-10 — sibling-stable sort within Class → Type → Instance tree.

Sorts full matching sibling sets before pagination/load-more. Never flattens
descendants away from parents. Sorting does not change totals.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

HIERARCHY_SORT_PARAM = "hierarchy_sort"
HIERARCHY_SORT_DIR_PARAM = "hierarchy_sort_dir"


def _str(value: Any) -> str:
    return str(value or "").strip()


def parse_hierarchy_sort(query: Mapping[str, Any] | None) -> dict[str, str]:
    """Return ``{key, dir}`` with dir in asc/desc; empty key clears sort."""
    if not query:
        return {"key": "", "dir": "asc"}
    key = _str(query.get(HIERARCHY_SORT_PARAM))
    direction = _str(query.get(HIERARCHY_SORT_DIR_PARAM)).lower() or "asc"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    return {"key": key, "dir": direction}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    text = _str(value).replace(",", "")
    # Strip partial coverage suffixes: "12.3 (3 of 5 values)"
    if " (" in text:
        text = text.split(" (", 1)[0]
    if not text or text in {"—", "-", "Multiple values", "Complex value"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _sort_value(node: Mapping[str, Any], column_key: str) -> tuple[int, Any, str]:
    """Return (missing_rank, comparable, tie_id) for one node."""
    key = _str(column_key)
    tie = _str(node.get("node_key") or node.get("row_key") or node.get("global_id"))
    if key in {"name", "type_name"}:
        text = _str(node.get("primary_name") or node.get("type_name") or node.get("name"))
        return (0 if text else 1, text.lower(), tie)
    if key == "ifc_class":
        text = _str(node.get("ifc_class"))
        return (0 if text else 1, text.lower(), tie)
    if key == "quantity":
        num = _to_decimal(node.get("total"))
        if num is None:
            return (1, Decimal("0"), tie)
        return (0, num, tie)
    if key == "status":
        text = _str(node.get("review_status") or node.get("status"))
        return (0 if text else 1, text.lower(), tie)

    by_key = (
        node.get("prop_column_by_key") if isinstance(node.get("prop_column_by_key"), dict) else {}
    )
    cell = by_key.get(key) if isinstance(by_key, dict) else None
    display = ""
    num = None
    if isinstance(cell, dict):
        display = _str(cell.get("display"))
        num = _to_decimal(cell.get("numeric_value"))
        if num is None:
            num = _to_decimal(display)
    else:
        display = _str(node.get(key))
        num = _to_decimal(display)
    if num is not None:
        return (0, num, tie)
    if display and display not in {"—", "Multiple values", "Complex value"}:
        return (0, display.lower(), tie)
    return (1, "", tie)


def _sort_nodes(
    nodes: list[dict[str, Any]],
    *,
    column_key: str,
    direction: str,
) -> list[dict[str, Any]]:
    """Sort siblings ascending or descending; missing values stay at the end.

    Tie-break is stable identity (node_key / row_key / global_id), then input order.
    """
    reverse = direction == "desc"
    decorated = [(_sort_value(n, column_key), i, n) for i, n in enumerate(nodes)]
    present = [t for t in decorated if t[0][0] == 0]
    missing = [t for t in decorated if t[0][0] != 0]
    # Ascending: missing_rank, value, tie, index. Descending flips value order only.
    present.sort(key=lambda t: (t[0][1], t[0][2], t[1]), reverse=reverse)
    missing.sort(key=lambda t: (t[0][2], t[1]))
    return [t[2] for t in present] + [t[2] for t in missing]


def apply_hierarchy_sort_to_tree(
    hierarchy: MutableMapping[str, Any],
    *,
    column_key: str,
    direction: str = "asc",
    row_lookup: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Sort class / type / instance sibling lists in-place on the hierarchy tree.

    ``row_lookup`` maps node_key → enriched prep row (for prop cell values).
    """
    key = _str(column_key)
    if not key:
        return
    direction = "desc" if _str(direction).lower() == "desc" else "asc"
    lookup = dict(row_lookup or {})

    def _enrich(node: dict[str, Any]) -> dict[str, Any]:
        nk = _str(node.get("node_key"))
        extra = lookup.get(nk) or {}
        if extra:
            merged = dict(node)
            if isinstance(extra.get("prop_column_by_key"), dict):
                merged["prop_column_by_key"] = extra["prop_column_by_key"]
            if "total" in extra:
                merged["total"] = extra.get("total")
            if extra.get("primary_name"):
                merged["primary_name"] = extra.get("primary_name")
            return merged
        return node

    classes = list(hierarchy.get("classes") or [])
    enriched_classes = [_enrich(dict(c)) for c in classes if isinstance(c, dict)]
    sorted_classes = _sort_nodes(enriched_classes, column_key=key, direction=direction)
    # Preserve original class dicts in new order
    by_ck = {_str(c.get("node_key")): c for c in classes if isinstance(c, dict)}
    hierarchy["classes"] = [
        by_ck[k] for k in (_str(c.get("node_key")) for c in sorted_classes) if k in by_ck
    ]

    type_by_key = hierarchy.get("type_by_key") or {}
    instances_by_type = hierarchy.get("instances_by_type_key") or {}

    for class_node in hierarchy.get("classes") or []:
        if not isinstance(class_node, dict):
            continue
        type_keys = list(class_node.get("type_keys") or [])
        type_nodes: list[dict[str, Any]] = []
        for tk in type_keys:
            tnode = type_by_key.get(tk)
            if isinstance(tnode, dict):
                type_nodes.append(_enrich(dict(tnode)))
        sorted_types = _sort_nodes(type_nodes, column_key=key, direction=direction)
        class_node["type_keys"] = [_str(t.get("node_key")) for t in sorted_types]

        for tnode in sorted_types:
            tk = _str(tnode.get("node_key"))
            inst_list = list(instances_by_type.get(tk) or [])
            enriched_inst = [_enrich(dict(i)) for i in inst_list if isinstance(i, dict)]
            sorted_inst = _sort_nodes(enriched_inst, column_key=key, direction=direction)
            # Replace with original dicts in sorted order
            by_ik = {_str(i.get("node_key")): i for i in inst_list if isinstance(i, dict)}
            instances_by_type[tk] = [
                by_ik[k] for k in (_str(i.get("node_key")) for i in sorted_inst) if k in by_ik
            ]

    hierarchy["instances_by_type_key"] = instances_by_type
    logger.debug("hierarchy sort key=%s dir=%s", key, direction)
