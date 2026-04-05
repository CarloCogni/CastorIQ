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

import hashlib
import json
import logging
from collections import defaultdict

from django.db.models import Q
from langchain_core.messages import HumanMessage, SystemMessage
from pgvector.django import CosineDistance

from core.llm import get_llm
from documents.models import DocumentChunk
from ifc_processor.models import IFCEntity
from writeback.models import Conflict, ScanRun
from writeback.services.emitters import NullEmitter

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
# Scanner LLM Prompts
# ────────────────────────────────────────────────────────────

SCANNER_SYSTEM_PROMPT = """\
You are a construction compliance checker.

Your task is to compare IFC building model data against technical document requirements.

## Two-step process (MANDATORY):
Step 1 — EXTRACT: List every specific requirement found in the document excerpts
         (property name + required value). Ignore vague or general statements.
Step 2 — COMPARE: For each extracted requirement, look up the same property in the
         IFC entity's current properties and compare:
         - Values match or are equivalent → NOT a conflict. Skip it.
         - Values differ → flag as a conflict.
         - Property absent in IFC but entity has other properties → NOT a conflict (property may be optional or not applicable to this element).
         - Entity has NO properties at all (shown as "(no properties)") AND document explicitly requires a specific value for this element type → flag as a conflict: required data is absent. Use ifc_value: "absent (no property sets)", suggested_fix: "Add [PropertyName] = [RequiredValue] to [EntityName] — required by document".
         - Requirement is ambiguous → NOT a conflict (uncertainty, not contradiction).

## Critical rules:
- NEVER flag a property where the IFC value already satisfies the document requirement.
- ONLY flag when the IFC value is demonstrably DIFFERENT from the document requirement.
- Assign a confidence score (0.0–1.0). Only include conflicts with confidence >= 0.7.
- If no genuine conflicts exist, return {"conflicts": []}.

## Output format (JSON only, no markdown):
{
  "conflicts": [
    {
      "property_name": "FireRating",
      "ifc_value": "EI60",
      "document_value": "EI120",
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

    def __init__(self, project, user, skip_low_value: bool = True):
        self.project = project
        self.user = user
        self.skip_low_value = skip_low_value
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

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
            emitter.emit(
                "compare",
                "running",
                f"Comparing entity {i}/{total}…",
                {"current": i, "total": total},
            )
            try:
                findings = self._evaluate_entity(entity, chunks)
                stats["entities_scanned"] += 1

                for finding in findings:
                    chunk_idx = finding.get("source_chunk_index", 0)
                    chunk = chunks[min(chunk_idx, len(chunks) - 1)]
                    result = self._upsert_conflict(entity, chunk, finding, scan_run)
                    if result == "created":
                        stats["conflicts_found"] += 1
                    elif result == "updated":
                        stats["conflicts_updated"] += 1

            except Exception:
                logger.exception("Conflict scan failed for entity %s (%s)", entity.id, entity)

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
        location_parts = filter(None, [entity.building_storey, entity.space, entity.building])
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

        response = self.llm.invoke(messages)

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
        skipped = len(findings) - len(confident)
        if skipped:
            logger.debug("Filtered %d low-confidence finding(s) for entity %s", skipped, entity.id)

        return confident

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
        property_name = finding.get("property_name", "")[:255]
        title = finding.get("title", "Conflict")[:255]
        severity = finding.get("severity", Conflict.Severity.MEDIUM)

        valid_severities = {s.value for s in Conflict.Severity}
        if severity not in valid_severities:
            severity = Conflict.Severity.MEDIUM

        content_hash = hashlib.sha256(
            f"{entity.id}:{chunk.id}:{property_name}".encode()
        ).hexdigest()

        common_fields = {
            "title": title,
            "description": finding.get("description", ""),
            "ifc_value": finding.get("ifc_value", ""),
            "document_value": finding.get("document_value", ""),
            "severity": severity,
            "suggested_fix": finding.get("suggested_fix", ""),
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
