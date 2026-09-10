# takeoff/services/quantity_unit_display.py
"""Presentation-only unit labels for Quantity Preparation UI.

Keeps raw ``unit_basis`` values unchanged for export/backend while mapping
internal model-unit strings to product-safe display labels.
"""

from __future__ import annotations

from typing import Any


def resolve_quantity_unit_display(unit_raw: str | None) -> str:
    """Map internal unit_basis text to a user-facing Quantities label.

    Never surfaces ``model volume/area/length units`` or blank-unit wording.
    Does not invent SI units (m³/m²/m) until project/model context confirms them.
    """
    raw = str(unit_raw or "").strip()
    if not raw or raw == "—":
        return "—"
    key = raw.lower()
    if key == "count":
        return "count"
    if key in {"m3", "m³", "m^3", "cubic metre", "cubic metres", "cubic meter", "cubic meters"}:
        return "m³"
    if key in {"m2", "m²", "m^2", "square metre", "square metres", "square meter", "square meters"}:
        return "m²"
    if key in {"mm", "millimetre", "millimetres", "millimeter", "millimeters"}:
        return "mm"
    if key in {"cm", "centimetre", "centimetres", "centimeter", "centimeters"}:
        return "cm"
    if key in {"m", "metre", "metres", "meter", "meters"}:
        return "m"
    if "model volume" in key or key in {"volume unit", "volume units"}:
        return "Unit not resolved"
    if "model area" in key or key in {"area unit", "area units"}:
        return "Area unit unresolved"
    if "model length" in key or key in {"length unit", "length units"}:
        return "Length unit unresolved"
    # Unknown explicit unit strings stay unresolved until Castor confirms them.
    return "Unit not resolved"


def attach_unit_basis_display(row: dict[str, Any], *, field: str = "unit_basis") -> dict[str, Any]:
    """Add ``unit_basis_display`` beside the raw ``unit_basis`` value."""
    row["unit_basis_display"] = resolve_quantity_unit_display(row.get(field))
    return row
