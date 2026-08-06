# benchmarks/ifc_parser/reference.py
"""Parse engine_web-ifc/benchmark.md into reference entity/mesh counts.

Used for the correctness cross-check: entity counts should match web-ifc
exactly (both count total STEP instances); mesh counts are informational
only (different tessellation engines and instancing strategies).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE = REPO_ROOT / "engine_web-ifc" / "benchmark.md"

# Fractional mesh delta below which no note is added to the Markdown table.
MESH_DELTA_SILENT_THRESHOLD = 0.10


@dataclass(frozen=True)
class ReferenceCounts:
    """Published web-ifc numbers for one corpus file."""

    entities: int
    meshes: int


def _stem_key(filename: str) -> str:
    """Normalize a filename to a comparison key: lowercase stem, no dirs/ext.

    Handles 'tests/ifcfiles/public/X.ifc' vs 'X.ifc' vs 'X.ifczip'.
    """
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return stem.lower()


def load_reference(path: Path = DEFAULT_REFERENCE) -> dict[str, ReferenceCounts]:
    """Parse benchmark.md rows into {stem_key: ReferenceCounts}.

    Tolerates the file's format quirk: data rows after the first lack the
    leading '|'. Returns an empty dict (with a warning) if the file is
    missing — the checkout is gitignored and may be absent.
    """
    if not path.exists():
        logger.warning("Reference file not found (%s) — skipping cross-checks", path)
        return {}

    reference: dict[str, ReferenceCounts] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        cells = [c for c in cells if c != ""]
        # Data row shape: filename, size, open, execute, entities, meshes, geometries, errors
        if len(cells) < 8 or not cells[0].lower().endswith((".ifc", ".ifczip")):
            continue
        try:
            entities = int(cells[4])
            meshes = int(cells[5])
        except ValueError:
            continue
        reference[_stem_key(cells[0])] = ReferenceCounts(entities=entities, meshes=meshes)

    logger.info("Loaded %d reference rows from %s", len(reference), path)
    return reference


def cross_check(
    filename: str,
    entity_count: int,
    mesh_count: int,
    reference: dict[str, ReferenceCounts],
) -> tuple[list[str], ReferenceCounts | None]:
    """Compare measured counts against the reference table.

    Returns (error_notes, reference_row). Entity mismatches are flagged as
    potential correctness bugs; mesh deltas are informational and only
    noted beyond MESH_DELTA_SILENT_THRESHOLD.
    """
    ref = reference.get(_stem_key(filename))
    if ref is None:
        return [], None

    notes: list[str] = []
    if entity_count != ref.entities:
        notes.append(f"ENTITY_MISMATCH (castor={entity_count}, webifc={ref.entities})")
    if ref.meshes > 0:
        delta = (mesh_count - ref.meshes) / ref.meshes
        if abs(delta) > MESH_DELTA_SILENT_THRESHOLD:
            notes.append(f"MESH_DELTA {delta:+.0%} (castor={mesh_count}, webifc={ref.meshes})")
    return notes, ref
