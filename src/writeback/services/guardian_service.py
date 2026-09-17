# writeback/services/guardian_service.py
"""
Castor Guardian — Document cross-reference for modification proposals.

When a user proposes an IFC modification, the Guardian searches project
documents (specs, fire reports, contracts) for relevant information and
flags potential conflicts before the user approves.

This is the "second opinion" that makes Castor bidirectional:
  IFC ←→ Documents

V3: the search queries and the LLM prompt are built from the proposal's
measured diff and its request text (spec A-4), not from an intent structure.
:func:`build_guardian_query` reads the dominant aggregated row;
:func:`build_request_query` is the request text, capped, added 2026-09-17
after a real case (proposal 9173ae8f) showed the diff-row query alone misses
a requirement stated in the request's own words — a document citation, a
term in another language, anything the diff row cannot reconstruct from a
type/property/value triple. Results from both queries are unioned by chunk
id, keeping the best (lowest) distance per chunk, before the existing
relevance threshold applies.
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from langchain_core.messages import HumanMessage, SystemMessage
from pgvector.django import CosineDistance

from core.llm import get_llm, safe_invoke
from documents.models import DocumentChunk
from embeddings.services.embedding_service import EmbeddingService
from ifc_processor.models import IFCEntity
from writeback.models import ModificationProposal
from writeback.services.generator import NUM_CTX
from writeback.services.verifier import DiffRow, aggregate_rows, camel_to_words

logger = logging.getLogger(__name__)

#: Hard wall-clock cap on the verdict call, like the explainer's: a stalled
#: stream must not hold the pipeline thread. A timeout is a FAILED verdict.
CALL_TIMEOUT_SECONDS = 90

# ────────────────────────────────────────────────────────────
# Guardian LLM Prompt
# ────────────────────────────────────────────────────────────
GUARDIAN_SYSTEM_PROMPT = """\
You are the Castor Guardian, a verification assistant for BIM/IFC modifications.

You receive:
1. A PROPOSED CHANGE to an IFC model (entity type, property, value, count).
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
- Change: {change}
- Explanation: {explanation}

## Document Excerpts
{doc_excerpts}

## Task
Analyze whether these documents confirm, conflict with, or say nothing about this proposed change. Return JSON only.
"""


def build_guardian_query(proposal: ModificationProposal) -> str:
    """The primary document search query, from the dominant aggregated diff row.

    "wall" + "fire rating" + "EI60" → ``wall fire rating EI60``. Falls back to
    the explanation, then the request, when the diff has no property row.
    Precise, but built from nothing the user actually wrote — see
    :func:`build_request_query` for the second query that covers that gap.
    """
    row = dominant_row(proposal)
    if row is None:
        return proposal.explanation or proposal.request_text
    parts = [natural_type(entity_type_of(proposal, row))]
    if row.prop:
        parts.append(camel_to_words(row.prop))
    if isinstance(row.after, str) and row.after:
        parts.append(row.after)
    return " ".join(p for p in parts if p).strip() or proposal.request_text


#: A search query embeds best short; the request text is free-form user input,
#: so it is capped rather than sent in full. 512 characters comfortably covers
#: any real Modify request (the case that motivated this — a request carrying
#: a full document citation — was ~190 chars) with headroom, while bounding a
#: pasted paragraph that would otherwise dilute the query's own vector and
#: cost more to embed than a one-sentence request ever needs.
REQUEST_QUERY_CAP = 512


def build_request_query(proposal: ModificationProposal) -> str:
    """The request text as a second search query, capped at :data:`REQUEST_QUERY_CAP`.

    Lets the request's own words — a document name, a clause, a term in
    another language — compete for the same chunks the diff-row query
    cannot reach on its own.
    """
    return (proposal.request_text or "").strip()[:REQUEST_QUERY_CAP]


def dominant_row(proposal: ModificationProposal) -> DiffRow | None:
    """The property or attribute row touching the most entities, if any."""
    rows = [r for r in aggregate_rows(proposal.diff or {}) if r.kind in ("property", "attribute")]
    return max(rows, key=lambda r: r.count) if rows else None


def entity_type_of(proposal: ModificationProposal, row: DiffRow) -> str:
    """The most common IFC type among the row's entities, read from the index."""
    types = IFCEntity.objects.filter(
        ifc_file=proposal.ifc_file, global_id__in=row.global_ids
    ).values_list("ifc_type", flat=True)
    common = Counter(types).most_common(1)
    return common[0][0] if common else ""


def natural_type(ifc_type: str) -> str:
    """'IfcWallStandardCase' → 'wall standard case'; '' → ''."""
    return camel_to_words(ifc_type[3:]) if ifc_type.startswith("Ifc") else camel_to_words(ifc_type)


class GuardianService:
    """
    Cross-references a ModificationProposal against project documents.

    Flow:
        1. Build two targeted search queries: the dominant diff row, and the
           request text (capped)
        2. Vector-search DocumentChunks with both, unioned by chunk id and
           deduped keeping the best distance (docs only, not IFC entities)
        3. If relevant chunks found → LLM pass to classify verdict
        4. Save results to proposal.verification_* fields

    Usage:
        guardian = GuardianService()
        guardian.check(proposal)  # mutates proposal in-place and saves
    """

    # Minimum similarity threshold — chunks less relevant than this are skipped
    RELEVANCE_THRESHOLD = 0.45  # cosine distance (lower = more similar)

    def __init__(self, user=None):
        """``user`` is the requesting user: per-user provider overrides, the
        token budget and the call log follow the same rules as the code call."""
        self.embedding_service = EmbeddingService()
        # The Modify model, with the Modify context size: a request then runs
        # on one loaded Ollama runner for code, explanation and verdict.
        self.llm = get_llm(
            user=user, purpose="modify", temperature=0.1, format_json=True, num_ctx=NUM_CTX
        )

    def check(self, proposal: ModificationProposal) -> ModificationProposal:
        """
        Run the guardian check on a proposal. Saves results to DB.

        Args:
            proposal: A ModificationProposal with its diff populated.

        Returns:
            The same proposal, updated with verification_* fields.
        """
        try:
            project = proposal.ifc_file.project

            diff_query = build_guardian_query(proposal)
            request_query = build_request_query(proposal)
            logger.info(f"Guardian search queries: diff={diff_query!r} request={request_query!r}")

            chunks = self._search_documents(project, [diff_query, request_query])

            if not chunks:
                proposal.verification_status = ModificationProposal.VerificationStatus.UNKNOWN
                proposal.verification_result = "No relevant information found in project documents."
                proposal.verification_source = ""
                proposal.save(
                    update_fields=[
                        "verification_status",
                        "verification_result",
                        "verification_source",
                    ]
                )
                logger.info(f"Guardian: no relevant docs for proposal {proposal.id}")
                return proposal

            verdict = self._evaluate(proposal, chunks)

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

            proposal.save(
                update_fields=[
                    "verification_status",
                    "verification_result",
                    "verification_source",
                ]
            )

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

    def _search_documents(
        self,
        project,
        queries: list[str],
        top_k: int = 5,
    ) -> list[DocumentChunk]:
        """
        Vector search against project document chunks only, unioned across queries.

        Each distinct, non-empty query is embedded and searched independently
        (top_k nearest each); a chunk found by more than one query keeps its
        best (lowest) distance. The existing top_k and relevance threshold
        apply to the merged result, so the worst-case prompt size to
        :meth:`_evaluate` is unchanged from a single-query search. Two equal
        query strings are embedded once, not twice.
        """
        best: dict[int, DocumentChunk] = {}
        embedded: set[str] = set()

        for query in queries:
            query = (query or "").strip()
            if not query or query in embedded:
                continue
            embedded.add(query)

            query_vector = self.embedding_service.embed_query(query)
            if not query_vector:
                continue

            candidates = (
                DocumentChunk.objects.filter(
                    document__project=project,
                    document__status="completed",
                    embedding__isnull=False,
                )
                .select_related("document")
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance")[:top_k]
            )
            for chunk in candidates:
                existing = best.get(chunk.id)
                if existing is None or chunk.distance < existing.distance:
                    best[chunk.id] = chunk

        ranked = sorted(best.values(), key=lambda c: c.distance)
        relevant = [c for c in ranked if c.distance <= self.RELEVANCE_THRESHOLD][:top_k]

        logger.info(
            f"Guardian doc search: {len(embedded)} quer{'y' if len(embedded) == 1 else 'ies'} embedded, "
            f"{len(best)} unique candidate(s), {len(relevant)} above threshold ({self.RELEVANCE_THRESHOLD})"
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
        excerpts = [
            f"[{chunk.document.name}, Page {chunk.page_number or '?'}]\n{chunk.content}"
            for chunk in chunks
        ]
        doc_excerpts = "\n\n---\n\n".join(excerpts) if excerpts else "(none)"

        row = dominant_row(proposal)
        ifc_type = natural_type(entity_type_of(proposal, row)) if row else "Unknown"
        change = (
            f"{row.label}: {row.before!r} → {row.after!r} on {row.count} entities"
            if row
            else proposal.request_text
        )

        messages = [
            SystemMessage(content=GUARDIAN_SYSTEM_PROMPT),
            HumanMessage(
                content=GUARDIAN_USER_TEMPLATE.format(
                    ifc_type=ifc_type or "Unknown",
                    change=change,
                    explanation=proposal.explanation,
                    doc_excerpts=doc_excerpts,
                )
            ),
        ]

        response = safe_invoke(self.llm.invoke, messages, timeout=CALL_TIMEOUT_SECONDS)

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning(f"Guardian LLM returned invalid JSON: {response.content}")
            return {
                "verdict": "NO_INFO",
                "explanation": "Could not parse verification result.",
                "source_detail": "",
            }
