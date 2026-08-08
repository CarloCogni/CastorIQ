# castor/ifc_insights/services/quantities.py
"""Quantity Take-Off (QTO) service — extracts and caches element quantities."""

from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quantity extraction spec: ifc_type → [(pset_name, property_name, unit)]
# Entries are tried in order; first numeric hit wins.
# ---------------------------------------------------------------------------
_QTO_SPEC: dict[str, list[tuple[str, str, str]]] = {
    "IfcWall": [
        ("Qto_WallBaseQuantities", "NetSideArea", "m²"),
        ("Qto_WallBaseQuantities", "NetVolume", "m³"),
        ("Qto_WallBaseQuantities", "GrossVolume", "m³"),
    ],
    "IfcWallStandardCase": [
        ("Qto_WallBaseQuantities", "NetSideArea", "m²"),
        ("Qto_WallBaseQuantities", "NetVolume", "m³"),
    ],
    "IfcSlab": [
        ("Qto_SlabBaseQuantities", "NetArea", "m²"),
        ("Qto_SlabBaseQuantities", "NetVolume", "m³"),
        ("Qto_SlabBaseQuantities", "GrossVolume", "m³"),
    ],
    "IfcColumn": [
        ("Qto_ColumnBaseQuantities", "NetVolume", "m³"),
        ("Qto_ColumnBaseQuantities", "GrossVolume", "m³"),
    ],
    "IfcBeam": [
        ("Qto_BeamBaseQuantities", "NetVolume", "m³"),
        ("Qto_BeamBaseQuantities", "GrossVolume", "m³"),
    ],
    "IfcDoor": [
        ("Qto_DoorBaseQuantities", "Area", "m²"),
        ("Qto_DoorBaseQuantities", "Width", "m"),
    ],
    "IfcWindow": [
        ("Qto_WindowBaseQuantities", "Area", "m²"),
    ],
    "IfcRoof": [
        ("Qto_RoofBaseQuantities", "NetArea", "m²"),
    ],
    "IfcStair": [
        ("Qto_StairBaseQuantities", "NetVolume", "m³"),
    ],
    "IfcRamp": [
        ("Qto_RampBaseQuantities", "NetVolume", "m³"),
    ],
    "IfcFooting": [
        ("Qto_FootingBaseQuantities", "NetVolume", "m³"),
        ("Qto_FootingBaseQuantities", "GrossVolume", "m³"),
    ],
    "IfcPile": [
        ("Qto_PileBaseQuantities", "Length", "m"),
    ],
    "IfcCovering": [
        ("Qto_CoveringBaseQuantities", "NetArea", "m²"),
    ],
    "IfcPlate": [
        ("Qto_PlateBaseQuantities", "NetArea", "m²"),
    ],
}

# Default unit displayed when only a fallback count is available
_FALLBACK_UNIT = "ea"

# Recognized real quantity measure names (exclude metadata like Qto_*.id).
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


def _extract_quantity(ifc_type: str, props: dict) -> tuple[float | None, str, str]:
    """Return (quantity, unit, source) for a single entity.

    source is 'ifc' when extracted from a Qto_* property set,
    or 'estimated' when falling back to a generic scan or count.

    Accepts nested Qto_* dicts and flat dotted keys written by the IFC parser
    (e.g. ``Qto_WallBaseQuantities.NetVolume``).
    """
    candidates = _QTO_SPEC.get(ifc_type, [])

    # 1. Exact pset + property lookup (nested dict OR flat dotted key)
    for pset_name, prop_name, unit in candidates:
        pset = props.get(pset_name)
        if isinstance(pset, dict):
            num = _coerce_numeric(pset.get(prop_name))
            if num is not None:
                return num, unit, "ifc"
        flat_key = f"{pset_name}.{prop_name}"
        num = _coerce_numeric(props.get(flat_key))
        if num is not None:
            return num, unit, "ifc"

    # 2. Generic scan over recognized Qto measures only (skips Qto_*.id)
    for _pset, pname, num in iter_ifc_quantity_measures(props):
        return num, _unit_for_prop_name(pname), "ifc"

    return None, _FALLBACK_UNIT, "estimated"


def _extract_material(props: dict) -> str:
    """Return first material value found in top-level properties, or empty string."""
    for k, v in props.items():
        if k.lower().endswith("material") and v:
            return str(v).strip()
    return ""


def compute_qto(project) -> dict:
    """Compute QTO for *project*'s latest completed IFC file and cache the result.

    Returns a result dict suitable for JSON response (excludes heavy items_json).
    Idempotent: preserves existing unit_costs_json across re-computations.
    """
    from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement
    from takeoff.models import QTOCache

    ifc_file = (
        IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )
    if not ifc_file:
        return {"has_data": False}

    # Preserve user-set unit costs across re-computation
    existing = QTOCache.objects.filter(ifc_file=ifc_file).first()
    unit_costs: dict[str, float] = dict(existing.unit_costs_json) if existing else {}

    # ------------------------------------------------------------------
    # Build level-name + elevation lookups from IFCSpatialElement
    # ------------------------------------------------------------------
    level_name_map: dict = dict(
        IFCSpatialElement.objects.filter(ifc_file=ifc_file)
        .select_related("entity")
        .values_list("pk", "entity__name")
    )
    level_elev_map: dict = {
        str(pk): (float(elev) if elev is not None else float("inf"))
        for pk, elev in IFCSpatialElement.objects.filter(ifc_file=ifc_file).values_list(
            "pk", "elevation"
        )
    }

    # ------------------------------------------------------------------
    # Iterate entities — single DB query with only() for efficiency
    # ------------------------------------------------------------------
    entities = list(
        IFCEntity.objects.filter(ifc_file=ifc_file).only(
            "global_id", "name", "ifc_type", "properties", "spatial_container_id"
        )
    )
    total = len(entities)
    if total == 0:
        return {"has_data": False}

    items: list[dict] = []
    entities_with_qty = 0

    type_agg: dict[str, dict] = {}
    level_agg: dict[str, dict] = {}
    material_agg: dict[str, dict] = defaultdict(lambda: {"entity_count": 0, "cost": 0.0})

    for ent in entities:
        ifc_type = ent.ifc_type or "Unknown"
        props = ent.properties or {}

        # Level
        sc_id = str(ent.spatial_container_id) if ent.spatial_container_id else None
        level_name = level_name_map.get(ent.spatial_container_id, "Unassigned") or "Unassigned"
        elevation = level_elev_map.get(sc_id, float("inf")) if sc_id else float("inf")

        # Material + quantity
        material = _extract_material(props)
        qty, unit, source = _extract_quantity(ifc_type, props)
        if qty is not None:
            entities_with_qty += 1

        # Cost
        unit_cost_val = unit_costs.get(ifc_type)
        total_cost_val: float | None = None
        if unit_cost_val is not None and qty is not None:
            total_cost_val = round(qty * unit_cost_val, 2)

        item = {
            "global_id": ent.global_id,
            "name": ent.name or ent.global_id[:8],
            "type": ifc_type,
            "level": level_name,
            "material": material,
            "quantity": round(qty, 4) if qty is not None else None,
            "unit": unit,
            "source": source,
            "unit_cost": unit_cost_val,
            "total_cost": total_cost_val,
        }
        items.append(item)

        # Type accumulator
        if ifc_type not in type_agg:
            type_agg[ifc_type] = {
                "count": 0,
                "qty_sum": 0.0,
                "qty_count": 0,
                "unit": unit,
                "cost_sum": 0.0,
                "top_entities": [],
            }
        ta = type_agg[ifc_type]
        ta["count"] += 1
        if qty is not None:
            ta["qty_sum"] += qty
            ta["qty_count"] += 1
        if total_cost_val is not None:
            ta["cost_sum"] += total_cost_val
        if len(ta["top_entities"]) < 5:
            ta["top_entities"].append(
                {
                    "global_id": ent.global_id,
                    "name": ent.name or ent.global_id[:8],
                    "qty": round(qty, 4) if qty is not None else None,
                    "unit": unit,
                }
            )

        # Level accumulator
        if level_name not in level_agg:
            level_agg[level_name] = {
                "entity_count": 0,
                "cost": 0.0,
                "_elevation": elevation,
            }
        level_agg[level_name]["entity_count"] += 1
        if total_cost_val is not None:
            level_agg[level_name]["cost"] += total_cost_val

        # Material accumulator
        if material:
            material_agg[material]["entity_count"] += 1
            if total_cost_val is not None:
                material_agg[material]["cost"] += total_cost_val

    # ------------------------------------------------------------------
    # Build output JSON blobs
    # ------------------------------------------------------------------
    summary = sorted(
        [
            {
                "type": t,
                "count": ta["count"],
                "total_qty": round(ta["qty_sum"], 2),
                "unit": ta["unit"],
                "coverage_pct": round(ta["qty_count"] / ta["count"] * 100, 1)
                if ta["count"]
                else 0.0,
                "unit_cost": unit_costs.get(t),
                "total_cost": round(ta["cost_sum"], 2) if ta["cost_sum"] > 0 else None,
                "top_entities": ta["top_entities"],
            }
            for t, ta in type_agg.items()
        ],
        key=lambda x: -x["count"],
    )

    by_level = [
        {"level": ln, "entity_count": la["entity_count"], "cost": round(la["cost"], 2)}
        for ln, la in sorted(
            level_agg.items(),
            key=lambda kv: (kv[1]["_elevation"], kv[0]),
        )
    ]

    by_material = sorted(
        [
            {"material": mat, "entity_count": ma["entity_count"], "cost": round(ma["cost"], 2)}
            for mat, ma in material_agg.items()
            if mat
        ],
        key=lambda x: -x["entity_count"],
    )[:8]

    coverage_pct = round(entities_with_qty / total * 100, 1) if total else 0.0
    cost_vals = [s["total_cost"] for s in summary if s["total_cost"] is not None]
    total_cost_est: float | None = round(sum(cost_vals), 2) if cost_vals else None

    QTOCache.objects.update_or_create(
        ifc_file=ifc_file,
        defaults={
            "project": project,
            "total_entities": total,
            "entities_with_qty": entities_with_qty,
            "coverage_pct": coverage_pct,
            "total_cost_estimate": total_cost_est,
            "summary_json": summary,
            "by_level_json": by_level,
            "by_material_json": by_material,
            "items_json": items,
            "unit_costs_json": unit_costs,
        },
    )

    logger.info(
        "QTO computed — project %s: %d entities, %.1f%% coverage, cost=%s",
        project.pk,
        total,
        coverage_pct,
        total_cost_est,
    )

    return {
        "has_data": True,
        "total_entities": total,
        "entities_with_qty": entities_with_qty,
        "coverage_pct": coverage_pct,
        "total_cost_estimate": total_cost_est,
        "summary": summary,
        "by_level": by_level,
        "by_material": by_material,
    }
