# writeback/services/conflict_scan_service.py
"""
Conflict Scan Engine — proactive IFC-vs-document contradiction detector.

For each IFC entity in a project, uses its existing embedding to find
semantically relevant document chunks and asks the LLM whether any of
the entity's current properties contradict the document requirements.
Found contradictions are stored as Conflict records.

Design mirrors guardian_service.py: same LLM factory, same chunk
formatting, same JSON parse safety. The difference is directionality —
the Guardian checks a *proposed* change, the Scanner checks *existing*
entity state.
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pgvector.django import CosineDistance

from core.llm import get_llm
from documents.models import DocumentChunk
from ifc_processor.models import IFCEntity
from writeback.models import Conflict

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Scanner LLM Prompts
# ────────────────────────────────────────────────────────────

SCANNER_SYSTEM_PROMPT = """\
You are the Castor Conflict Scanner, a BIM/document auditor.

You receive:
1. An IFC entity with its current properties (extracted from the BIM model).
2. DOCUMENT EXCERPTS from the project's specification documents (retrieved by semantic similarity).

Your job is to find SPECIFIC CONTRADICTIONS where a document explicitly states a DIFFERENT
requirement or value than what is currently in the IFC model.

## Output

Return ONLY valid JSON (no markdown, no explanation):

{
  "conflicts": [
    {
      "title": "<Short conflict title, 5-10 words>",
      "description": "<1-2 sentence explanation of the contradiction>",
      "ifc_value": "<The current value in the IFC model>",
      "document_value": "<The value required by the document>",
      "severity": "<one of: low, medium, high, critical>",
      "suggested_fix": "<A natural language modify request, e.g. 'Set FireRating to EI120 for all external walls on Level 1'>",
      "source_chunk_index": <0-based index into the provided document excerpts>
    }
  ]
}

If there are NO conflicts, return: {"conflicts": []}

## Rules

1. Only report GENUINE contradictions — explicit value/requirement differences.
2. Do NOT report missing properties or absent data — only real contradictions.
3. Do NOT invent conflicts from vague or ambiguous text.
4. Be conservative: if unclear, return no conflict.
5. Severity guide:
   - critical: fire safety, structural integrity, life safety
   - high: energy performance, acoustic, accessibility compliance
   - medium: materials, finishes, stated dimensions
   - low: labeling, naming conventions, non-critical metadata
6. The suggested_fix should be a plain English instruction a BIM manager would give.
7. source_chunk_index must reference a valid index from the provided excerpts (0-based).
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
Identify any SPECIFIC CONTRADICTIONS between the IFC entity's current properties \
and the document requirements. Return JSON only.
"""


class ConflictScanService:
    """
    Proactive conflict scanner: checks each IFC entity against project documents.

    Uses entity embeddings directly (no extra embed call) to find relevant
    document chunks, then passes entity properties + chunks to the LLM to
    detect contradictions. Results are stored as Conflict records.

    Usage:
        svc = ConflictScanService(project, user)
        stats = svc.full_scan()  # returns dict with counters
    """

    RELEVANCE_THRESHOLD = 0.45

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

    def full_scan(self) -> dict:
        """
        Scan all IFC entities in the project for document contradictions.

        Returns a stats dict: scanned, skipped_no_docs, conflicts_found, conflicts_updated.
        """
        has_docs = DocumentChunk.objects.filter(
            document__project=self.project,
            document__status="completed",
            embedding__isnull=False,
        ).exists()

        if not has_docs:
            logger.warning(f"Conflict scan: no documents with embeddings in project {self.project.id}")
            return {
                "scanned": 0,
                "skipped_no_docs": 0,
                "conflicts_found": 0,
                "conflicts_updated": 0,
                "error": "No documents with embeddings found in this project.",
            }

        entities = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            embedding__isnull=False,
        ).select_related("ifc_file")

        stats = {"scanned": 0, "skipped_no_docs": 0, "conflicts_found": 0, "conflicts_updated": 0}

        for entity in entities:
            if self.skip_low_value and entity.ifc_type in self.LOW_VALUE_IFC_TYPES:
                continue

            try:
                chunks = self._find_relevant_chunks(entity)
                if not chunks:
                    stats["skipped_no_docs"] += 1
                    continue

                findings = self._evaluate_entity(entity, chunks)
                stats["scanned"] += 1

                for finding in findings:
                    chunk_idx = finding.get("source_chunk_index", 0)
                    # Guard against out-of-range indices
                    chunk = chunks[min(chunk_idx, len(chunks) - 1)]
                    result = self._upsert_conflict(entity, chunk, finding)
                    if result == "created":
                        stats["conflicts_found"] += 1
                    elif result == "updated":
                        stats["conflicts_updated"] += 1

            except Exception:
                logger.exception(f"Conflict scan failed for entity {entity.id} ({entity})")

        logger.info(
            f"Conflict scan complete for project {self.project.id}: "
            f"scanned={stats['scanned']}, found={stats['conflicts_found']}, "
            f"updated={stats['conflicts_updated']}, no_docs={stats['skipped_no_docs']}"
        )
        return stats

    def _find_relevant_chunks(self, entity: IFCEntity, top_k: int = 5) -> list[DocumentChunk]:
        """
        Use the entity's existing embedding to find relevant document chunks.

        No extra embed call — we reuse the stored 1024d vector directly.
        Returns chunks sorted by relevance, filtered by threshold.
        """
        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="completed",
                embedding__isnull=False,
            )
            .select_related("document")
            .annotate(distance=CosineDistance("embedding", entity.embedding))
            .order_by("distance")[:top_k]
        )

        relevant = [c for c in chunks if c.distance <= self.RELEVANCE_THRESHOLD]

        logger.debug(
            f"Entity {entity}: {len(chunks)} candidates, "
            f"{len(relevant)} above threshold ({self.RELEVANCE_THRESHOLD})"
        )
        return relevant

    def _evaluate_entity(self, entity: IFCEntity, chunks: list[DocumentChunk]) -> list[dict]:
        """
        One LLM call: given entity properties + relevant chunks, find contradictions.

        Returns a list of finding dicts (may be empty).
        """
        # Format location string
        location_parts = filter(None, [entity.building_storey, entity.space, entity.building])
        location = ", ".join(location_parts) or "Unassigned"

        # Format properties as readable text
        properties_text = self._format_properties(entity.properties)

        # Format document excerpts
        excerpts = []
        for i, chunk in enumerate(chunks):
            page = chunk.page_number or "?"
            excerpts.append(f"[{i}] [{chunk.document.name}, Page {page}]\n{chunk.content}")
        doc_excerpts = "\n\n---\n\n".join(excerpts) if excerpts else "(none)"

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
            return result.get("conflicts", [])
        except json.JSONDecodeError:
            logger.warning(
                f"Scanner LLM returned invalid JSON for entity {entity.id}: {response.content[:200]}"
            )
            return []

    def _upsert_conflict(self, entity: IFCEntity, chunk: DocumentChunk, finding: dict) -> str:
        """
        Create or update a Conflict record for this finding.

        Match key: (project, ifc_entity, document_chunk, title)
        - DISMISSED: skip (don't recreate on re-scan)
        - OPEN: update in place
        - RESOLVED: create new record
        - DoesNotExist: create new record

        Returns 'created', 'updated', or 'skipped'.
        """
        title = finding.get("title", "Conflict")[:255]
        severity = finding.get("severity", Conflict.Severity.MEDIUM)

        # Validate severity value
        valid_severities = {s.value for s in Conflict.Severity}
        if severity not in valid_severities:
            severity = Conflict.Severity.MEDIUM

        try:
            existing = Conflict.objects.get(
                project=self.project,
                ifc_entity=entity,
                document_chunk=chunk,
                title=title,
            )

            if existing.status == Conflict.Status.DISMISSED:
                return "skipped"

            if existing.status == Conflict.Status.OPEN:
                existing.description = finding.get("description", "")
                existing.ifc_value = finding.get("ifc_value", "")
                existing.document_value = finding.get("document_value", "")
                existing.severity = severity
                existing.suggested_fix = finding.get("suggested_fix", "")
                existing.save(
                    update_fields=[
                        "description",
                        "ifc_value",
                        "document_value",
                        "severity",
                        "suggested_fix",
                    ]
                )
                return "updated"

            # RESOLVED — create a fresh record
            Conflict.objects.create(
                project=self.project,
                ifc_entity=entity,
                document_chunk=chunk,
                title=title,
                description=finding.get("description", ""),
                ifc_value=finding.get("ifc_value", ""),
                document_value=finding.get("document_value", ""),
                severity=severity,
                suggested_fix=finding.get("suggested_fix", ""),
                status=Conflict.Status.OPEN,
            )
            return "created"

        except Conflict.DoesNotExist:
            Conflict.objects.create(
                project=self.project,
                ifc_entity=entity,
                document_chunk=chunk,
                title=title,
                description=finding.get("description", ""),
                ifc_value=finding.get("ifc_value", ""),
                document_value=finding.get("document_value", ""),
                severity=severity,
                suggested_fix=finding.get("suggested_fix", ""),
                status=Conflict.Status.OPEN,
            )
            return "created"

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
