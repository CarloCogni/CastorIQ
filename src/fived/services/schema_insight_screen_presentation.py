# fived/services/schema_insight_screen_presentation.py
"""Presentation helpers for Schema Quantity Insight screen (S3f).

Builds read-only screen summary and rollup display order from an S2 insight
payload. Does not query the database, mutate models, or change the S2 contract.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_GAP_LABELS: dict[str, str] = {
    "classification": "Classification",
    "package_mapping": "Package",
    "work_package": "Work package",
    "basis_unresolved_or_blank": "Missing or blank basis",
    "missing_quantity_source": "Missing quantity source",
    "non_numeric_total": "Non-numeric totals",
}

_PROVENANCE_LABELS: dict[str, str] = {
    "manual_session_schema_node": "Schema node (session)",
    "manual_session": "Free text (session)",
    "manual_field": "Manual field",
    "not_mapped": "Not mapped",
    "future_modify_handoff": "Future Modify handoff",
    "empty": "Empty",
    "other": "Other",
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def order_rollup_groups(groups: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return rollup groups with mapped groups first, Unmapped last."""
    items = [dict(g) for g in (groups or [])]
    items.sort(
        key=lambda g: (
            1 if g.get("is_unmapped") else 0,
            str(g.get("code") or ""),
            str(g.get("group_key") or ""),
        )
    )
    return items


def enrich_quantity_buckets(
    buckets: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Add display fields for quantity buckets without changing source values."""
    out: list[dict[str, Any]] = []
    for raw in buckets or []:
        basis = str(raw.get("quantity_basis") or "").strip()
        unit = str(raw.get("unit_basis") or "").strip()
        source = str(raw.get("quantity_source") or "").strip()
        rows = _int(raw.get("row_count"))
        total = _float(raw.get("total_sum"))
        non_numeric = _int(raw.get("non_numeric_count"))
        is_gap = not basis and not unit
        if is_gap:
            quantity_line = f"{rows} row{'s' if rows != 1 else ''} need quantity review"
            basis_line = "Missing basis/unit"
        else:
            basis_line = basis or "Basis not set"
            unit_label = unit or "units"
            if total is None:
                quantity_line = f"— {unit_label}"
            else:
                quantity_line = f"{total:g} {unit_label}"
        out.append(
            {
                **dict(raw),
                "display_basis": basis_line,
                "display_quantity": quantity_line,
                "display_rows": f"{rows} row{'s' if rows != 1 else ''}",
                "display_source": source or "",
                "is_gap_bucket": is_gap,
                "show_non_numeric": non_numeric > 0,
                "non_numeric_count": non_numeric,
            }
        )
    return out


def enrich_rollup_groups(
    groups: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Order groups and attach display fields for the product rollup layout."""
    ordered = order_rollup_groups(groups)
    enriched: list[dict[str, Any]] = []
    for group in ordered:
        buckets = enrich_quantity_buckets(group.get("quantity_buckets"))
        primary = buckets[0] if buckets else None
        if group.get("is_unmapped"):
            status = "Unmapped"
            status_hint = f"{_int(group.get('row_count'))} rows need mapping"
        elif any(b.get("is_gap_bucket") for b in buckets):
            status = "Needs review"
            status_hint = "Quantity basis or unit needs review"
        else:
            status = "Mapped"
            status_hint = ""
        enriched.append(
            {
                **group,
                "quantity_buckets_display": buckets,
                "primary_bucket": primary,
                "extra_bucket_count": max(len(buckets) - 1, 0),
                "status_label": status,
                "status_hint": status_hint,
            }
        )
    return enriched


def labeled_count_items(
    counts: Mapping[str, Any] | None,
    labels: Mapping[str, str],
    *,
    only_positive: bool = True,
) -> list[dict[str, Any]]:
    """Turn a counts dict into labeled rows for gaps UI."""
    items: list[dict[str, Any]] = []
    for key, value in (counts or {}).items():
        n = _int(value)
        if only_positive and n <= 0:
            continue
        items.append(
            {
                "key": str(key),
                "label": labels.get(str(key), str(key).replace("_", " ")),
                "count": n,
            }
        )
    return items


def build_schema_insight_screen_summary(insight: Mapping[str, Any]) -> dict[str, Any]:
    """Derive product summary cards from an S2 insight payload."""
    class_groups = list(insight.get("quantity_totals_by_classification") or [])
    pkg_groups = list(insight.get("quantity_totals_by_package") or [])
    wp_groups = list(insight.get("quantity_totals_by_work_package") or [])
    unmapped = insight.get("unmapped_counts") or {}
    basis_gaps = insight.get("quantity_basis_gap_counts") or {}

    mapped_class = [g for g in class_groups if not g.get("is_unmapped")]
    mapped_pkg = [g for g in pkg_groups if not g.get("is_unmapped")]
    mapped_wp = [g for g in wp_groups if not g.get("is_unmapped")]

    mapped_class_rows = sum(_int(g.get("row_count")) for g in mapped_class)
    unmapped_rows = _int(unmapped.get("classification"))
    if unmapped_rows <= 0:
        unmapped_rows = sum(_int(g.get("row_count")) for g in class_groups if g.get("is_unmapped"))

    basis_gap_total = sum(_int(v) for v in basis_gaps.values())

    summary = {
        "total_rows": _int(insight.get("row_count")),
        "mapped_classification_groups": len(mapped_class),
        "mapped_classification_rows": mapped_class_rows,
        "unmapped_classification_rows": unmapped_rows,
        "quantity_basis_source_gaps": basis_gap_total,
        "package_groups": len(mapped_pkg),
        "package_groups_total": len(pkg_groups),
        "work_package_groups": len(mapped_wp),
        "work_package_groups_total": len(wp_groups),
        "has_unmapped": unmapped_rows > 0,
        "unmapped_label": (
            f"{unmapped_rows} rows need mapping"
            if unmapped_rows
            else "No unmapped classification rows"
        ),
    }
    logger.debug(
        "schema insight screen summary rows=%s mapped_class=%s unmapped=%s",
        summary["total_rows"],
        summary["mapped_classification_groups"],
        summary["unmapped_classification_rows"],
    )
    return summary


def build_schema_insight_screen_presentation(
    insight: Mapping[str, Any],
) -> dict[str, Any]:
    """Return all presentation structures needed by the S3f product screen."""
    return {
        "summary": build_schema_insight_screen_summary(insight),
        "classification_groups": enrich_rollup_groups(
            insight.get("quantity_totals_by_classification")
        ),
        "package_groups": enrich_rollup_groups(insight.get("quantity_totals_by_package")),
        "work_package_groups": enrich_rollup_groups(insight.get("quantity_totals_by_work_package")),
        "unmapped_items": labeled_count_items(insight.get("unmapped_counts"), _GAP_LABELS),
        "missing_mapping_items": labeled_count_items(
            insight.get("missing_mapping_counts"), _GAP_LABELS
        ),
        "basis_gap_items": labeled_count_items(
            insight.get("quantity_basis_gap_counts"), _GAP_LABELS
        ),
        "provenance_items": labeled_count_items(
            insight.get("provenance_counts"), _PROVENANCE_LABELS
        ),
    }
