# takeoff/services/measurement_resolver.py
"""Deterministic measurement type → IFC source resolver (R5D-QTO-MEASURE-01).

Foundation only: does not rewrite legacy ``quantity_basis`` prep rows, freeze
payloads, or HASH-1 snapshot hashes. Wire into prep UI in a later slice.

Concepts kept separate:
* measurement_type — count | length | area | volume
* selected_source — element_count | Length | NetArea | GrossArea | NetVolume | GrossVolume
* total — numeric aggregate (0 preserved; missing is None)
* model_unit_family — count | length | area | volume (no conversion)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

MeasurementType = Literal["count", "length", "area", "volume"]
ResolutionStatus = Literal["resolved", "choice_required", "unavailable", "unresolved"]
ModelUnitFamily = Literal["count", "length", "area", "volume"]

MEASUREMENT_TYPES: frozenset[str] = frozenset({"count", "length", "area", "volume"})

MEASUREMENT_TYPE_LABELS: dict[str, str] = {
    "count": "Count",
    "length": "Length",
    "area": "Area",
    "volume": "Volume",
}

SOURCE_ELEMENT_COUNT = "element_count"
SOURCE_LENGTH = "Length"
SOURCE_NET_AREA = "NetArea"
SOURCE_GROSS_AREA = "GrossArea"
SOURCE_NET_VOLUME = "NetVolume"
SOURCE_GROSS_VOLUME = "GrossVolume"

# Explicit semantic compatibility — SideArea / Width / Height / Perimeter excluded.
COMPATIBLE_SOURCES: dict[str, tuple[str, ...]] = {
    "count": (SOURCE_ELEMENT_COUNT,),
    "length": (SOURCE_LENGTH,),
    "area": (SOURCE_NET_AREA, SOURCE_GROSS_AREA),
    "volume": (SOURCE_NET_VOLUME, SOURCE_GROSS_VOLUME),
}

UNIT_FAMILY_FOR_TYPE: dict[str, ModelUnitFamily] = {
    "count": "count",
    "length": "length",
    "area": "area",
    "volume": "volume",
}

INVENTORY_MEASURE_KEYS: tuple[str, ...] = (
    SOURCE_NET_VOLUME,
    SOURCE_GROSS_VOLUME,
    SOURCE_NET_AREA,
    SOURCE_GROSS_AREA,
    SOURCE_LENGTH,
)


@dataclass(frozen=True, slots=True)
class MeasurementResolution:
    """Immutable resolver result for one measurement target + type (+ optional source)."""

    measurement_type: str
    status: ResolutionStatus
    compatible_sources: tuple[str, ...]
    selected_source: str | None
    total: float | int | None
    model_unit_family: ModelUnitFamily
    resolution_reason: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize for JSON / templates."""
        return {
            "measurement_type": self.measurement_type,
            "status": self.status,
            "compatible_sources": list(self.compatible_sources),
            "selected_source": self.selected_source,
            "total": self.total,
            "model_unit_family": self.model_unit_family,
            "resolution_reason": self.resolution_reason,
        }


def normalize_measurement_type(raw: str | None) -> str | None:
    """Map labels/legacy basis names to canonical measurement_type, or None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    lower = text.lower()
    if lower in MEASUREMENT_TYPES:
        return lower
    labels = {v.lower(): k for k, v in MEASUREMENT_TYPE_LABELS.items()}
    if lower in labels:
        return labels[lower]
    # Legacy basis names → type (not auto-selected source).
    legacy = {
        "netvolume": "volume",
        "grossvolume": "volume",
        "netarea": "area",
        "grossarea": "area",
        "length": "length",
        "count": "count",
    }
    return legacy.get(lower.replace(" ", ""))


def _coerce_present_number(value: object) -> float | int | None:
    """Return numeric value including 0; None means missing/unavailable.

    Preserves full float precision. Callers round only for display — never
    round here, or class totals drift from sum(round(instance)) vs round(sum).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    return num


def inventory_has_source(inventory: Mapping[str, Any], source: str) -> bool:
    """True when *source* is present in inventory (value may be 0)."""
    if source == SOURCE_ELEMENT_COUNT:
        return "element_count" in inventory and inventory.get("element_count") is not None
    return source in inventory and inventory.get(source) is not None


def list_compatible_sources(
    measurement_type: str,
    inventory: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return compatible sources that are present on the aggregate inventory."""
    allowed = COMPATIBLE_SOURCES.get(measurement_type, ())
    return tuple(src for src in allowed if inventory_has_source(inventory, src))


def _read_source_total(inventory: Mapping[str, Any], source: str) -> float | int | None:
    if source == SOURCE_ELEMENT_COUNT:
        raw = inventory.get("element_count")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            coerced = _coerce_present_number(raw)
            return int(coerced) if coerced is not None else None
    return _coerce_present_number(inventory.get(source))


def resolve_measurement(
    *,
    measurement_type: str,
    inventory: Mapping[str, Any],
    selected_source: str | None = None,
) -> MeasurementResolution:
    """Resolve measurement type against aggregate inventory.

    Parameters
    ----------
    measurement_type:
        Canonical ``count|length|area|volume``.
    inventory:
        Aggregate measures. Missing keys/values are absent; numeric ``0`` is present.
        Must include ``element_count`` for Count.
    selected_source:
        Optional explicit IFC/synthetic source. When set, must be compatible and present.
    """
    mtype = normalize_measurement_type(measurement_type)
    family: ModelUnitFamily = UNIT_FAMILY_FOR_TYPE.get(mtype or "", "count")  # type: ignore[assignment]

    if mtype is None:
        return MeasurementResolution(
            measurement_type=str(measurement_type or "").strip() or "unresolved",
            status="unresolved",
            compatible_sources=(),
            selected_source=None,
            total=None,
            model_unit_family="count",
            resolution_reason="Unknown measurement type.",
        )

    family = UNIT_FAMILY_FOR_TYPE[mtype]
    compatible = list_compatible_sources(mtype, inventory)

    if selected_source is not None and str(selected_source).strip():
        source = str(selected_source).strip()
        allowed = COMPATIBLE_SOURCES[mtype]
        if source not in allowed:
            return MeasurementResolution(
                measurement_type=mtype,
                status="unavailable",
                compatible_sources=compatible,
                selected_source=None,
                total=None,
                model_unit_family=family,
                resolution_reason=(
                    f"Source {source!r} is not compatible with measurement type {mtype!r}."
                ),
            )
        if not inventory_has_source(inventory, source):
            return MeasurementResolution(
                measurement_type=mtype,
                status="unavailable",
                compatible_sources=compatible,
                selected_source=None,
                total=None,
                model_unit_family=family,
                resolution_reason=f"Requested source {source!r} is not present on this aggregate.",
            )
        total = _read_source_total(inventory, source)
        return MeasurementResolution(
            measurement_type=mtype,
            status="resolved",
            compatible_sources=compatible,
            selected_source=source,
            total=total,
            model_unit_family=family,
            resolution_reason=f"Explicit source {source} selected.",
        )

    if not compatible:
        return MeasurementResolution(
            measurement_type=mtype,
            status="unavailable",
            compatible_sources=(),
            selected_source=None,
            total=None,
            model_unit_family=family,
            resolution_reason=f"No compatible {mtype} sources on this aggregate.",
        )

    if len(compatible) == 1:
        source = compatible[0]
        total = _read_source_total(inventory, source)
        return MeasurementResolution(
            measurement_type=mtype,
            status="resolved",
            compatible_sources=compatible,
            selected_source=source,
            total=total,
            model_unit_family=family,
            resolution_reason=f"Single compatible source auto-selected: {source}.",
        )

    return MeasurementResolution(
        measurement_type=mtype,
        status="choice_required",
        compatible_sources=compatible,
        selected_source=None,
        total=None,
        model_unit_family=family,
        resolution_reason=(
            "Multiple compatible sources require an explicit selection: "
            + ", ".join(compatible)
            + "."
        ),
    )


def inventory_from_aggregate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Build resolver inventory from a ModelQuantities row (prefers measure_inventory)."""
    inv: dict[str, Any] = {}
    count = row.get("element_count")
    if count is not None:
        try:
            inv[SOURCE_ELEMENT_COUNT] = int(count)
        except (TypeError, ValueError):
            coerced = _coerce_present_number(count)
            if coerced is not None:
                inv[SOURCE_ELEMENT_COUNT] = int(coerced)

    raw_inv = row.get("measure_inventory")
    if isinstance(raw_inv, Mapping):
        for key in INVENTORY_MEASURE_KEYS:
            if key in raw_inv and raw_inv[key] is not None:
                inv[key] = _coerce_present_number(raw_inv[key])
        return inv

    # Legacy flattened fields (None = missing; 0 preserved if present).
    legacy_map = {
        SOURCE_NET_VOLUME: "net_volume",
        SOURCE_GROSS_VOLUME: "gross_volume",
        SOURCE_NET_AREA: "net_area",
        SOURCE_GROSS_AREA: "gross_area",
        SOURCE_LENGTH: "length",
    }
    for src, field in legacy_map.items():
        if field in row and row[field] is not None:
            inv[src] = _coerce_present_number(row[field])
    return inv


class MeasurementResolverService:
    """Per-request resolver wrapper (project context optional for logging)."""

    def __init__(self, project: Any | None = None) -> None:
        self.project = project

    def resolve(
        self,
        *,
        measurement_type: str,
        inventory: Mapping[str, Any],
        selected_source: str | None = None,
        measurement_target_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve and return a dict including optional target key for callers."""
        result = resolve_measurement(
            measurement_type=measurement_type,
            inventory=inventory,
            selected_source=selected_source,
        )
        payload = result.as_dict()
        if measurement_target_key:
            payload["measurement_target_key"] = measurement_target_key
        return payload
