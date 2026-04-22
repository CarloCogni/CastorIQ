# ifc_processor/templatetags/ifc_filters.py
"""General-purpose template filters for IFC data display."""

import math

from django import template

register = template.Library()

# ---------------------------------------------------------------------------
# Quantity name → IFC unit type mapping
# ---------------------------------------------------------------------------
# Used to resolve which project unit applies to a given Qto_ property name.
# Keys are lowercase; lookup normalises the property name before matching.

_QUANTITY_UNIT_MAP: dict[str, str] = {
    # Length
    "length": "LENGTHUNIT",
    "width": "LENGTHUNIT",
    "height": "LENGTHUNIT",
    "depth": "LENGTHUNIT",
    "thickness": "LENGTHUNIT",
    "perimeter": "LENGTHUNIT",
    "radius": "LENGTHUNIT",
    "diameter": "LENGTHUNIT",
    # Area
    "area": "AREAUNIT",
    "surfacearea": "AREAUNIT",
    "crosssectionarea": "AREAUNIT",
    "outersurfacearea": "AREAUNIT",
    "grosssurfacearea": "AREAUNIT",
    "netsurfacearea": "AREAUNIT",
    "grossarea": "AREAUNIT",
    "netarea": "AREAUNIT",
    "grossfloorarea": "AREAUNIT",
    "netfloorarea": "AREAUNIT",
    # Volume
    "volume": "VOLUMEUNIT",
    "grossvolume": "VOLUMEUNIT",
    "netvolume": "VOLUMEUNIT",
    # Mass
    "weight": "MASSUNIT",
    "mass": "MASSUNIT",
    "grossweight": "MASSUNIT",
    "netweight": "MASSUNIT",
}


def get_unit_type_for_quantity(prop_name: str) -> str | None:
    """Map a quantity property name to its IFC unit type.

    Tries exact match first, then suffix match (e.g. "NetSideArea" ends
    with "area" → AREAUNIT).
    """
    lower = prop_name.lower()
    # Exact match
    if lower in _QUANTITY_UNIT_MAP:
        return _QUANTITY_UNIT_MAP[lower]
    # Suffix match — longest suffix wins
    for suffix, unit_type in sorted(
        _QUANTITY_UNIT_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if lower.endswith(suffix):
            return unit_type
    return None


# ---------------------------------------------------------------------------
# Unit-aware rounding
# ---------------------------------------------------------------------------

# Precision (decimal places) per unit label. Millimetres are already
# "whole-number precise" in IFC files, so showing decimals is noise.
_UNIT_PRECISION: dict[str, int] = {
    "mm": 0,
    "cm": 1,
}
_DEFAULT_PRECISION: int = 2  # m, m², m³, kg, …


def smart_round(value: float, unit: str | None = None) -> int | float:
    """Round a numeric value to the precision appropriate for *unit*.

    - ``mm`` → integer (2700, not 2700.00)
    - ``cm`` → 1 decimal
    - everything else → 2 decimals
    """
    decimals = _UNIT_PRECISION.get(unit or "", _DEFAULT_PRECISION)
    if decimals == 0:
        return int(round(value))
    return round(value, decimals)


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------


@register.filter
def format_value(value: object) -> object:
    """Format a numeric value for display with thousand separators and up to
    2 decimals (trailing zeros stripped).

    Accepts Python ``int``/``float`` and numeric-looking strings. Non-numeric
    values (labels like ``"EI60"``, ``"(not set)"``, ``None``, booleans,
    non-finite floats) pass through unchanged.

    Examples: ``6.33939393939394`` → ``"6.34"``; ``"5.0"`` → ``"5"``;
    ``1234.5`` → ``"1,234.5"``; ``"EI60"`` → ``"EI60"``.
    """
    if isinstance(value, bool):
        return value
    num: float | None = None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return value
    if num is None or not math.isfinite(num):
        return value
    return f"{num:,.2f}".rstrip("0").rstrip(".")


@register.filter
def spatial_icon(spatial_type: str) -> str:
    """Map an IFCSpatialElement.spatial_type value to a Bootstrap icon class.

    Usage: ``{{ node.spatial_type|spatial_icon }}``
    """
    return {
        "site": "bi-geo-alt",
        "building": "bi-building",
        "building_storey": "bi-layers",
        "space": "bi-door-open",
        "facility": "bi-building",
        "facility_part": "bi-building",
    }.get(spatial_type or "", "bi-box")


@register.filter
def dictget(dictionary: object, key: str) -> object:
    """Look up a key in a dictionary, returning None on miss.

    Usage: ``{{ my_dict|dictget:key_var }}``
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None
