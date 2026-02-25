# writeback/services/guardian_service.py
"""
Castor Guardian — Document cross-reference for modification proposals.

When a user proposes an IFC modification, the Guardian searches project
documents (specs, fire reports, contracts) for relevant information and
flags potential conflicts before the user approves.

This is the "second opinion" that makes Castor bidirectional:
  IFC ←→ Documents
"""

import json
import logging
import re
from pgvector.django import CosineDistance
from langchain_core.messages import SystemMessage, HumanMessage
from documents.models import DocumentChunk
from embeddings.services.embedding_service import EmbeddingService
from writeback.models import ModificationProposal
from core.llm import get_llm

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Guardian LLM Prompt
# ────────────────────────────────────────────────────────────
GUARDIAN_SYSTEM_PROMPT = """\
You are the Castor Guardian, a verification assistant for BIM/IFC modifications.

You receive:
1. A PROPOSED CHANGE to an IFC model (property, value, entity type).
2. DOCUMENT EXCERPTS retrieved from the project's specification documents.

Your job is to determine whether the document excerpts CONFIRM, CONFLICT with,
or are IRRELEVANT to the proposed change.

## Output

Return ONLY valid JSON (no markdown, no explanation):

{
  "verdict": "<one of: CONFIRMED, CONFLICT, NO_INFO>",
  "explanation": "<1-2 sentence summary of what you found>",
  "source_detail": "<document name and page, e.g. 'Fire Strategy.pdf, p.14'>"
}

## Rules

1. CONFLICT: The document explicitly states a DIFFERENT value or requirement
   for the same property/attribute on the same type of entity.
   Example: Proposal sets FireRating=EI120, document says "external walls
   shall be rated EI90" → CONFLICT.

2. CONFIRMED: The document explicitly supports or matches the proposed value.
   Example: Proposal sets FireRating=EI120, document says "minimum EI120
   for all external walls" → CONFIRMED.

3. NO_INFO: The excerpts don't mention anything relevant to this specific
   property/value combination. Don't stretch — if it's not clearly about
   the same topic, return NO_INFO.

4. Be conservative. Only flag CONFLICT if there's a clear contradiction.
5. Always cite the document name and page number in source_detail.
6. If multiple excerpts are relevant, base your verdict on the most specific one.
"""

GUARDIAN_USER_TEMPLATE = """\
## Proposed Change
- Entity type: {ifc_type}
- Operation: {operation}
- Property: {property_or_attribute}
- New value: {new_value}
- Explanation: {explanation}

## Document Excerpts
{doc_excerpts}

## Task
Analyze whether these documents confirm, conflict with, or say nothing about this proposed change. Return JSON only.
"""


class GuardianService:
    """
    Cross-references a ModificationProposal against project documents.

    Flow:
        1. Build a targeted search query from the proposal's intent
        2. Vector-search DocumentChunks (docs only, not IFC entities)
        3. If relevant chunks found → LLM pass to classify verdict
        4. Save results to proposal.verification_* fields

    Usage:
        guardian = GuardianService()
        guardian.check(proposal)  # mutates proposal in-place and saves
    """

    # Minimum similarity threshold — chunks less relevant than this are skipped
    RELEVANCE_THRESHOLD = 0.45  # cosine distance (lower = more similar)

    def __init__(self, user=None):
        self.embedding_service = EmbeddingService()
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

    def check(self, proposal: ModificationProposal) -> ModificationProposal:
        """
        Run the guardian check on a proposal. Saves results to DB.

        Args:
            proposal: A ModificationProposal with intent_json populated.

        Returns:
            The same proposal, updated with verification_* fields.
        """
        try:
            project = proposal.ifc_file.project

            # 1. Build search query from the proposal's intent
            search_query = self._build_search_query(proposal)
            logger.info(f"Guardian search query: '{search_query}'")

            # 2. Vector search against project documents
            chunks = self._search_documents(project, search_query)

            if not chunks:
                proposal.verification_status = ModificationProposal.VerificationStatus.UNKNOWN
                proposal.verification_result = "No relevant information found in project documents."
                proposal.verification_source = ""
                proposal.save(update_fields=[
                    "verification_status", "verification_result", "verification_source",
                ])
                logger.info(f"Guardian: no relevant docs for proposal {proposal.id}")
                return proposal

            # 3. LLM pass — classify confirm/conflict/no_info
            verdict = self._evaluate(proposal, chunks)

            # 4. Map verdict to model status
            status_map = {
                "CONFIRMED": ModificationProposal.VerificationStatus.VERIFIED,
                "CONFLICT": ModificationProposal.VerificationStatus.CONFLICT,
                "NO_INFO": ModificationProposal.VerificationStatus.UNKNOWN,
            }
            proposal.verification_status = status_map.get(
                verdict.get("verdict", "NO_INFO"),
                ModificationProposal.VerificationStatus.UNKNOWN,
            )
            proposal.verification_result = verdict.get("explanation", "")
            proposal.verification_source = verdict.get("source_detail", "")

            proposal.save(update_fields=[
                "verification_status", "verification_result", "verification_source",
            ])

            logger.info(
                f"Guardian verdict for proposal {proposal.id}: "
                f"{proposal.verification_status} — {proposal.verification_result}"
            )
            return proposal

        except Exception as e:
            logger.exception(f"Guardian check failed for proposal {proposal.id}: {e}")
            proposal.verification_status = ModificationProposal.VerificationStatus.FAILED
            proposal.verification_result = f"Guardian check failed: {str(e)}"
            proposal.save(update_fields=["verification_status", "verification_result"])
            return proposal

    def _build_search_query(self, proposal: ModificationProposal) -> str:
        """
        Build a natural-language search query from the proposal's intent.

        The goal is to find document chunks that talk about the same
        property/requirement. We combine entity type + property + value
        into a query that the embedding model can match against.
        """
        intent = proposal.intent_json or {}

        parts = []

        # Entity type in natural language
        ifc_type = intent.get("filter", {}).get("ifc_type", "")
        if ifc_type:
            # "IfcWall" → "wall", "IfcDoor" → "door"
            natural_type = ifc_type.replace("Ifc", "").lower()
            parts.append(natural_type)

        # Property or attribute name
        prop = intent.get("property", "") or intent.get("attribute", "")
        if prop:
            # "FireRating" → "fire rating", "ThermalTransmittance" → "thermal transmittance"
            natural_prop = self._camel_to_words(prop)
            parts.append(natural_prop)

        # Value for extra specificity
        new_value = intent.get("new_value", "")
        if new_value and isinstance(new_value, str):
            parts.append(str(new_value))

        # Pset for context
        pset = intent.get("pset", "")
        if pset and "Common" in pset:
            parts.append("requirements")

        if not parts:
            # Fallback: use the raw explanation
            return proposal.explanation or proposal.request_text

        return " ".join(parts)

    def _search_documents(
        self,
        project,
        query: str,
        top_k: int = 5,
    ) -> list[DocumentChunk]:
        """
        Vector search against project document chunks only.

        Returns chunks sorted by relevance, filtered by threshold.
        """
        query_vector = self.embedding_service.embed_query(query)
        if not query_vector:
            return []

        chunks = list(
            DocumentChunk.objects
            .filter(
                document__project=project,
                document__status="completed",
                embedding__isnull=False,
            )
            .select_related("document")
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")
            [:top_k]
        )

        # Filter by relevance threshold
        relevant = [c for c in chunks if c.distance <= self.RELEVANCE_THRESHOLD]

        logger.info(
            f"Guardian doc search: {len(chunks)} candidates, "
            f"{len(relevant)} above threshold ({self.RELEVANCE_THRESHOLD})"
        )

        return relevant

    def _evaluate(
        self,
        proposal: ModificationProposal,
        chunks: list[DocumentChunk],
    ) -> dict:
        """
        LLM pass: given proposal + relevant doc chunks, classify the verdict.
        """
        intent = proposal.intent_json or {}

        # Format document excerpts
        excerpts = []
        for chunk in chunks:
            excerpts.append(
                f"[{chunk.document.name}, Page {chunk.page_number or '?'}]\n"
                f"{chunk.content}"
            )
        doc_excerpts = "\n\n---\n\n".join(excerpts) if excerpts else "(none)"

        # Determine property or attribute label
        prop_or_attr = (
            f"{intent.get('pset', '')}.{intent.get('property', '')}"
            if intent.get("property")
            else intent.get("attribute", "N/A")
        )

        messages = [
            SystemMessage(content=GUARDIAN_SYSTEM_PROMPT),
            HumanMessage(content=GUARDIAN_USER_TEMPLATE.format(
                ifc_type=intent.get("filter", {}).get("ifc_type", "Unknown"),
                operation=intent.get("operation", proposal.operation),
                property_or_attribute=prop_or_attr,
                new_value=intent.get("new_value", "N/A"),
                explanation=proposal.explanation,
                doc_excerpts=doc_excerpts,
            )),
        ]

        response = self.llm.invoke(messages)

        try:
            result = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning(f"Guardian LLM returned invalid JSON: {response.content}")
            return {
                "verdict": "NO_INFO",
                "explanation": "Could not parse verification result.",
                "source_detail": "",
            }

        return result

    @staticmethod
    def _camel_to_words(name: str) -> str:
        """Convert CamelCase to space-separated words. 'FireRating' → 'fire rating'."""
        words = re.sub(r"([A-Z])", r" \1", name).strip().lower()
        return words