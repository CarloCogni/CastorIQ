# islam/scheduling/services/autolink.py
"""4-layer schedule Task → IFC entity auto-link pipeline.

Layer order (a task exits at the first layer that produces a match):
  1. Exact property match       confidence 1.00  auto-accept
  2. Normalized property match  confidence 0.95  auto-accept
  3. IFC-type keyword heuristic confidence 0.70  needs review
  4. Embedding cosine distance  ≥ 0.82 auto-accept, 0.65–0.81 needs review

"Auto-accept" means the entity is added to task.ifc_entities M2M immediately.
"Needs review" means a TaskEntityBinding is created with needs_review=True
but the M2M link is NOT written — Phase 3 Review UI handles acceptance.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from ifc_processor.models import IFCEntity, IFCFile

from ..models import Task, TaskEntityBinding
from .linker import _read_property

logger = logging.getLogger(__name__)

# Confidence thresholds
_HEURISTIC_CONF = 0.70
_MAX_HEURISTIC = 50   # cap on entities returned by Layer 3
_EMBED_HIGH = 0.82   # embedding auto-accept floor
_EMBED_LOW = 0.65    # embedding minimum (below → skip)

# Keyword → IFC entity type map (all keys lowercase, no spaces)
_IFC_KEYWORDS: dict[str, str] = {
    "wall": "IfcWall",
    "slab": "IfcSlab",
    "beam": "IfcBeam",
    "column": "IfcColumn",
    "door": "IfcDoor",
    "window": "IfcWindow",
    "stair": "IfcStair",
    "staircase": "IfcStair",
    "ramp": "IfcRamp",
    "roof": "IfcRoof",
    "ceiling": "IfcCovering",
    "floor": "IfcSlab",
    "foundation": "IfcFooting",
    "footing": "IfcFooting",
    "pile": "IfcPile",
    "pipe": "IfcPipeSegment",
    "duct": "IfcDuctSegment",
    "cable": "IfcCableSegment",
    "fitting": "IfcPipeFitting",
    "furniture": "IfcFurnishingElement",
    "space": "IfcSpace",
    "opening": "IfcOpeningElement",
    "plate": "IfcPlate",
    "member": "IfcMember",
    "railing": "IfcRailing",
    "curtain": "IfcCurtainWall",
    "excavation": "IfcEarthworksCut",
    "fill": "IfcEarthworksFill",
}


def run_autolink(project, ifc_param_name: str = "Activity ID") -> dict:
    """Run the 4-layer auto-link pipeline for all tasks in a project.

    Returns:
        {total_tasks, linked_exact, linked_normalized, linked_heuristic,
         linked_embedding, unlinked, needs_review}
    """
    tasks = list(Task.objects.filter(project=project))
    if not tasks:
        return _empty_summary(0)

    ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
    entity_list = list(
        IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
            "id", "global_id", "ifc_type", "name", "properties", "embedding",
            "spatial_container",
        )
    )
    if not entity_list:
        return _empty_summary(len(tasks))

    # Clear stale bindings so each run is idempotent
    TaskEntityBinding.objects.filter(task__project=project).delete()

    prop_index, norm_index = _build_prop_index(entity_list, ifc_param_name)
    type_index = _build_type_index(entity_list)
    container_names = _build_container_names(ifc_files)

    counters: dict[str, int] = {"exact": 0, "normalized": 0, "heuristic": 0, "embedding": 0}
    needs_review_count = 0
    new_bindings: list[TaskEntityBinding] = []
    m2m_adds: dict[str, list[IFCEntity]] = {}   # task_pk (str) → entities
    remaining: list[Task] = []

    # ------------------------------------------------------------------ #
    # Layers 1 & 2 — property value match                                 #
    # ------------------------------------------------------------------ #
    for task in tasks:
        code = (task.activity_code or "").strip()
        if not code:
            remaining.append(task)
            continue

        matched = prop_index.get(code)
        if matched:
            _record(task, matched, 1.0, "exact", False, new_bindings, m2m_adds)
            counters["exact"] += 1
            continue

        matched = norm_index.get(_normalize(code))
        if matched:
            _record(task, matched, 0.95, "normalized", False, new_bindings, m2m_adds)
            counters["normalized"] += 1
            continue

        remaining.append(task)

    # ------------------------------------------------------------------ #
    # Layer 3 — IFC-type keyword heuristic                                #
    # ------------------------------------------------------------------ #
    embed_remaining: list[Task] = []
    for task in remaining:
        entities = _heuristic_match(task, type_index, container_names)
        if entities:
            _record(task, entities, _HEURISTIC_CONF, "heuristic", True, new_bindings, m2m_adds)
            counters["heuristic"] += 1
            needs_review_count += 1
        else:
            embed_remaining.append(task)

    # ------------------------------------------------------------------ #
    # Layer 4 — embedding cosine similarity                               #
    # ------------------------------------------------------------------ #
    if embed_remaining:
        matched_gids = {b.entity_global_id for b in new_bindings}
        embed_qs = (
            IFCEntity.objects.filter(ifc_file__in=ifc_files, embedding__isnull=False)
            .exclude(global_id__in=matched_gids)
            .only("id", "global_id", "ifc_type", "name", "embedding")
        )
        if embed_qs.exists():
            for task in embed_remaining:
                result = _embedding_match(task, embed_qs)
                if result is None:
                    continue
                entity, confidence = result
                review = confidence < _EMBED_HIGH
                _record(task, [entity], confidence, "embedding", review, new_bindings, m2m_adds)
                counters["embedding"] += 1
                if review:
                    needs_review_count += 1

    # ------------------------------------------------------------------ #
    # Persist                                                             #
    # ------------------------------------------------------------------ #
    TaskEntityBinding.objects.bulk_create(new_bindings, ignore_conflicts=True)

    # Apply M2M only for auto-accepted bindings (needs_review=False)
    for task in tasks:
        adds = m2m_adds.get(str(task.pk))
        if adds:
            task.ifc_entities.add(*adds)

    total_linked = sum(counters.values())
    return {
        "total_tasks": len(tasks),
        "linked_exact": counters["exact"],
        "linked_normalized": counters["normalized"],
        "linked_heuristic": counters["heuristic"],
        "linked_embedding": counters["embedding"],
        "unlinked": len(tasks) - total_linked,
        "needs_review": needs_review_count,
    }


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _build_prop_index(
    entities: list[IFCEntity], param_name: str
) -> tuple[dict[str, list[IFCEntity]], dict[str, list[IFCEntity]]]:
    """Return (exact_index, normalized_index) keyed by entity property value."""
    exact: dict[str, list[IFCEntity]] = {}
    norm: dict[str, list[IFCEntity]] = {}
    for entity in entities:
        val = _read_property(entity, param_name)
        if not val:
            continue
        exact.setdefault(val, []).append(entity)
        norm.setdefault(_normalize(val), []).append(entity)
    return exact, norm


def _build_type_index(entities: list[IFCEntity]) -> dict[str, list[IFCEntity]]:
    """Return ifc_type → [entities] index."""
    idx: dict[str, list[IFCEntity]] = {}
    for entity in entities:
        if entity.ifc_type:
            idx.setdefault(entity.ifc_type, []).append(entity)
    return idx


def _build_container_names(ifc_files) -> dict[str, str]:
    """Return {str(IFCSpatialElement.pk): lowercase storey name} for building storeys."""
    from ifc_processor.models import IFCSpatialElement

    storeys = (
        IFCSpatialElement.objects
        .filter(
            ifc_file__in=ifc_files,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        )
        .select_related("entity")
    )
    return {str(s.pk): (s.entity.name or "").lower() for s in storeys}


# ---------------------------------------------------------------------------
# Per-layer match helpers
# ---------------------------------------------------------------------------

def _record(
    task: Task,
    entities: list[IFCEntity],
    confidence: float,
    method: str,
    needs_review: bool,
    bindings: list[TaskEntityBinding],
    m2m: dict[str, list[IFCEntity]],
) -> None:
    """Append binding objects and, if auto-accepted, queue M2M writes."""
    for entity in entities:
        bindings.append(
            TaskEntityBinding(
                task=task,
                entity_global_id=entity.global_id,
                confidence=confidence,
                link_method=method,
                needs_review=needs_review,
            )
        )
    if not needs_review:
        m2m.setdefault(str(task.pk), []).extend(entities)


def _heuristic_match(
    task: Task,
    type_index: dict[str, list[IFCEntity]],
    container_names: dict[str, str],
) -> list[IFCEntity]:
    """Return up to _MAX_HEURISTIC entities matching the task's IFC-type keyword.

    If the task name contains a level/floor hint the candidates are first narrowed
    to entities whose spatial_container storey name contains that hint.  Falls back
    to the full type-matched list when the hint yields no spatial matches.
    """
    words = re.findall(r"\w+", task.name.lower())
    for word in words:
        ifc_type = _IFC_KEYWORDS.get(word)
        if not ifc_type:
            continue
        candidates = type_index.get(ifc_type)
        if not candidates:
            continue

        level_hint = _extract_level_hint(task.name)
        if level_hint and container_names:
            spatial = [
                e for e in candidates
                if e.spatial_container_id is not None
                and level_hint in container_names.get(str(e.spatial_container_id), "")
            ]
            if spatial:
                return spatial[:_MAX_HEURISTIC]
            # Hint present but no spatial match — return unfiltered list

        return candidates[:_MAX_HEURISTIC]
    return []


def _embedding_match(task: Task, entities_qs) -> tuple[IFCEntity, float] | None:
    """Return (entity, confidence) for the closest embedding match above _EMBED_LOW."""
    from pgvector.django import CosineDistance

    try:
        from embeddings.services.embedding_service import EmbeddingService
        vec = EmbeddingService().embed_query(task.name)
    except Exception as exc:
        logger.warning("Embed failed for task '%s': %s", task.name, exc)
        return None

    max_dist = round(1.0 - _EMBED_LOW, 4)   # 0.35
    match = (
        entities_qs
        .annotate(dist=CosineDistance("embedding", vec))
        .filter(dist__lte=max_dist)
        .order_by("dist")
        .first()
    )
    if match is None:
        return None

    return match, round(1.0 - float(match.dist), 4)


# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------

def _extract_level_hint(task_name: str) -> str | None:
    """Return a normalised lowercase token to match against storey names.

    "Level 3 Walls"      → "3"       "Ground Floor Columns" → "ground"
    "B1 Slabs"           → "b1"      "L01 Beams"            → "1"
    "Install all walls"  → None
    """
    name = task_name.lower()
    # "level N" / "floor N" / "storey N" / "story N" / "lvl N"
    m = re.search(r"\b(?:level|floor|storey|story|lvl)\s*(\w+)", name)
    if m:
        token = re.sub(r"(st|nd|rd|th)$", "", m.group(1))  # strip ordinal suffix
        return token or None
    # Named ground levels
    for kw in ("ground", "basement", "roof", "podium"):
        if re.search(rf"\b{kw}\b", name):
            return kw
    # "B1", "B2" — basement shorthand
    m = re.search(r"\bb(\d+)\b", name)
    if m:
        return "b" + m.group(1)
    # "L1", "L01" — level shorthand
    m = re.search(r"\bl(\d+)\b", name)
    if m:
        return m.group(1).lstrip("0") or "0"
    return None


def _normalize(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s\-_]+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _empty_summary(total: int) -> dict:
    return {
        "total_tasks": total,
        "linked_exact": 0,
        "linked_normalized": 0,
        "linked_heuristic": 0,
        "linked_embedding": 0,
        "unlinked": total,
        "needs_review": 0,
    }
