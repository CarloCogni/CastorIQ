# takeoff/services/quantity_mapping_safety.py
"""MAP-SAFETY-1 — mixed-selection analysis for batch mapping preview.

Pure inspection of selected prep rows. No DB writes, no IFC mutation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_SAMPLE_VALUES = 4

# Empty / unresolved placeholders — ignored unless other distinct values exist.
_IGNORE_VALUES = frozenset(
    {
        "",
        "—",
        "-",
        "n/a",
        "na",
        "none",
        "null",
        "unresolved",
        "unit not resolved",
        "length unit unresolved",
        "area unit unresolved",
        "volume unit unresolved",
        "model volume units",
        "model area units",
        "model length units",
        "mixed values",
    }
)

SAFETY_DIMENSIONS: tuple[dict[str, str], ...] = (
    {"field": "ifc_class", "label": "IFC Class"},
    {"field": "quantity_basis", "label": "Measurement Basis"},
    {"field": "unit_basis_display", "label": "Unit", "fallback": "unit_basis"},
    {"field": "semantic_category", "label": "Category"},
    {"field": "semantic_family", "label": "Family"},
    {"field": "prop:Identity Data.Project Level", "label": "Project Level"},
    {"field": "spatial:storey", "label": "Level / Storey"},
)

CONSISTENT_MESSAGE = "Selected rows look consistent for batch mapping."
MIXED_MESSAGE = "Selected rows contain mixed model evidence. Review before applying one mapping."


def _normalize_display(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    return text


def _is_ignored(value: str) -> bool:
    if not value:
        return True
    return value.casefold() in _IGNORE_VALUES


def _row_value(row: Mapping[str, Any], field: str, fallback: str = "") -> str:
    if field in row:
        return _normalize_display(row.get(field))
    # Property columns may live under prop_columns map.
    props = row.get("prop_columns")
    if isinstance(props, Mapping) and field in props:
        return _normalize_display(props.get(field))
    if fallback:
        return _normalize_display(row.get(fallback))
    return ""


def collect_dimension_values(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    fallback: str = "",
) -> list[str]:
    """Return sorted unique non-ignored values for one dimension."""
    found: set[str] = set()
    for row in rows:
        value = _row_value(row, field, fallback=fallback)
        if _is_ignored(value):
            continue
        found.add(value)
    return sorted(found)


def analyze_batch_selection_safety(
    selected_rows: Sequence[Mapping[str, Any]],
    *,
    max_samples: int = MAX_SAMPLE_VALUES,
) -> dict[str, Any]:
    """Inspect selected prep rows and return a consistent/mixed safety summary.

    Only analyzes the provided rows (callers must pass selected visible rows).
    """
    rows = [dict(r) for r in selected_rows if r]
    selected_count = len(rows)
    warnings: list[dict[str, Any]] = []

    for dim in SAFETY_DIMENSIONS:
        field = dim["field"]
        values = collect_dimension_values(
            rows,
            field=field,
            fallback=str(dim.get("fallback") or ""),
        )
        if len(values) < 2:
            continue
        samples = values[: max(1, max_samples)]
        warnings.append(
            {
                "field": field,
                "label": dim["label"],
                "value_count": len(values),
                "sample_values": samples,
                "samples_capped": len(values) > len(samples),
            }
        )

    status = "mixed" if warnings else "consistent"
    return {
        "status": status,
        "selected_count": selected_count,
        "warnings": warnings,
        "message": MIXED_MESSAGE if status == "mixed" else CONSISTENT_MESSAGE,
        "apply_label": (
            "Apply anyway to selected rows" if status == "mixed" else "Apply to session"
        ),
    }
