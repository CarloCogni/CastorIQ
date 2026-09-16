# writeback/services/conflict_scan_service.py
"""
Conflict Scan Engine — document-first IFC-vs-document contradiction detector.

Strategy: instead of iterating all IFC entities (entity-first), we start from
document requirement chunks and find the IFC entities they constrain (document-first).
This drastically reduces LLM calls by only scanning entities actually referenced in
specification requirements.

Pipeline:
    1. Gather requirement chunks — filter DocumentChunks by AEC compliance keywords
    2. Build entity-chunk map — three passes per requirement chunk, union of:
         a. reference pass — entities whose model reference or name segment the
            chunk quotes verbatim ("Wall-Ext_102Bwk-75Ins-100LBlk-12P");
         b. label pass — every entity of the element classes the chunk names
            ("external walls", "windows"), capped per class, when the chunk
            also mentions a property;
         c. embedding pass — top-K nearest entities by cosine distance.
       (a) and (b) are lookups, not retrieval: they exist because per-chunk
       top-K is a lottery among near-identical embeddings (three identical
       walls crowd each other out) and left whole conflict cases unreachable
       (docs/evaluation/2026-08-30-rav-benchmark.md).
    3. LLM compare — one call per entity that has at least one relevant chunk
    4. Verify — the current value a finding claims is checked against the
       entity's indexed properties before anything is stored; a value the
       entity does not carry is stored as "(not set)", never as invented
    5. Persist — upsert Conflict records with content_hash deduplication

Design mirrors guardian_service.py: same LLM factory, same chunk formatting,
same JSON parse safety. The difference is directionality — the Guardian checks a
*proposed* change, the Scanner checks *existing* entity state.
"""

import concurrent.futures
import hashlib
import json
import logging
import re
import time
from collections import defaultdict

import httpcore
import httpx
from django.db.models import Q
from langchain_core.messages import HumanMessage, SystemMessage
from pgvector.django import CosineDistance

from core.llm import get_llm, resolve_model_name, safe_invoke
from documents.models import DocumentChunk
from ifc_processor.models import IFCEntity
from writeback.models import Conflict, ScanRun
from writeback.services.emitters import CancellationError, NullEmitter
from writeback.services.generator import NUM_CTX
from writeback.services.property_aliases import PROPERTY_ALIASES, canonical_property, squash

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# AEC compliance requirement keywords for document chunk filtering
# ────────────────────────────────────────────────────────────

REQUIREMENT_KEYWORDS = [
    "shall",
    "must",
    "required",
    "minimum",
    "maximum",
    "fire rating",
    "acoustic",
    "thermal",
    "u-value",
    "resistance",
    "EI",
    "REI",
    " R ",
    " E ",
    "class",
    "grade",
    "performance",
    "load-bearing",
    "structural",
    "compliance",
    "standard",
    "regulation",
]

# Minimum LLM confidence to store a finding
CONFIDENCE_THRESHOLD = 0.7

# ────────────────────────────────────────────────────────────
# Element-type gate: maps the LLM's `applies_to_element` label
# (short English word extracted from the requirement text) to
# the set of IFC classes that satisfy it. Findings whose label
# does not match the entity's IFC type are dropped before
# persistence — prevents wall requirements being pinned on
# beams, columns, etc. by a loose vector recall.
# ────────────────────────────────────────────────────────────

ELEMENT_TYPE_MAP: dict[str, set[str]] = {
    "wall": {"IfcWall", "IfcWallStandardCase", "IfcCurtainWall"},
    "door": {"IfcDoor"},
    "window": {"IfcWindow"},
    "slab": {"IfcSlab"},
    "roof": {"IfcRoof", "IfcSlab"},
    "beam": {"IfcBeam"},
    "column": {"IfcColumn"},
    "stair": {"IfcStair", "IfcStairFlight"},
    "ramp": {"IfcRamp", "IfcRampFlight"},
    "railing": {"IfcRailing"},
    "covering": {"IfcCovering"},
    "furniture": {"IfcFurnishingElement", "IfcFurniture"},
    "space": {"IfcSpace"},
    "zone": {"IfcZone"},
    "pipe": {"IfcPipeSegment", "IfcPipeFitting"},
    "duct": {"IfcDuctSegment", "IfcDuctFitting"},
}

# Labels that explicitly bypass the gate (generic/applies-to-all requirements).
ELEMENT_TYPE_ANY_LABELS = {"any", "all", "", "element", "any element"}

# ────────────────────────────────────────────────────────────
# Entity-first retrieval: the element nouns a requirement sentence
# uses and the IFC classes each one names. Deliberately narrower
# than ELEMENT_TYPE_MAP (the post-LLM gate): "wall" here does not
# pull in curtain walls — a requirement that means them says so.
# Plurals are matched by the regex in _label_types.
# ────────────────────────────────────────────────────────────

ENTITY_FIRST_LABELS: dict[str, frozenset[str]] = {
    "wall": frozenset({"IfcWall", "IfcWallStandardCase"}),
    "partition": frozenset({"IfcWall", "IfcWallStandardCase"}),
    "curtain wall": frozenset({"IfcCurtainWall"}),
    "door": frozenset({"IfcDoor"}),
    "window": frozenset({"IfcWindow"}),
    "slab": frozenset({"IfcSlab"}),
    "floor": frozenset({"IfcSlab"}),
    "deck": frozenset({"IfcSlab"}),
    "roof": frozenset({"IfcRoof"}),
    "beam": frozenset({"IfcBeam"}),
    "column": frozenset({"IfcColumn"}),
    "stair": frozenset({"IfcStair", "IfcStairFlight"}),
    "railing": frozenset({"IfcRailing"}),
    "covering": frozenset({"IfcCovering"}),
    "ceiling": frozenset({"IfcCovering"}),
}

# A reference must read like a model type code, not a word: at least this long
# and carrying a digit, an underscore or a hyphen ("Wall-Ext_102Bwk", "1810x1210mm"),
# or a multi-word name of at least MIN_PHRASE_LENGTH ("Simple floor"). "Glazed"
# or "Floor" would match ordinary prose.
MIN_REFERENCE_LENGTH = 6
MIN_PHRASE_LENGTH = 10

# Short property aliases that need a word boundary rather than substring search
# ("rw" is inside too many words).
_SHORT_PROPERTY_RE = re.compile(r"\b(r'?w|u-?value)\b", re.IGNORECASE)
_LONG_PROPERTY_ALIASES = tuple(alias for alias in PROPERTY_ALIASES if len(alias) >= 4)

# The LLM's way of saying "the entity has no such property"; not a fabricated value.
_ABSENCE_RE = re.compile(r"absent|not set|none|null|missing|n/a|no propert", re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(non|not|no)\b|non-", re.IGNORECASE)


# ────────────────────────────────────────────────────────────
# Scanner LLM Prompts
# ────────────────────────────────────────────────────────────

SCANNER_SYSTEM_PROMPT = """\
You are a construction compliance checker.

Your task is to compare IFC building model data against technical document requirements.

## Three-step process (MANDATORY):
Step 1 — EXTRACT: List every specific requirement found in the document excerpts
         (property name + required value + the element class it targets,
         e.g. "wall", "door", "slab", "beam", "column"). Ignore vague statements.
Step 2 — GATE: Check whether the requirement APPLIES to the current IFC entity.
         The entity's IFC type is shown under "## IFC Entity · Type". A requirement
         that targets a specific element class only applies when the IFC type
         semantically matches that class:
           - "wall"   → IfcWall, IfcWallStandardCase, IfcCurtainWall
           - "door"   → IfcDoor
           - "window" → IfcWindow
           - "slab"   → IfcSlab
           - "roof"   → IfcRoof (or IfcSlab acting as roof)
           - "beam"   → IfcBeam
           - "column" → IfcColumn
           - "stair"  → IfcStair, IfcStairFlight
         If the requirement clearly targets a different element class, SKIP it —
         do NOT flag a conflict. A requirement for "external walls" is NEVER a
         conflict on an IfcBeam or IfcColumn, even if properties are missing.
         Only if the requirement is generic (applies to any element) set
         applies_to_element = "any".
Step 3 — COMPARE: For each requirement that passed the gate, look up the same
         property in the IFC entity's current properties and compare:
         - Values match or are equivalent → NOT a conflict. Skip it.
         - Values differ → flag as a conflict.
         - Property absent but entity has other properties → NOT a conflict
           (property may be optional or not applicable to this element).
         - Entity has NO properties at all (shown as "(no properties)") AND the
           requirement targets THIS element class specifically (gate passed) →
           flag as a conflict with ifc_value: "absent (no property sets)".
         - Requirement is ambiguous → NOT a conflict.

## Critical rules:
- NEVER flag a property where the IFC value already satisfies the document requirement.
- NEVER flag a requirement that targets a different element class than the IFC entity.
- ONLY flag when the IFC value is demonstrably DIFFERENT from the document requirement.
- Always include the `applies_to_element` field (lowercase, singular English noun,
  or "any" for generic requirements).
- Assign a confidence score (0.0–1.0). Only include conflicts with confidence >= 0.7.
- If no genuine conflicts exist, return {"conflicts": []}.

## Output format (JSON only, no markdown):
{
  "conflicts": [
    {
      "property_name": "FireRating",
      "ifc_value": "EI60",
      "document_value": "EI120",
      "applies_to_element": "wall",
      "title": "Fire rating below requirement",
      "description": "Wall fire rating is EI60 but specification requires EI120 for corridor partitions.",
      "severity": "critical",
      "confidence": 0.92,
      "suggested_fix": "Set FireRating from EI60 to EI120 for house - outer wall - house front right — required for corridor fire separation (fire safety report, p.1)",
      "source_chunk_index": 0
    }
  ]
}

For suggested_fix: be specific — include the property name, current IFC value, required value,
the entity name, and a short reason from the document. Format:
"Set [Property] from [current] to [required] for [entity name] — [reason from document]"

Severity guide:
  critical — fire safety, structural integrity, life safety
  high     — energy performance, acoustic, accessibility compliance
  medium   — materials, finishes, stated dimensions
  low      — labeling, naming conventions, non-critical metadata
"""

SCANNER_USER_TEMPLATE = """\
## IFC Entity
- Type: {ifc_type}
- Name: {name}
- Location: {location}

### Current Properties
{properties}

## Document Excerpts (most relevant first)
{doc_excerpts}

## Task
Apply the two-step process: first extract all specific requirements from the excerpts,
then compare each against the IFC entity properties above.
Return JSON only. NEVER flag a property whose IFC value already matches the requirement.
"""


class ConflictScanService:
    """
    Proactive conflict scanner: document-first strategy.

    Gathers AEC requirement chunks from documents, finds the IFC entities they
    constrain via vector similarity, then uses the LLM to detect genuine value
    contradictions. False-positive protection via two-step LLM prompt + confidence
    threshold filtering.

    Usage:
        svc = ConflictScanService(project, user)
        stats = svc.full_scan(emitter=NullEmitter())
    """

    ENTITY_RELEVANCE_THRESHOLD = 0.45
    ENTITY_TOP_K = 5  # IFC entities to retrieve per requirement chunk (embedding pass)
    ENTITY_TYPE_QUOTA = 25  # entities per IFC class per chunk (label pass)
    # Excerpts per LLM call. Ordered by pass (reference, label, embedding) then
    # cosine distance, so the chunks that name the entity come first and a
    # requirement-heavy corpus cannot push the prompt past the context window.
    MAX_CHUNKS_PER_ENTITY = 8

    # IFC types that rarely carry property requirements worth checking
    LOW_VALUE_IFC_TYPES = {
        "IfcSpace",
        "IfcBuildingElementProxy",
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
        "IfcProject",
        "IfcOpeningElement",
        "IfcGroup",
        "IfcZone",
    }

    # Per-entity LLM call cap. Tighter than the global OLLAMA_REQUEST_TIMEOUT
    # because the user is watching a live progress UI — a single hung entity
    # should advance the loop within 2 minutes
    SCAN_LLM_TIMEOUT_SECONDS = 120.0

    # Hard cap on findings JSON length (tokens). Stops the model from
    # rambling for minutes — typical valid scanner output fits well under
    # 1024 tokens. Combined with the wall-clock timeout below, this
    # makes runaway generation (the actual cause of the 18-minute hang
    # the user kept hitting on entity 6) impossible.
    SCAN_MAX_OUTPUT_TOKENS = 1024

    def __init__(
        self,
        project,
        user,
        skip_low_value: bool = True,
        *,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        type_gate: bool = True,
        keyword_filter: bool = True,
        entity_relevance_threshold: float | None = None,
        entity_top_k: int | None = None,
        entity_first: bool = True,
        verify_values: bool = True,
    ):
        """
        Args:
            skip_low_value: Exclude LOW_VALUE_IFC_TYPES from the entity search.
            confidence_threshold: Minimum LLM confidence to keep a finding.
            type_gate: Drop findings whose ``applies_to_element`` does not match
                the entity's IFC class (see ``_finding_applies_to_entity``).
            keyword_filter: Restrict candidate chunks to those containing an
                AEC requirement keyword. Off means every embedded chunk is a
                candidate — many more LLM calls.
            entity_relevance_threshold / entity_top_k: Override the vector
                search cutoffs. ``None`` keeps the class defaults.
            entity_first: Run the reference and label passes before the
                embedding pass (see ``_build_entity_chunk_map``). Off is the
                pre-2026-09 behaviour: embedding top-K only.
            verify_values: Check the current value a finding claims against
                the entity's indexed properties before storing it.

        The keyword-only knobs exist so the RAV benchmark can ablate each
        mitigation independently; production callers leave them at defaults.
        """
        self.project = project
        self.user = user
        self.skip_low_value = skip_low_value
        self.confidence_threshold = confidence_threshold
        self.type_gate = type_gate
        self.keyword_filter = keyword_filter
        self.entity_first = entity_first
        self.verify_values = verify_values
        self.retrieval_stats: dict[str, int] = {}
        self.value_stats: dict[str, int] = {"values_corrected": 0, "values_unset": 0}
        if entity_relevance_threshold is not None:
            self.ENTITY_RELEVANCE_THRESHOLD = entity_relevance_threshold
        if entity_top_k is not None:
            self.ENTITY_TOP_K = entity_top_k
        # `client_kwargs={"timeout": …}` is httpx's per-read timeout — it
        # only fires when the socket goes silent for that long. Ollama can
        # stream tokens slowly enough to keep the timer alive forever, so
        # this is NOT a wall-clock cap. The real cap is enforced in
        # _evaluate_entity() via ThreadPoolExecutor.result(timeout=…).
        # `num_predict` caps Ollama's output tokens at the model layer so
        # the model can't generate for 30 minutes even if the timeout
        # somehow doesn't fire.
        # `num_ctx` is the Modify context size: the scanner runs on the Modify
        # model, and a different num_ctx makes Ollama reload it. Without it a
        # prompt with several excerpts exceeds Ollama's default window and is
        # truncated from the front, which silently drops the entity's own
        # properties (the 2026-09-15 retrieval fix found this).
        self.llm = get_llm(
            user=user,
            purpose="modify",
            temperature=0.1,
            format_json=True,
            num_ctx=NUM_CTX,
            num_predict=self.SCAN_MAX_OUTPUT_TOKENS,
            client_kwargs={"timeout": self.SCAN_LLM_TIMEOUT_SECONDS},
        )

    def full_scan(self, emitter=None) -> dict:
        """
        Run a full conflict scan using the document-first strategy.

        Emits phase events via emitter for live UI feedback.
        Creates a ScanRun audit record. Returns a stats dict.
        """
        emitter = emitter or NullEmitter()

        scan_run = ScanRun.objects.create(
            project=self.project,
            triggered_by=self.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used=self._get_llm_model_name(),
        )

        emitter.emit("init", "running", "Starting conflict scan…")

        try:
            stats = self._run(emitter, scan_run)
        except CancellationError:
            # ScanRun has no CANCELLED status — FAILED with a clear marker keeps
            # the audit trail honest without synthesizing a new enum value.
            logger.debug("Conflict scan cancelled for project %s", self.project.id)
            scan_run.status = ScanRun.Status.FAILED
            scan_run.error_message = "Cancelled by client."
            scan_run.save(update_fields=["status", "error_message"])
            raise
        except Exception as e:
            logger.exception("Conflict scan failed for project %s: %s", self.project.id, e)
            scan_run.status = ScanRun.Status.FAILED
            scan_run.error_message = str(e)
            scan_run.save(update_fields=["status", "error_message"])
            raise

        scan_run.status = ScanRun.Status.COMPLETED
        scan_run.entities_scanned = stats["entities_scanned"]
        scan_run.conflicts_found = stats["conflicts_found"]
        scan_run.save(update_fields=["status", "entities_scanned", "conflicts_found"])

        return stats

    # ── Pipeline steps ─────────────────────────────────────────────────────────

    def _run(self, emitter, scan_run: ScanRun) -> dict:
        """Execute the document-first scan pipeline."""
        # Step 1: gather requirement chunks
        emitter.emit("requirements", "running", "Loading document requirements…")
        req_chunks = self._get_requirement_chunks()

        if not req_chunks:
            emitter.emit(
                "requirements",
                "done",
                "No requirement sections found in documents",
                {"count": 0},
            )
            return {"entities_scanned": 0, "conflicts_found": 0, "conflicts_updated": 0}

        emitter.emit(
            "requirements",
            "done",
            f"Found {len(req_chunks)} requirement sections",
            {"count": len(req_chunks)},
        )

        # Step 2: build entity → [chunks] mapping
        emitter.emit("matching", "running", "Finding relevant IFC entities…")
        entity_chunk_map = self._build_entity_chunk_map(req_chunks)
        total = len(entity_chunk_map)

        emitter.emit(
            "matching",
            "done",
            f"{total} entity-document pairs to compare",
            {"pairs": total},
        )

        if not entity_chunk_map:
            return {"entities_scanned": 0, "conflicts_found": 0, "conflicts_updated": 0}

        # Step 3: LLM compare per entity
        stats = {"entities_scanned": 0, "conflicts_found": 0, "conflicts_updated": 0}
        self.value_stats = {"values_corrected": 0, "values_unset": 0}

        for i, (entity, chunks) in enumerate(entity_chunk_map.items(), 1):
            # Explicit pre-LLM cancel check. The emit() right below also
            # raises CancellationError if the flag is set, but checking here
            # makes the contract obvious and avoids a wasted send roundtrip
            # when the user cancelled between entities.
            if emitter.is_cancelled():
                raise CancellationError(f"Cancelled before entity {i}/{total}.")

            entity_label = entity.name or entity.global_id or f"entity-{entity.id}"
            base_detail = {
                "current": i,
                "total": total,
                "entity_name": entity_label,
                "ifc_type": entity.ifc_type,
            }
            emitter.emit(
                "compare",
                "running",
                f"Comparing {i}/{total} — {entity.ifc_type} “{entity_label}”",
                base_detail,
            )

            prompt_chars = sum(len(c.content or "") for c in chunks)
            logger.info(
                "Scanner [%d/%d] %s id=%s name=%r → Ollama (chunks=%d, prompt~%d chars)",
                i,
                total,
                entity.ifc_type,
                entity.id,
                entity_label,
                len(chunks),
                prompt_chars,
            )

            started = time.monotonic()
            try:
                findings = self._evaluate_entity(entity, chunks)
                elapsed = time.monotonic() - started
                logger.info(
                    "Scanner [%d/%d] ← %.1fs, %d finding%s",
                    i,
                    total,
                    elapsed,
                    len(findings),
                    "s" if len(findings) != 1 else "",
                )

                stats["entities_scanned"] += 1

                for finding in findings:
                    chunk = self._attribute_chunk(finding, chunks)
                    result = self._upsert_conflict(entity, chunk, finding, scan_run)
                    if result == "created":
                        stats["conflicts_found"] += 1
                    elif result == "updated":
                        stats["conflicts_updated"] += 1

                # Per-entity heartbeat: emit a `done` so the UI sees a real
                # status flip after every comparison, not just an in-place
                # number change. Without this the user can't tell whether the
                # loop is alive or stuck on the current entity.
                emitter.emit(
                    "compare",
                    "done",
                    f"✓ {i}/{total} — {entity.ifc_type} “{entity_label}” — "
                    f"{len(findings)} finding{'s' if len(findings) != 1 else ''}",
                    {**base_detail, "findings": len(findings)},
                )

            except CancellationError:
                # Cancellation propagates as Exception; re-raise so the broad
                # except below doesn't swallow it and continue scanning.
                raise
            except Exception as e:
                elapsed = time.monotonic() - started
                # Timeouts are expected outcomes — one line is enough.
                # Anything else is genuinely surprising — keep the traceback.
                if isinstance(e, (httpx.TimeoutException, httpcore.TimeoutException)):
                    logger.warning(
                        "Scanner [%d/%d] ✗ timeout after %.1fs (Ollama did not respond)",
                        i,
                        total,
                        elapsed,
                    )
                else:
                    logger.exception(
                        "Scanner [%d/%d] ✗ %s after %.1fs",
                        i,
                        total,
                        type(e).__name__,
                        elapsed,
                    )
                emitter.emit(
                    "compare",
                    "error",
                    f"⚠ {i}/{total} — {entity.ifc_type} “{entity_label}” — {type(e).__name__}",
                    {**base_detail, "error": str(e)[:200]},
                )

        logger.info(
            "Conflict scan complete for project %s: scanned=%d, found=%d, updated=%d",
            self.project.id,
            stats["entities_scanned"],
            stats["conflicts_found"],
            stats["conflicts_updated"],
        )
        stats.update(self.value_stats)
        stats["retrieval"] = dict(self.retrieval_stats)
        return stats

    def _get_requirement_chunks(self) -> list[DocumentChunk]:
        """
        Return document chunks that contain AEC compliance keywords.

        Filters by keyword presence to focus only on specification requirements,
        avoiding irrelevant descriptive text.
        """
        chunks = DocumentChunk.objects.filter(
            document__project=self.project,
            document__status="completed",
            embedding__isnull=False,
        ).select_related("document")

        if self.keyword_filter:
            q = Q()
            for kw in REQUIREMENT_KEYWORDS:
                q |= Q(content__icontains=kw)
            chunks = chunks.filter(q)

        return list(chunks)

    def _build_entity_chunk_map(
        self, req_chunks: list[DocumentChunk]
    ) -> dict[IFCEntity, list[DocumentChunk]]:
        """
        For each requirement chunk, find the IFC entities it constrains.

        Three passes, unioned per entity in this order: reference (the chunk
        quotes the entity's model reference or name), label (the chunk names
        the entity's element class and a property), embedding (top-K nearest).
        The first two are exact lookups and run only when ``entity_first`` is
        on; the third is the original vector search and always runs.

        Returns a mapping entity → [chunks], deduplicating entities that
        appear across multiple chunks. ``retrieval_stats`` records how many
        (entity, chunk) pairs each pass contributed.
        """
        entity_qs = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            embedding__isnull=False,
        ).select_related("ifc_file")

        if self.skip_low_value:
            entity_qs = entity_qs.exclude(ifc_type__in=self.LOW_VALUE_IFC_TYPES)

        # entity → {chunk: (pass rank, cosine distance)}; the lowest rank wins
        # when several passes find the same pair.
        pairs: dict[IFCEntity, dict[DocumentChunk, tuple[int, float]]] = defaultdict(dict)
        self.retrieval_stats = {"by_reference": 0, "by_label": 0, "by_embedding": 0}
        reference_index = self._reference_index(entity_qs) if self.entity_first else []
        passes = (
            ("by_reference", 0),
            ("by_label", 1),
            ("by_embedding", 2),
        )

        def add(entity: IFCEntity, chunk: DocumentChunk, pass_name: str, rank: int) -> None:
            if chunk in pairs[entity]:
                return
            pairs[entity][chunk] = (rank, self._cosine_distance(entity, chunk))
            self.retrieval_stats[pass_name] += 1

        for chunk in req_chunks:
            text = chunk.content or ""
            if self.entity_first:
                for entity in self._by_reference(text, reference_index):
                    add(entity, chunk, *passes[0])
                for entity in self._by_label(text, chunk, entity_qs):
                    add(entity, chunk, *passes[1])
            for entity in self._by_embedding(chunk, entity_qs):
                add(entity, chunk, *passes[2])

        return {
            entity: [
                chunk
                for chunk, _ in sorted(found.items(), key=lambda item: item[1])[
                    : self.MAX_CHUNKS_PER_ENTITY
                ]
            ]
            for entity, found in pairs.items()
        }

    @staticmethod
    def _cosine_distance(entity: IFCEntity, chunk: DocumentChunk) -> float:
        """1 - cosine similarity of the two stored vectors; 1.0 when either is missing."""
        a, b = entity.embedding, chunk.embedding
        if a is None or b is None:
            return 1.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        return 1.0 - dot / norm if norm else 1.0

    # ── Retrieval passes ───────────────────────────────────────────────────────

    @staticmethod
    def _reference_index(entity_qs) -> list[tuple[IFCEntity, tuple[re.Pattern, ...]]]:
        """Each entity with the word-bounded patterns that identify it in prose.

        Sources: every ``*.Reference`` property value and every ``:``-separated
        segment of the name except a trailing number (the Revit export id).
        """
        index = []
        for entity in entity_qs:
            strings: set[str] = set()
            for key, value in (entity.properties or {}).items():
                if key.endswith(".Reference") and isinstance(value, str):
                    strings.add(value.strip())
            segments = [part.strip() for part in (entity.name or "").split(":")]
            strings.update(part for part in segments if not part.isdigit())
            patterns = tuple(
                re.compile(rf"(?<![\w]){re.escape(text)}(?![\w])", re.IGNORECASE)
                for text in strings
                if ConflictScanService._is_reference_like(text)
            )
            if patterns:
                index.append((entity, patterns))
        return index

    @staticmethod
    def _is_reference_like(text: str) -> bool:
        """Does this string identify a model type rather than read as a word?"""
        if len(text) < MIN_REFERENCE_LENGTH or text.isdigit():
            return False
        if any(ch.isdigit() or ch in "_-" for ch in text):
            return True
        return " " in text.strip() and len(text) >= MIN_PHRASE_LENGTH

    @staticmethod
    def _by_reference(text: str, reference_index) -> list[IFCEntity]:
        """Entities whose reference or name the chunk quotes."""
        return [
            entity
            for entity, patterns in reference_index
            if any(pattern.search(text) for pattern in patterns)
        ]

    def _by_label(self, text: str, chunk: DocumentChunk, entity_qs) -> list[IFCEntity]:
        """Every entity of each element class the chunk names, capped per class.

        Requires the chunk to mention a property as well ("U-value", "fire
        resistance"), so a sentence that merely says "walls" pulls nothing in.
        Within a class the nearest entities by embedding come first.
        """
        if not self._mentions_property(text):
            return []
        found: list[IFCEntity] = []
        for ifc_type in sorted(self._label_types(text)):
            typed = entity_qs.filter(ifc_type=ifc_type)
            if chunk.embedding is not None:
                typed = typed.annotate(
                    distance=CosineDistance("embedding", chunk.embedding)
                ).order_by("distance")
            else:
                typed = typed.order_by("name")
            found.extend(typed[: self.ENTITY_TYPE_QUOTA])
        return found

    def _by_embedding(self, chunk: DocumentChunk, entity_qs) -> list[IFCEntity]:
        """The original pass: top-K nearest entities under the distance cut."""
        if chunk.embedding is None:
            return []
        return list(
            entity_qs.annotate(distance=CosineDistance("embedding", chunk.embedding))
            .filter(distance__lte=self.ENTITY_RELEVANCE_THRESHOLD)
            .order_by("distance")[: self.ENTITY_TOP_K]
        )

    @staticmethod
    def _label_types(text: str) -> set[str]:
        """IFC classes for every element noun (singular or plural) the text uses."""
        lowered = text.casefold()
        types: set[str] = set()
        for label, ifc_types in ENTITY_FIRST_LABELS.items():
            if re.search(rf"\b{re.escape(label)}s?\b", lowered):
                types |= ifc_types
        return types

    @staticmethod
    def _mentions_property(text: str) -> bool:
        squashed = squash(text)
        if any(alias in squashed for alias in _LONG_PROPERTY_ALIASES):
            return True
        return bool(_SHORT_PROPERTY_RE.search(text))

    @staticmethod
    def _attribute_chunk(finding: dict, chunks: list[DocumentChunk]) -> DocumentChunk:
        """The chunk a finding cites: the one that quotes the requirement, else the LLM's index.

        The model's ``source_chunk_index`` is sometimes wrong (a fire-rating
        conflict cited to the thermal spec). Every chunk is scored by how many
        tokens of the finding's document value it contains, plus a bonus for
        naming the property; the best chunk wins only when it beats the one
        the model named, so a tie keeps the model's choice.
        """
        try:
            index = int(finding.get("source_chunk_index") or 0)
        except (TypeError, ValueError):
            index = 0
        fallback = chunks[min(max(index, 0), len(chunks) - 1)]
        if len(chunks) == 1:
            return fallback

        document_value = str(finding.get("document_value") or "")
        canonical = canonical_property(str(finding.get("property_name") or ""))
        aliases = [
            alias
            for alias, target in PROPERTY_ALIASES.items()
            if target == canonical and len(alias) >= 4
        ]
        tokens = [t for t in re.findall(r"[A-Za-z0-9.']+", document_value) if len(t) >= 2]

        def score(chunk: DocumentChunk) -> float:
            squashed = squash(chunk.content or "")
            total = 0.0
            for token in tokens:
                if squash(token) in squashed:
                    total += 1.0 if (len(token) >= 3 or not token.isdigit()) else 0.5
            if any(alias in squashed for alias in aliases):
                total += 2.0
            return total

        best = max(chunks, key=score)
        return best if score(best) > score(fallback) else fallback

    def _evaluate_entity(self, entity: IFCEntity, chunks: list[DocumentChunk]) -> list[dict]:
        """
        One LLM call: given entity properties + relevant chunks, find contradictions.

        Filters results by confidence threshold (>= CONFIDENCE_THRESHOLD) before
        returning to prevent low-confidence guesses from being stored.
        """
        # Walk the spatial_container chain to build location text
        location_parts: list[str] = []
        node = getattr(entity, "spatial_container", None)
        while node:
            name = node.entity.name if hasattr(node, "entity") and node.entity else ""
            if name:
                location_parts.append(name)
            node = node.parent
        location = ", ".join(location_parts) or "Unassigned"

        properties_text = self._format_properties(entity.properties)

        excerpts = []
        for i, chunk in enumerate(chunks):
            page = chunk.page_number or "?"
            excerpts.append(f"[{i}] [{chunk.document.name}, Page {page}]\n{chunk.content}")
        doc_excerpts = "\n\n---\n\n".join(excerpts)

        messages = [
            SystemMessage(content=SCANNER_SYSTEM_PROMPT),
            HumanMessage(
                content=SCANNER_USER_TEMPLATE.format(
                    ifc_type=entity.ifc_type,
                    name=entity.name or "(unnamed)",
                    location=location,
                    properties=properties_text,
                    doc_excerpts=doc_excerpts,
                )
            ),
        ]

        # Hard wall-clock cap on the LLM call. httpx's per-read timeout doesn't
        # fire while Ollama is streaming tokens, so a stalled call can wedge a
        # thread for many minutes. core.llm.safe_invoke runs the call in a
        # worker we can abandon and re-raises the timeout — we map that back
        # to httpx.ReadTimeout so the loop's existing handler catches it.
        try:
            response = safe_invoke(
                self.llm.invoke,
                messages,
                timeout=self.SCAN_LLM_TIMEOUT_SECONDS,
                thread_name_prefix=f"scan-llm-{entity.id}",
            )
        except concurrent.futures.TimeoutError as e:
            raise httpx.ReadTimeout(
                f"Hard wall-clock timeout after {self.SCAN_LLM_TIMEOUT_SECONDS:.0f}s"
            ) from e

        try:
            result = json.loads(response.content)
            findings = result.get("conflicts", [])
        except json.JSONDecodeError:
            logger.warning(
                "Scanner LLM returned invalid JSON for entity %s: %s",
                entity.id,
                response.content[:200],
            )
            return []

        confident = [f for f in findings if f.get("confidence", 0) >= self.confidence_threshold]
        low_conf_skipped = len(findings) - len(confident)
        if low_conf_skipped:
            logger.debug(
                "Filtered %d low-confidence finding(s) for entity %s",
                low_conf_skipped,
                entity.id,
            )

        applicable = [
            f for f in confident if not self.type_gate or self._finding_applies_to_entity(f, entity)
        ]
        type_skipped = len(confident) - len(applicable)
        if type_skipped:
            logger.info(
                "Dropped %d cross-type finding(s) for entity %s (type=%s): requirement targeted a different element class",
                type_skipped,
                entity.id,
                entity.ifc_type,
            )

        return applicable

    @staticmethod
    def _finding_applies_to_entity(finding: dict, entity: IFCEntity) -> bool:
        """
        Gate: does this LLM finding actually target the entity's IFC type?

        Uses the `applies_to_element` label the LLM attaches to each finding.
        Generic labels (in ELEMENT_TYPE_ANY_LABELS) bypass the gate. Known
        labels (in ELEMENT_TYPE_MAP) must have the entity's ifc_type in their
        allowed set. Unknown labels are allowed through (fail-open) so a new
        requirement vocabulary doesn't silently drop findings — extend
        ELEMENT_TYPE_MAP when that happens.
        """
        label = str(finding.get("applies_to_element", "")).strip().lower()

        if label in ELEMENT_TYPE_ANY_LABELS:
            return True

        allowed = ELEMENT_TYPE_MAP.get(label)
        if allowed is None:
            logger.debug("Unknown applies_to_element label %r — allowing finding through", label)
            return True

        return entity.ifc_type in allowed

    def _upsert_conflict(
        self,
        entity: IFCEntity,
        chunk: DocumentChunk,
        finding: dict,
        scan_run: ScanRun,
    ) -> str:
        """
        Create or update a Conflict record using content_hash deduplication.

        content_hash = SHA-256(entity.id + ":" + chunk.id + ":" + property_name)

        Logic:
          - content_hash exists as DISMISSED → skip (don't recreate)
          - content_hash exists as OPEN → update in place
          - content_hash exists as RESOLVED / not found → create fresh record

        Returns 'created', 'updated', or 'skipped'.
        """
        property_name = self._finding_str(finding, "property_name")[:255]

        if self.verify_values:
            finding = self._verify_ifc_value(entity, finding)

        # Hard guard: never store a conflict where IFC value equals document value.
        # The LLM sometimes flags these despite explicit prompt instructions not to.
        ifc_val = self._finding_str(finding, "ifc_value")
        doc_val = self._finding_str(finding, "document_value")
        if ifc_val and doc_val and self._values_equivalent(ifc_val, doc_val):
            logger.info(
                "Skipping false positive: IFC value '%s' already matches document value '%s' for entity %s",
                ifc_val,
                doc_val,
                entity.id,
            )
            return "skipped"

        title = self._finding_str(finding, "title", "Conflict")[:255]
        severity = finding.get("severity", Conflict.Severity.MEDIUM)

        valid_severities = {s.value for s in Conflict.Severity}
        if severity not in valid_severities:
            severity = Conflict.Severity.MEDIUM

        content_hash = hashlib.sha256(
            f"{entity.id}:{chunk.id}:{property_name}".encode()
        ).hexdigest()

        common_fields = {
            "title": title,
            "description": self._finding_str(finding, "description"),
            "ifc_value": self._finding_str(finding, "ifc_value", "(not set)"),
            "document_value": self._finding_str(finding, "document_value"),
            "severity": severity,
            "suggested_fix": self._finding_str(finding, "suggested_fix"),
            "confidence": finding.get("confidence", 0.0),
            "property_name": property_name,
            "scan_run": scan_run,
        }

        # Check for DISMISSED — never recreate
        if Conflict.objects.filter(
            project=self.project,
            content_hash=content_hash,
            status=Conflict.Status.DISMISSED,
        ).exists():
            return "skipped"

        # Check for OPEN — update in place
        existing_open = Conflict.objects.filter(
            project=self.project,
            content_hash=content_hash,
            status=Conflict.Status.OPEN,
        ).first()

        if existing_open:
            for field, value in common_fields.items():
                setattr(existing_open, field, value)
            existing_open.save(update_fields=list(common_fields.keys()))
            return "updated"

        # Create fresh record
        Conflict.objects.create(
            project=self.project,
            ifc_entity=entity,
            document_chunk=chunk,
            content_hash=content_hash,
            status=Conflict.Status.OPEN,
            **common_fields,
        )
        return "created"

    # ── Value verification ─────────────────────────────────────────────────────

    def _verify_ifc_value(self, entity: IFCEntity, finding: dict) -> dict:
        """Replace the current value the model claims with the value the index holds.

        The model reads the entity's properties from the prompt and still
        invents current values (``EI60`` on a wall with no FireRating). The
        index is the truth: a property the entity carries overrides the claim;
        a property it does not carry is stored as ``(not set)``. Counts land in
        ``value_stats`` so a benchmark can report how often this fired.
        """
        canonical = canonical_property(self._finding_str(finding, "property_name"))
        claimed = self._finding_str(finding, "ifc_value")
        found, actual = self._lookup_property(entity.properties or {}, canonical)

        if not found:
            if claimed and not _ABSENCE_RE.search(claimed):
                self.value_stats["values_unset"] += 1
                logger.info(
                    "Finding claimed %s=%r on entity %s which carries no such property; stored as not set",
                    canonical,
                    claimed,
                    entity.id,
                )
            return {**finding, "ifc_value": "(not set)"}

        actual_text = self._value_text(actual)
        if claimed and not self._same_value(claimed, actual):
            self.value_stats["values_corrected"] += 1
            logger.info(
                "Finding claimed %s=%r on entity %s; index holds %r, stored the index value",
                canonical,
                claimed,
                entity.id,
                actual_text,
            )
        return {**finding, "ifc_value": actual_text}

    @staticmethod
    def _lookup_property(properties: dict, canonical: str) -> tuple[bool, object]:
        """(found, value) for the property in a flat ``Pset.Name`` dict.

        Keys are matched on their last segment through the alias table, so
        ``Pset_WallCommon.FireRating`` answers a finding about "fire class".
        A ``Pset_*`` key wins over a ``Type.*`` or vendor key when both exist.
        """
        if not canonical:
            return False, None
        matches = [
            (key, value)
            for key, value in properties.items()
            if canonical_property(key.rsplit(".", 1)[-1]) == canonical
        ]
        if not matches:
            return False, None
        matches.sort(key=lambda kv: (not kv[0].startswith("Pset_"), kv[0]))
        return True, matches[0][1]

    @classmethod
    def _same_value(cls, claimed: str, actual: object) -> bool:
        """Is the claim the indexed value, allowing for float formatting?"""
        if squash(claimed) == squash(cls._value_text(actual)):
            return True
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            try:
                return abs(float(claimed) - float(actual)) <= 1e-6 * max(1.0, abs(float(actual)))
            except ValueError:
                return False
        return False

    @staticmethod
    def _value_text(value: object) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    @classmethod
    def _values_equivalent(cls, ifc_value: str, document_value: str) -> bool:
        """Does the document's value describe the entity's current value?

        Exact after squashing; numerically equal at the document's own
        precision ("0.35 as designed" describes 0.350998); or a boolean that
        the document's wording asserts ("non-load-bearing" describes False).
        """
        if squash(ifc_value) == squash(document_value):
            return True
        number = cls._first_number(document_value)
        if number is not None:
            try:
                current = float(ifc_value)
            except ValueError:
                return False
            decimals = len(number.split(".")[1]) if "." in number else 0
            return round(current, decimals) == float(number)
        if ifc_value in {"True", "False"}:
            negated = bool(_NEGATION_RE.search(document_value))
            return (ifc_value == "False") == negated
        return False

    @staticmethod
    def _first_number(text: str) -> str | None:
        match = re.search(r"\d+(?:\.\d+)?", text)
        return match.group(0) if match else None

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _finding_str(finding: dict, key: str, fallback: str = "") -> str:
        """Coerce an LLM-supplied finding field to a non-None string.

        `dict.get(key, default)` only uses the default when the key is missing,
        not when the value is explicitly None — the LLM sometimes returns
        ``null`` for string fields despite prompt guidance, so this helper
        covers both cases.
        """
        value = finding.get(key)
        if value is None:
            return fallback
        return str(value)

    def _get_llm_model_name(self) -> str:
        """The model the scan actually ran on, for ``ScanRun.llm_model_used``."""
        return resolve_model_name(self.user, "modify")

    @staticmethod
    def _format_properties(properties: dict) -> str:
        """Format entity properties dict as readable text for the LLM."""
        if not properties:
            return "(no properties)"

        lines = []
        for pset_name, pset_data in properties.items():
            if isinstance(pset_data, dict):
                lines.append(f"**{pset_name}:**")
                for prop_name, prop_value in pset_data.items():
                    lines.append(f"  - {prop_name}: {prop_value}")
            else:
                lines.append(f"- {pset_name}: {pset_data}")

        return "\n".join(lines) if lines else "(no properties)"
