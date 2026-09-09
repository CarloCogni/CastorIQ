# fived/services/schema_insight_screen_presentation.py
"""Presentation helpers for 5D Quantity Review dashboard (S3g / Figma v4).

Builds read-only screen structures from an S2 insight payload. Does not query
the database, mutate models, or change the S2 contract/payload.
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

_CONFIRMED_CUBIC_METRE_UNITS = frozenset(
    {
        "m3",
        "m³",
        "m^3",
        "cubic metre",
        "cubic metres",
        "cubic meter",
        "cubic meters",
    }
)

_CONFIRMED_SQUARE_METRE_UNITS = frozenset(
    {
        "m2",
        "m²",
        "m^2",
        "square metre",
        "square metres",
        "square meter",
        "square meters",
    }
)

_CONFIRMED_LENGTH_UNITS = {
    "mm": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "cm": "cm",
    "m": "m",
    "metre": "m",
    "metres": "m",
    "meter": "m",
    "meters": "m",
}

_UNRESOLVED_UNIT_TOKENS = frozenset(
    {
        "",
        "units",
        "model volume units",
        "volume unit",
        "volume units",
        "model volume unit",
        "model area units",
        "model length units",
    }
)


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


def format_quantity_number(value: float | None) -> str:
    """Format a quantity with thousands separators for the dashboard."""
    if value is None:
        return "—"
    # Prefer two decimals when fractional, else integer-style.
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def resolve_unit_display(unit_raw: str | None) -> dict[str, Any]:
    """Map snapshot unit text to a product-safe display label.

    Never surfaces ``model volume units``. Confirmed metric labels become
    ``m³`` / ``m²`` / ``mm`` / ``m`` / ``count``; everything else remains
    ``Unit not resolved`` until confirmed.
    """
    raw = str(unit_raw or "").strip()
    key = raw.lower()
    if key in _CONFIRMED_CUBIC_METRE_UNITS:
        return {"label": "m³", "resolved": True, "raw": raw}
    if key in _CONFIRMED_SQUARE_METRE_UNITS:
        return {"label": "m²", "resolved": True, "raw": raw}
    if key == "count":
        return {"label": "count", "resolved": True, "raw": raw}
    if key in _CONFIRMED_LENGTH_UNITS:
        return {"label": _CONFIRMED_LENGTH_UNITS[key], "resolved": True, "raw": raw}
    if (
        key in _UNRESOLVED_UNIT_TOKENS
        or "model volume" in key
        or "model area" in key
        or "model length" in key
    ):
        return {"label": "Unit not resolved", "resolved": False, "raw": raw}
    # Unknown explicit unit strings stay unresolved until Castor confirms them.
    return {"label": "Unit not resolved", "resolved": False, "raw": raw}


def order_rollup_groups(groups: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return rollup groups with mapped groups first (largest quantity first)."""
    items = [dict(g) for g in (groups or [])]

    def _mapped_total(group: Mapping[str, Any]) -> float:
        total = 0.0
        for bucket in group.get("quantity_buckets") or []:
            value = _float(bucket.get("total_sum"))
            if value is not None:
                total += value
        return total

    items.sort(
        key=lambda g: (
            1 if g.get("is_unmapped") else 0,
            -_mapped_total(g),
            str(g.get("label") or ""),
            str(g.get("code") or ""),
        )
    )
    return items


def enrich_quantity_buckets(
    buckets: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Add dashboard display fields for quantity buckets."""
    out: list[dict[str, Any]] = []
    for raw in buckets or []:
        basis = str(raw.get("quantity_basis") or "").strip()
        unit = str(raw.get("unit_basis") or "").strip()
        source = str(raw.get("quantity_source") or "").strip()
        rows = _int(raw.get("row_count"))
        total = _float(raw.get("total_sum"))
        non_numeric = _int(raw.get("non_numeric_count"))
        is_gap = not basis
        unit_info = resolve_unit_display(unit)
        out.append(
            {
                **dict(raw),
                "display_basis": basis if basis else "Needs basis review",
                "display_quantity_number": format_quantity_number(total) if not is_gap else "—",
                "display_unit_status": unit_info["label"],
                "unit_resolved": unit_info["resolved"],
                "display_rows": f"{rows} row{'s' if rows != 1 else ''}",
                "display_source": source or "",
                "is_gap_bucket": is_gap,
                "show_non_numeric": non_numeric > 0,
                "non_numeric_count": non_numeric,
                "row_count": rows,
                "total_sum": total if total is not None else 0.0,
            }
        )
    return out


def enrich_rollup_groups(
    groups: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Order groups and attach dashboard fields; split mapped vs needs-setup."""
    ordered = order_rollup_groups(groups)
    enriched: list[dict[str, Any]] = []
    for group in ordered:
        buckets = enrich_quantity_buckets(group.get("quantity_buckets"))
        # Prefer a NetVolume bucket when present for primary display.
        primary = None
        for bucket in buckets:
            if str(bucket.get("quantity_basis") or "") == "NetVolume":
                primary = bucket
                break
        if primary is None:
            primary = next((b for b in buckets if not b.get("is_gap_bucket")), None)
        if primary is None and buckets:
            primary = buckets[0]

        if group.get("is_unmapped"):
            status = "Needs setup"
            status_hint = f"{_int(group.get('row_count'))} rows need mapping"
        elif primary and primary.get("is_gap_bucket"):
            status = "Needs review"
            status_hint = "Quantity basis or unit needs review"
        else:
            status = "Mapped"
            status_hint = ""

        title = str(group.get("label") or "").strip() or str(group.get("code") or "Group")
        code = str(group.get("code") or "").strip()
        enriched.append(
            {
                **group,
                "display_title": title,
                "display_code": code,
                "quantity_buckets_display": buckets,
                "primary_bucket": primary,
                "extra_bucket_count": max(len(buckets) - 1, 0),
                "status_label": status,
                "status_hint": status_hint,
            }
        )
    return enriched


def _unmapped_work_queue(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build the compact needs-mapping block for one rollup family."""
    unmapped = next((g for g in groups if g.get("is_unmapped")), None)
    if unmapped is None:
        return None
    buckets = unmapped.get("quantity_buckets_display") or []
    with_qty = sum(_int(b.get("row_count")) for b in buckets if not b.get("is_gap_bucket"))
    need_basis = sum(_int(b.get("row_count")) for b in buckets if b.get("is_gap_bucket"))
    rows = _int(unmapped.get("row_count"))
    return {
        "row_count": rows,
        "with_quantity_rows": with_qty,
        "need_basis_source_rows": need_basis,
        "title": f"{rows} rows need mapping",
        "action_label": "Review required",
        "status_label": "Needs setup",
    }


def labeled_count_items(
    counts: Mapping[str, Any] | None,
    labels: Mapping[str, str],
    *,
    only_positive: bool = True,
) -> list[dict[str, Any]]:
    """Turn a counts dict into labeled rows for Advanced details."""
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


def _netvolume_totals(
    groups: list[Mapping[str, Any]],
) -> tuple[float, str | None]:
    """Sum mapped NetVolume totals and capture raw unit text."""
    total = 0.0
    unit_raw: str | None = None
    for group in groups:
        if group.get("is_unmapped"):
            continue
        for bucket in group.get("quantity_buckets") or []:
            if str(bucket.get("quantity_basis") or "") != "NetVolume":
                continue
            value = _float(bucket.get("total_sum"))
            if value is not None:
                total += value
            if unit_raw is None:
                unit_raw = str(bucket.get("unit_basis") or "")
    return total, unit_raw


def build_schema_insight_screen_summary(insight: Mapping[str, Any]) -> dict[str, Any]:
    """Derive v4 dashboard summary metrics from an S2 insight payload."""
    class_groups = list(insight.get("quantity_totals_by_classification") or [])
    pkg_groups = list(insight.get("quantity_totals_by_package") or [])
    wp_groups = list(insight.get("quantity_totals_by_work_package") or [])
    unmapped = insight.get("unmapped_counts") or {}
    basis_buckets = list(insight.get("basis_unit_buckets") or [])

    mapped_class = [g for g in class_groups if not g.get("is_unmapped")]
    mapped_pkg = [g for g in pkg_groups if not g.get("is_unmapped")]
    mapped_wp = [g for g in wp_groups if not g.get("is_unmapped")]

    total_rows = _int(insight.get("row_count"))
    mapped_class_rows = sum(_int(g.get("row_count")) for g in mapped_class)
    unmapped_rows = _int(unmapped.get("classification"))
    if unmapped_rows <= 0:
        unmapped_rows = sum(_int(g.get("row_count")) for g in class_groups if g.get("is_unmapped"))

    usable_rows = 0
    need_basis_rows = 0
    for bucket in basis_buckets:
        basis = str(bucket.get("quantity_basis") or "").strip()
        rows = _int(bucket.get("row_count"))
        if basis:
            usable_rows += rows
        else:
            need_basis_rows += rows
    if usable_rows + need_basis_rows == 0 and total_rows:
        # Fallback if buckets are empty: treat mapped+unmapped-with-basis unknown.
        need_basis_rows = _int(
            (insight.get("quantity_basis_gap_counts") or {}).get("basis_unresolved_or_blank")
        )
        usable_rows = max(total_rows - need_basis_rows, 0)

    mapped_net_total, mapped_unit_raw = _netvolume_totals(class_groups)
    unit_info = resolve_unit_display(mapped_unit_raw)

    coverage_pct = round((mapped_class_rows / total_rows) * 100) if total_rows else 0
    needs_pct = max(100 - coverage_pct, 0)

    summary = {
        "total_rows": total_rows,
        "mapped_classification_groups": len(mapped_class),
        "mapped_classification_rows": mapped_class_rows,
        "unmapped_classification_rows": unmapped_rows,
        "usable_quantity_rows": usable_rows,
        "need_basis_source_rows": need_basis_rows,
        "mapped_netvolume_total": mapped_net_total,
        "mapped_netvolume_display": format_quantity_number(mapped_net_total),
        "mapped_netvolume_unit_status": unit_info["label"],
        "mapped_netvolume_unit_resolved": unit_info["resolved"],
        "package_groups": len(mapped_pkg),
        "work_package_groups": len(mapped_wp),
        "coverage_percent": coverage_pct,
        "needs_mapping_percent": needs_pct,
        "recommended_map_count": unmapped_rows,
        "coverage_value": f"{mapped_class_rows} / {total_rows}",
        "coverage_subtext": (
            f"{mapped_class_rows} mapped rows and {unmapped_rows} need mapping"
            if total_rows
            else "No rows in this snapshot"
        ),
        "readiness_value": f"{usable_rows} usable",
        "readiness_subtext": f"{need_basis_rows} need basis/source review",
        "rollup_groups_value": (f"{len(mapped_class)}, {len(mapped_pkg)}, {len(mapped_wp)}"),
        "rollup_groups_subtext": "classification, package, work package",
        "recommended_action_value": f"Map {unmapped_rows}",
        "recommended_action_subtext": "then freeze a new snapshot",
        "has_unmapped": unmapped_rows > 0,
    }
    logger.debug(
        "quantity review summary rows=%s mapped=%s unmapped=%s usable=%s",
        total_rows,
        mapped_class_rows,
        unmapped_rows,
        usable_rows,
    )
    return summary


def build_attention_panel(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Build the right-side What needs attention panel content."""
    return {
        "title": "What needs attention",
        "intro": ("A short work queue for turning this snapshot into a stronger quantity model."),
        "items": [
            {
                "title": "Map remaining rows",
                "body": (
                    f"{summary.get('unmapped_classification_rows', 0)} rows are not "
                    "connected to classification, package, or work package schema nodes."
                ),
                "badge": f"{summary.get('unmapped_classification_rows', 0)} items",
                "testid": "s3g-attention-map",
            },
            {
                "title": "Review basis/source",
                "body": (
                    f"{summary.get('need_basis_source_rows', 0)} rows need basis/source "
                    "review before reliable quantity analysis."
                ),
                "badge": f"{summary.get('need_basis_source_rows', 0)} items",
                "testid": "s3g-attention-basis",
            },
            {
                "title": "Resolve units",
                "body": (
                    "Show cubic metres only after model/snapshot units are confirmed; "
                    "otherwise keep unit unresolved."
                ),
                "badge": "Pending",
                "testid": "s3g-attention-units",
            },
            {
                "title": "Freeze after changes",
                "body": "Create a new snapshot after mapping or unit changes.",
                "badge": "Audit trail",
                "testid": "s3g-attention-freeze",
            },
        ],
    }


def build_schema_insight_screen_presentation(
    insight: Mapping[str, Any],
) -> dict[str, Any]:
    """Return all presentation structures for the S3g Quantity Review dashboard."""
    summary = build_schema_insight_screen_summary(insight)
    classification = enrich_rollup_groups(insight.get("quantity_totals_by_classification"))
    package = enrich_rollup_groups(insight.get("quantity_totals_by_package"))
    work_package = enrich_rollup_groups(insight.get("quantity_totals_by_work_package"))
    return {
        "summary": summary,
        "attention": build_attention_panel(summary),
        "classification_groups": [g for g in classification if not g.get("is_unmapped")],
        "classification_needs_mapping": _unmapped_work_queue(classification),
        "package_groups": [g for g in package if not g.get("is_unmapped")],
        "package_needs_mapping": _unmapped_work_queue(package),
        "work_package_groups": [g for g in work_package if not g.get("is_unmapped")],
        "work_package_needs_mapping": _unmapped_work_queue(work_package),
        # Keep full enriched lists for Advanced details only.
        "classification_groups_all": classification,
        "package_groups_all": package,
        "work_package_groups_all": work_package,
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
