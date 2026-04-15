# ifc_processor/templatetags/ifc_filters.py
"""General-purpose template filters for IFC data display."""

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
    """Format a property value for display.

    Rounds floats to 2 decimal places to avoid raw Python precision noise
    (e.g. 0.12345678901234 → 0.12). Non-float values pass through unchanged.
    """
    if isinstance(value, float):
        return round(value, 2)
    return value


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
