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

import numpy as np

from ifc_processor.models import IFCEntity, IFCFile

from ..models import Task, TaskEntityBinding
from .linker import _read_property

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 0 — non-physical activity pre-filter
# ---------------------------------------------------------------------------

_NON_PHYSICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "submittal",
        "approval",
        "rfi",
        "meeting",
        "inspection",
        "permit",
        "review",
        "procurement",
        "delivery",
        "mobilization",
        "demobilization",
        "testing",
        "commissioning",
        "handover",
        "closeout",
        "warranty",
        "training",
        "admin",
    }
)

_SKIP_ACTIVITY_TYPES: frozenset[str] = frozenset(
    {
        "wbs summary",
        "milestone",
        "loe",
        "hammock",
    }
)

# Confidence thresholds
_MAX_HEURISTIC = 50  # cap on entities returned by Layer 3
_EMBED_HIGH = 0.82  # embedding auto-accept floor
_EMBED_LOW = 0.65  # embedding minimum (below → skip)
_HEURISTIC_TYPE_ONLY = 0.50  # Layer 3: type keyword match only
_HEURISTIC_ZONE_MATCH = 0.75  # Layer 3: type + zone/level spatial match
_HEURISTIC_FULL_MATCH = 0.85  # Layer 3: type + zone + trade context → auto-accept

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


# ---------------------------------------------------------------------------
# Stage auto-detection
# ---------------------------------------------------------------------------

_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "substructure": (
        "foundation",
        "footing",
        "pile",
        "piling",
        "excavat",
        "basement",
        "grade slab",
        "raft slab",
    ),
    "structure": (
        "column",
        "beam",
        "slab",
        "structural",
        "formwork",
        "rebar",
        "shear wall",
        "concrete pour",
        "steel erect",
        "rc frame",
    ),
    "envelope": (
        "facade",
        "cladding",
        "curtain wall",
        "roof",
        "waterproof",
        "glazing",
        "exterior wall",
    ),
    "mep": (
        "mep",
        "mechanical",
        "electrical",
        "plumbing",
        "hvac",
        "ductwork",
        "pipework",
        "cable tray",
        "sprinkler",
        "drainage",
    ),
    "finishes": (
        "finish",
        "plaster",
        "plasterboard",
        "paint",
        "tiling",
        "tile ",
        "flooring",
        "ceiling",
        "partition",
        "screed",
    ),
    "external": (
        "landscape",
        "hardscape",
        "paving",
        "road",
        "car park",
        "fence",
        "external works",
        "soft landscape",
    ),
}

_SUB_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Substructure
    "excavation": ("excavat", "earthwork", "bulk cut", "trench"),
    "blinding": ("blinding", "blind concrete", "lean concrete", "lean mix"),
    "waterproofing": ("waterproof", "tanking", "membrane", "damp proof"),
    "backfill": ("backfill", "back fill", "filling", "compaction"),
    "piling": ("bored pile", "driven pile", "sheet pile", "piling"),
    # Structure
    "formwork": ("formwork", "shuttering", "form work", "falsework"),
    "rebar": ("rebar", "reinforcement", "steel fixing", "bar bending", "brc mesh"),
    "concrete": ("concrete pour", "casting", "in-situ concrete", "rcc pour"),
    "stripping": ("strip form", "de-shutter", "deshutter", "form stripping"),
    "steel_erection": ("steel erection", "structural steel", "erect steel", "steel frame"),
    "precast": ("precast", "pre-cast", "prefab"),
    # Envelope
    "blockwork": ("blockwork", "block work", "masonry", "brickwork", "brick work"),
    "cladding": ("cladding", "curtain wall", "facade panel", "rainscreen"),
    "glazing": ("glazing", "glaze", "curtain glazing", "glass panel"),
    "roofing": ("roofing", "roof cover", "roof membrane", "roof screed"),
    "insulation": ("insulation", "insulate", "thermal insul", "acoustic insul"),
    # MEP
    "electrical": ("electrical", "wiring", "conduit", "cable laying", "switchgear", "db install"),
    "plumbing": ("plumbing", "sanitary", "water supply", "drainage pipe", "soil pipe"),
    "hvac": ("hvac", "ductwork", "air handling", "chiller", "ahu install", "fan coil"),
    "firefighting": ("firefighting", "fire fighting", "fire protection", "sprinkler", "fire alarm"),
    "lv_systems": (
        "lv systems",
        "low voltage",
        "data cabling",
        "cctv",
        "access control",
        "pa system",
    ),
    # Finishes
    "plaster": ("plaster", "render", "skimming", "skim coat", "gypsum plaster"),
    "painting": ("painting", "emulsion", "primer coat", "paint finish"),
    "flooring": ("flooring", "floor finish", "floor screed", "epoxy floor"),
    "tiling": ("tiling", "tile fix", "ceramic tile", "porcelain tile", "marble tile"),
    "ceiling": ("false ceiling", "suspended ceiling", "gypsum ceiling", "ceiling board"),
    "joinery": ("joinery", "door frame", "door leaf", "skirting", "architrave", "fitted furniture"),
    # External
    "landscaping": ("landscaping", "planting", "soft landscape", "turf"),
    "paving": ("paving", "pavement", "road base", "car park surface"),
    "fencing": ("fencing", "fence install", "boundary wall", "gate install"),
    "hardscape": ("hardscape", "hard landscape", "external paving", "external concrete"),
}

_SUB_TO_PARENT_STAGE: dict[str, str] = {
    "excavation": "substructure",
    "blinding": "substructure",
    "waterproofing": "substructure",
    "backfill": "substructure",
    "piling": "substructure",
    "formwork": "structure",
    "rebar": "structure",
    "concrete": "structure",
    "stripping": "structure",
    "steel_erection": "structure",
    "precast": "structure",
    "blockwork": "envelope",
    "cladding": "envelope",
    "glazing": "envelope",
    "roofing": "envelope",
    "insulation": "envelope",
    "electrical": "mep",
    "plumbing": "mep",
    "hvac": "mep",
    "firefighting": "mep",
    "lv_systems": "mep",
    "plaster": "finishes",
    "painting": "finishes",
    "flooring": "finishes",
    "tiling": "finishes",
    "ceiling": "finishes",
    "joinery": "finishes",
    "landscaping": "external",
    "paving": "external",
    "fencing": "external",
    "hardscape": "external",
}


def detect_task_stage(task_name: str) -> str:
    """Return the Stage key matching the task name, or '' if none match."""
    name = task_name.lower()
    for stage_key, keywords in _STAGE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return stage_key
    return ""


def detect_task_sub_stage(task_name: str) -> str:
    """Return the SubStage key matching the task name, or '' if none match."""
    name = task_name.lower()
    for sub_key, keywords in _SUB_STAGE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return sub_key
    return ""


def autodetect_stages(tasks: list[Task]) -> int:
    """Detect and set stage + sub_stage for tasks that don't have them yet.

    Sub_stage is detected first; parent stage is derived from _SUB_TO_PARENT_STAGE.
    Falls back to detect_task_stage() when no sub_stage matches.
    Skips tasks where stage is already set (preserves overrides).
    Returns count of tasks updated.
    """
    updates: list[Task] = []
    for task in tasks:
        if task.stage:
            continue
        sub_stage = detect_task_sub_stage(task.name)
        if sub_stage:
            task.sub_stage = sub_stage
            task.stage = _SUB_TO_PARENT_STAGE[sub_stage]
            updates.append(task)
        else:
            stage = detect_task_stage(task.name)
            if stage:
                task.stage = stage
                updates.append(task)
    if updates:
        Task.objects.bulk_update(updates, ["stage", "sub_stage"])
    return len(updates)


def _is_non_physical_auto(task: Task) -> bool:
    """Return True if the task name or activity_type flags it as non-physical."""
    name_lower = task.name.lower()
    if any(kw in name_lower for kw in _NON_PHYSICAL_KEYWORDS):
        return True
    if task.activity_type and task.activity_type.strip().lower() in _SKIP_ACTIVITY_TYPES:
        return True
    return False


def _run_layer0(tasks: list[Task]) -> tuple[list[Task], list[Task]]:
    """Separate tasks into (physical, non_physical).

    Tasks with non_physical_locked=True keep their current is_non_physical value
    and are never auto-reclassified. All others are detected by keyword + activity_type
    on every pipeline run.

    Returns (physical_tasks, non_physical_tasks).
    """
    physical: list[Task] = []
    non_physical: list[Task] = []
    mark_np: list = []
    mark_physical: list = []

    for task in tasks:
        if task.non_physical_locked:
            (non_physical if task.is_non_physical else physical).append(task)
            continue
        if _is_non_physical_auto(task):
            non_physical.append(task)
            if not task.is_non_physical:
                mark_np.append(task.pk)
        else:
            physical.append(task)
            if task.is_non_physical:
                mark_physical.append(task.pk)

    if mark_np:
        Task.objects.filter(pk__in=mark_np).update(is_non_physical=True)
    if mark_physical:
        Task.objects.filter(pk__in=mark_physical).update(is_non_physical=False)

    return physical, non_physical


def analyse_schedule_context(tasks: list[Task]) -> dict:
    """Use LLM to extract project patterns from a task sample.

    Returns a context dict used by Layer 3 to improve match quality.
    Returns {} on any failure — run_autolink() continues without context.
    """
    import json

    from core.llm import get_llm

    if not tasks:
        return {}

    sample = sorted(tasks[:150], key=lambda t: t.activity_code or "")
    task_sample = "\n".join(f"{t.activity_code or '—'} | {t.name}" for t in sample)
    prompt = (
        "You are a construction project analyst.\n"
        "Analyze these schedule tasks and extract patterns.\n"
        "Return ONLY valid JSON, no other text.\n\n"
        f"Tasks (activity_code | name):\n{task_sample}\n\n"
        "Return this exact JSON structure:\n"
        "{\n"
        '  "id_patterns": ["pattern description"],\n'
        '  "zones": ["B01", "B02"],\n'
        '  "levels": ["Basement 01", "Ground Floor"],\n'
        '  "trades": {"keyword": "ifc_type_hint"},\n'
        '  "non_physical_prefixes": ["GENDA", "SCCAS"],\n'
        '  "physical_prefixes": ["B01ZI", "FNDZ1"],\n'
        '  "template_tasks": ["task name pattern that repeats per zone"]\n'
        "}"
    )
    try:
        llm = get_llm(purpose="ask", temperature=0.1, format_json=True)
        response = llm.invoke(prompt)
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as exc:
        logger.warning("analyse_schedule_context failed: %s", exc)
        return {}


def run_autolink(project, ifc_param_name: str | None = None) -> dict:
    """Run the 5-layer auto-link pipeline for all tasks in a project.

    Returns:
        {total_tasks, linked_exact, linked_normalized, linked_heuristic,
         linked_embedding, unlinked, needs_review}
    """
    all_tasks = list(Task.objects.filter(project=project))
    if not all_tasks:
        return _empty_summary(0)

    # ------------------------------------------------------------------ #
    # Layer 0 — pre-filter non-physical activities                        #
    # ------------------------------------------------------------------ #
    tasks, non_physical_tasks = _run_layer0(all_tasks)
    excluded = len(non_physical_tasks)

    # Detect construction stages for physical tasks (skips already-set values)
    autodetect_stages(tasks)

    if not tasks:
        return {
            **_empty_summary(0),
            "total_tasks": len(all_tasks),
            "excluded_non_physical": excluded,
        }

    ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
    entity_list = list(
        IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
            "id",
            "global_id",
            "ifc_type",
            "name",
            "properties",
            "embedding",
            "spatial_container",
        )
    )
    if not entity_list:
        return {**_empty_summary(len(tasks)), "excluded_non_physical": excluded}

    # Clear stale bindings for physical tasks only
    TaskEntityBinding.objects.filter(task__in=tasks).delete()

    # Analyze schedule patterns — graceful no-op on LLM failure
    context = analyse_schedule_context(tasks)

    # Auto-discover IFC property key when none provided
    if not ifc_param_name:
        ifc_param_name = _discover_ifc_param(entity_list, tasks)

    prop_index, norm_index = _build_prop_index(entity_list, ifc_param_name)
    type_index = _build_type_index(entity_list)
    container_names = _build_container_names(ifc_files)
    storey_elevations = _build_storey_elevations(ifc_files)

    counters: dict[str, int] = {"exact": 0, "normalized": 0, "heuristic": 0, "embedding": 0}
    needs_review_count = 0
    new_bindings: list[TaskEntityBinding] = []
    m2m_adds: dict[str, list[IFCEntity]] = {}  # task_pk (str) → entities
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
    # Layer 3 — IFC-type keyword heuristic with zone/trade context        #
    # ------------------------------------------------------------------ #
    embed_remaining: list[Task] = []
    for task in remaining:
        entities, confidence, needs_review = _run_layer3(
            task, type_index, container_names, storey_elevations, context
        )
        if entities:
            _record(task, entities, confidence, "heuristic", needs_review, new_bindings, m2m_adds)
            counters["heuristic"] += 1
            if needs_review:
                needs_review_count += 1
        else:
            embed_remaining.append(task)

    # ------------------------------------------------------------------ #
    # Layer 4 — embedding cosine similarity (batched)                     #
    # FIX 1: one embed_documents() call for all tasks instead of N calls  #
    # FIX 2: one entity fetch + numpy matrix multiply instead of N queries #
    # ------------------------------------------------------------------ #
    if embed_remaining:
        try:
            from embeddings.services.embedding_service import EmbeddingService

            task_vecs = EmbeddingService().embed_documents([t.name for t in embed_remaining])
        except Exception as exc:
            logger.warning("Batch embed failed, skipping Layer 4: %s", exc)
            task_vecs = []

        if task_vecs:
            embed_entities = list(
                IFCEntity.objects.filter(ifc_file__in=ifc_files, embedding__isnull=False).only(
                    "id", "global_id", "ifc_type", "name", "embedding"
                )
            )
            if embed_entities:
                ent_mat = np.array([e.embedding for e in embed_entities], dtype=np.float32)
                ent_norms = np.linalg.norm(ent_mat, axis=1, keepdims=True)
                ent_norms[ent_norms == 0] = 1.0
                ent_mat /= ent_norms

                for task, vec in zip(embed_remaining, task_vecs):
                    if not vec:
                        continue
                    tv = np.array(vec, dtype=np.float32)
                    n = float(np.linalg.norm(tv))
                    if n == 0:
                        continue
                    sims = ent_mat @ (tv / n)
                    best_idx = int(np.argmax(sims))
                    confidence = round(float(sims[best_idx]), 4)
                    if confidence < _EMBED_LOW:
                        continue
                    entity = embed_entities[best_idx]
                    review = confidence < _EMBED_HIGH
                    _record(task, [entity], confidence, "embedding", review, new_bindings, m2m_adds)
                    counters["embedding"] += 1
                    if review:
                        needs_review_count += 1

    # ------------------------------------------------------------------ #
    # Persist                                                             #
    # ------------------------------------------------------------------ #
    TaskEntityBinding.objects.bulk_create(new_bindings, ignore_conflicts=True)

    # FIX 3: one bulk_create instead of N individual M2M .add() calls
    ThroughModel = Task.ifc_entities.through
    through_rows = [
        ThroughModel(task_id=task.pk, ifcentity_id=entity.pk)
        for task in tasks
        if (adds := m2m_adds.get(str(task.pk)))
        for entity in adds
    ]
    if through_rows:
        ThroughModel.objects.bulk_create(through_rows, ignore_conflicts=True)

    total_linked = sum(counters.values())
    return {
        "total_tasks": len(all_tasks),
        "linked_exact": counters["exact"],
        "linked_normalized": counters["normalized"],
        "linked_heuristic": counters["heuristic"],
        "linked_embedding": counters["embedding"],
        "unlinked": len(tasks) - total_linked,
        "needs_review": needs_review_count,
        "excluded_non_physical": excluded,
    }


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def _build_prop_index(
    entities: list[IFCEntity], param_name: str | None
) -> tuple[dict[str, list[IFCEntity]], dict[str, list[IFCEntity]]]:
    """Return (exact_index, normalized_index) keyed by entity property value."""
    if not param_name:
        return {}, {}
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

    storeys = IFCSpatialElement.objects.filter(
        ifc_file__in=ifc_files,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
    ).select_related("entity")
    return {str(s.pk): (s.entity.name or "").lower() for s in storeys}


def _build_storey_elevations(ifc_files) -> dict[float, str]:
    """Return {elevation: lowercase_storey_name} for all completed building storeys."""
    from ifc_processor.models import IFCSpatialElement

    storeys = IFCSpatialElement.objects.filter(
        ifc_file__in=ifc_files,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
    ).select_related("entity")
    result: dict[float, str] = {}
    for s in storeys:
        if s.elevation is not None:
            result[float(s.elevation)] = (s.entity.name or "").lower()
    return result


def _discover_ifc_param(entities: list[IFCEntity], tasks: list[Task]) -> str | None:
    """Scan IFC property keys to find the one whose values best match task activity codes.

    Returns the property-name suffix with the highest hit rate, or None if no match
    reaches the 5% threshold on the sampled entities.
    """
    if not entities or not tasks:
        return None
    codes = {t.activity_code.strip() for t in tasks if t.activity_code}
    if not codes:
        return None

    sample = entities[:200]
    key_hits: dict[str, int] = {}
    for entity in sample:
        for key, val in (entity.properties or {}).items():
            if key.lower().endswith(".id"):
                continue
            if val is not None and str(val).strip() in codes:
                key_hits[key] = key_hits.get(key, 0) + 1

    if not key_hits:
        return None

    best_key = max(key_hits, key=lambda k: key_hits[k])
    if key_hits[best_key] < max(1, len(sample) * 0.05):
        return None

    dot = best_key.rfind(".")
    return best_key[dot + 1 :].strip() if dot != -1 else best_key


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


def _infer_entity_level(entity: IFCEntity, container_names: dict[str, str]) -> str | None:
    """Return the lowercase storey name for an entity via its spatial container index."""
    if entity.spatial_container_id is None:
        return None
    return container_names.get(str(entity.spatial_container_id))


def _run_layer3(
    task: Task,
    type_index: dict[str, list[IFCEntity]],
    container_names: dict[str, str],
    storey_elevations: dict[float, str],
    context: dict,
) -> tuple[list[IFCEntity], float, bool]:
    """Heuristic match with variable confidence scoring.

    Resolves IFC type from built-in keywords then LLM-extracted context trades.
    Returns (entities, confidence, needs_review).

    Confidence tiers:
      0.50  type keyword match only          → needs_review=True
      0.75  type + zone/level match          → needs_review=True
      0.85  type + zone + trade context hit  → needs_review=False (auto-accept)
    """
    name_lower = task.name.lower()
    context_trades: dict[str, str] = (context.get("trades") or {}) if context else {}

    ifc_type: str | None = None
    from_context_trade = False
    for word in re.findall(r"\w+", name_lower):
        if word in _IFC_KEYWORDS:
            ifc_type = _IFC_KEYWORDS[word]
            break
        if word in context_trades:
            ifc_type = context_trades[word]
            from_context_trade = True
            break

    if not ifc_type:
        return [], 0.0, False

    candidates = type_index.get(ifc_type)
    if not candidates:
        return [], 0.0, False

    level_hint = _extract_level_hint(task.name)
    zone_match = False
    if level_hint and container_names:
        spatial = [
            e for e in candidates if level_hint in (_infer_entity_level(e, container_names) or "")
        ]
        if spatial:
            candidates = spatial
            zone_match = True

    if zone_match and from_context_trade:
        return candidates[:_MAX_HEURISTIC], _HEURISTIC_FULL_MATCH, False
    if zone_match:
        return candidates[:_MAX_HEURISTIC], _HEURISTIC_ZONE_MATCH, True
    return candidates[:_MAX_HEURISTIC], _HEURISTIC_TYPE_ONLY, True


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


def _empty_summary(total: int) -> dict:
    return {
        "total_tasks": total,
        "linked_exact": 0,
        "linked_normalized": 0,
        "linked_heuristic": 0,
        "linked_embedding": 0,
        "unlinked": total,
        "needs_review": 0,
        "excluded_non_physical": 0,
    }
