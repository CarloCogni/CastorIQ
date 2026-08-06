# chat/services/ask_benchmark/ground_truth.py
"""Compute reference answers straight from the IFC file with IfcOpenShell.

Deliberately independent of Castor's own parser: the benchmark must not
inherit the pipeline's blind spots. ``ifcopenshell.open()`` on the fixture
plus ``by_type`` (subtype-inclusive) is the whole implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell

logger = logging.getLogger(__name__)

# Types the counting questions may reference. Subtype-inclusive counts.
COUNTED_TYPES: tuple[str, ...] = (
    "IfcDoor",
    "IfcWindow",
    "IfcWall",
    "IfcSlab",
    "IfcSpace",
    "IfcBuildingStorey",
)


@dataclass(frozen=True)
class GroundTruth:
    """Reference facts for one fixture model."""

    schema: str
    counts: dict[str, int] = field(default_factory=dict)
    storey_names: tuple[str, ...] = ()
    material_names: tuple[str, ...] = ()
    space_names: tuple[str, ...] = ()


def _names(elements, *, min_length: int = 2) -> tuple[str, ...]:
    """Distinct non-empty ``Name`` values, insertion-ordered."""
    seen: dict[str, None] = {}
    for element in elements:
        name = (getattr(element, "Name", None) or "").strip()
        if len(name) >= min_length:
            seen.setdefault(name)
    return tuple(seen)


def compute_ground_truth(path: Path) -> GroundTruth:
    """Open the fixture and derive the reference facts the scorers need."""
    model = ifcopenshell.open(str(path))

    counts = {ifc_type: len(model.by_type(ifc_type)) for ifc_type in COUNTED_TYPES}
    ground = GroundTruth(
        schema=model.schema,
        counts=counts,
        storey_names=_names(model.by_type("IfcBuildingStorey")),
        material_names=_names(model.by_type("IfcMaterial"), min_length=3),
        space_names=_names(model.by_type("IfcSpace")),
    )
    logger.info(
        "Ground truth for %s: schema=%s counts=%s storeys=%d materials=%d",
        path.name,
        ground.schema,
        counts,
        len(ground.storey_names),
        len(ground.material_names),
    )
    return ground
