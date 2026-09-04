# takeoff/services/ifc_qto_flags.py
"""IFC Qto presence helpers shared by Model Readiness (Phase 1).

Extracted from package quantities helpers so origin/main quantities.py is not replaced.
Detects numeric Qto_* measures only — not BOQ / commercial cost.
"""

from __future__ import annotations

REAL_MEASURE_NAMES: frozenset[str] = frozenset(
    {
        "NetVolume",
        "GrossVolume",
        "NetArea",
        "GrossArea",
        "NetSideArea",
        "GrossSideArea",
        "GrossFootprintArea",
        "Length",
        "Width",
        "Height",
        "Depth",
        "Perimeter",
        "Count",
        "Area",
        "CrossSectionArea",
        "NetSurfaceArea",
        "OuterSurfaceArea",
        "GrossSurfaceArea",
    }
)

_SKIP_PROP_NAMES: frozenset[str] = frozenset({"id"})
_FALLBACK_UNIT = "ea"

# Linear measures: values are often model-native (e.g. mm) — do not claim metres.
LINEAR_MEASURE_NAMES: frozenset[str] = frozenset(
    {"Length", "Width", "Height", "Depth", "Perimeter"}
)
VOLUME_MEASURE_NAMES: frozenset[str] = frozenset({"NetVolume", "GrossVolume"})
AREA_MEASURE_NAMES: frozenset[str] = frozenset(
    {
        "NetArea",
        "GrossArea",
        "NetSideArea",
        "GrossSideArea",
        "GrossFootprintArea",
        "Area",
        "CrossSectionArea",
        "NetSurfaceArea",
        "OuterSurfaceArea",
        "GrossSurfaceArea",
    }
)


def _coerce_numeric(val: object) -> float | None:
    """Return float for numeric IFC quantity values; ignore bools and garbage."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def is_recognized_ifc_measure_name(prop_name: str | None) -> bool:
    """True when *prop_name* is a real IFC quantity measure (not Qto_*.id)."""
    if not prop_name:
        return False
    if prop_name.lower() in _SKIP_PROP_NAMES:
        return False
    if prop_name in REAL_MEASURE_NAMES:
        return True
    pl = prop_name.lower()
    return any(
        kw in pl
        for kw in (
            "volume",
            "area",
            "length",
            "width",
            "height",
            "depth",
            "perimeter",
            "count",
        )
    )


def _unit_for_prop_name(prop_name: str) -> str:
    """Infer display unit from a Qto_* property name.

    Linear measures use ``model units`` — exporters often store mm without
    converting to metres.
    """
    if prop_name in VOLUME_MEASURE_NAMES or "volume" in prop_name.lower():
        return "m³"
    if prop_name in AREA_MEASURE_NAMES or "area" in prop_name.lower():
        return "m²"
    if prop_name in LINEAR_MEASURE_NAMES or any(
        kw in prop_name.lower() for kw in ("length", "width", "height", "depth", "perimeter")
    ):
        return "model units"
    if prop_name == "Count" or prop_name.lower() == "count":
        return "ea"
    return _FALLBACK_UNIT


def iter_ifc_quantity_measures(props: dict | None):
    """Yield ``(qto_set, prop_name, value)`` for recognized numeric Qto measures.

    Supports nested Qto dicts and flat dotted keys. Skips ``Qto_*.id``.
    """
    if not props:
        return
    for key, val in props.items():
        if not isinstance(key, str) or not key.startswith("Qto_"):
            continue
        if isinstance(val, dict):
            for pname, pval in val.items():
                name = str(pname)
                if not is_recognized_ifc_measure_name(name):
                    continue
                num = _coerce_numeric(pval)
                if num is None:
                    continue
                yield key, name, num
            continue
        if "." not in key:
            continue
        pset, pname = key.split(".", 1)
        if not is_recognized_ifc_measure_name(pname):
            continue
        num = _coerce_numeric(val)
        if num is None:
            continue
        yield pset, pname, num


def entity_has_ifc_quantity(props: dict | None) -> bool:
    """True when *props* contain a recognized numeric IFC Qto measure.

    Supports both nested ``{"Qto_WallBaseQuantities": {"NetVolume": 1.2}}``
    and flat dotted keys ``{"Qto_WallBaseQuantities.NetVolume": 1.2}``.

    ``Qto_*.id`` alone does **not** count as quantity availability.
    """
    for _pset, _name, _num in iter_ifc_quantity_measures(props):
        return True
    return False
