# writeback/services/conflict_scan_service.py
"""
Conflict Scan Engine — document-first IFC-vs-document contradiction detector.

Strategy: instead of iterating all IFC entities (entity-first), we start from
document requirement chunks and find the IFC entities they constrain (document-first).
This drastically reduces LLM calls by only scanning entities actually referenced in
specification requirements.

Pipeline:
    1. Gather requirement chunks — filter DocumentChunks by AEC compliance keywords
    2. Build entity-chunk map — for each req chunk, vector-search top-K IFC entities
    3. LLM compare — one call per entity that has at least one relevant chunk
    4. Persist — upsert Conflict records with content_hash deduplication

Design mirrors guardian_service.py: same LLM factory, same chunk formatting,
same JSON parse safety. The difference is directionality — the Guardian checks a
*proposed* change, the Scanner checks *existing* entity state.
"""

import concurrent.futures
import hashlib
import json
import logging
import time
from collections import defaultdict

import httpcore
import httpx
from django.db.models import Q
from langchain_core.messages import HumanMessage, SystemMessage
from pgvector.django import CosineDistance

from core.llm import get_llm
from documents.models import DocumentChunk
from ifc_processor.models import IFCEntity
from writeback.models import Conflict, ScanRun
from writeback.services.emitters import CancellationError, NullEmitter

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
    ENTITY_TOP_K = 5  # IFC entities to retrieve per requirement chunk

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

    def __init__(self, project, user, skip_low_value: bool = True):
        self.project = project
        self.user = user
        self.skip_low_value = skip_low_value
        # `client_kwargs={"timeout": …}` is httpx's per-read timeout — it
        # only fires when the socket goes silent for that long. Ollama can
        # stream tokens slowly enough to keep the timer alive forever, so
        # this is NOT a wall-clock cap. The real cap is enforced in
        # _evaluate_entity() via ThreadPoolExecutor.result(timeout=…).
        # `num_predict` caps Ollama's output tokens at the model layer so
        # the model can't generate for 30 minutes even if the timeout
        # somehow doesn't fire.
        self.llm = get_llm(
            user=user,
            temperature=0.1,
            format_json=True,
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
                    chunk_idx = finding.get("source_chunk_index", 0)
                    chunk = chunks[min(chunk_idx, len(chunks) - 1)]
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
        return stats

    def _get_requirement_chunks(self) -> list[DocumentChunk]:
        """
        Return document chunks that contain AEC compliance keywords.

        Filters by keyword presence to focus only on specification requirements,
        avoiding irrelevant descriptive text.
        """
        q = Q()
        for kw in REQUIREMENT_KEYWORDS:
            q |= Q(content__icontains=kw)

        return list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="completed",
                embedding__isnull=False,
            )
            .filter(q)
            .select_related("document")
        )

    def _build_entity_chunk_map(
        self, req_chunks: list[DocumentChunk]
    ) -> dict[IFCEntity, list[DocumentChunk]]:
        """
        For each requirement chunk, vector-search top-K IFC entities.

        Returns a mapping entity → [list of relevant chunks], deduplicating
        entities that appear across multiple chunks.
        """
        entity_qs = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            embedding__isnull=False,
        ).select_related("ifc_file")

        if self.skip_low_value:
            entity_qs = entity_qs.exclude(ifc_type__in=self.LOW_VALUE_IFC_TYPES)

        entity_chunk_map: dict = defaultdict(list)

        for chunk in req_chunks:
            if chunk.embedding is None:
                continue
            nearby = list(
                entity_qs.annotate(distance=CosineDistance("embedding", chunk.embedding))
                .filter(distance__lte=self.ENTITY_RELEVANCE_THRESHOLD)
                .order_by("distance")[: self.ENTITY_TOP_K]
            )
            for entity in nearby:
                entity_chunk_map[entity].append(chunk)

        return dict(entity_chunk_map)

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

        # Hard wall-clock cap on the LLM call. We can't trust httpx's per-read
        # timeout to fire (Ollama streams tokens, which keeps the read timer
        # alive even when generation effectively stalls — the user has hit
        # this exact mode for 18 minutes on a single entity). Run invoke()
        # in a worker thread and give up after SCAN_LLM_TIMEOUT_SECONDS.
        # The thread will keep running until Ollama responds — Python can't
        # interrupt a blocking C call — but the loop advances. The leaked
        # thread is a daemon so it won't block process exit; the only real
        # cost is that the in-flight Ollama request still occupies one slot
        # on the Ollama server until it returns.
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"scan-llm-{entity.id}"
        )
        try:
            future = executor.submit(self.llm.invoke, messages)
            try:
                response = future.result(timeout=self.SCAN_LLM_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError as e:
                # Re-raise as httpx.ReadTimeout so the loop's existing
                # timeout handler (one-line warning) catches it.
                raise httpx.ReadTimeout(
                    f"Hard wall-clock timeout after {self.SCAN_LLM_TIMEOUT_SECONDS:.0f}s"
                ) from e
        finally:
            # Don't wait — if the worker is wedged in Ollama, waiting would
            # defeat the entire point of the watchdog.
            executor.shutdown(wait=False)

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

        confident = [f for f in findings if f.get("confidence", 0) >= CONFIDENCE_THRESHOLD]
        low_conf_skipped = len(findings) - len(confident)
        if low_conf_skipped:
            logger.debug(
                "Filtered %d low-confidence finding(s) for entity %s",
                low_conf_skipped,
                entity.id,
            )

        applicable = [f for f in confident if self._finding_applies_to_entity(f, entity)]
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
        """Return the LLM model name for audit logging."""
        try:
            from core.models import UserLLMConfig

            config = UserLLMConfig.objects.filter(user=self.user).first()
            if config and config.model_name:
                return config.model_name
        except Exception:
            pass
        from django.conf import settings

        return getattr(settings, "OLLAMA_MODEL", "unknown")

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
