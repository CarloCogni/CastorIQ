# chat/services/rag_service.py
import logging
from textwrap import dedent
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pgvector.django import CosineDistance

from chat.models import ChatSession, Message
from core.llm import get_llm, resolve_model_name
from core.token_budget import compute_budget
from core.token_utils import estimate_tokens
from documents.models import Document, DocumentChunk
from embeddings.services.embedding_service import EmbeddingService
from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = dedent("""\
    You are Castor, an AI assistant specialized in BIM (Building Information Modeling) \
    and AEC (Architecture, Engineering, Construction) project analysis.

    You work with two types of data:
    - IFC MODEL DATA: Structured building elements (walls, doors, slabs...) with properties, \
    spatial locations, and technical attributes extracted from IFC files.
    - DOCUMENT DATA: Text excerpts from project documents (contracts, specifications, reports) \
    with page references.

    Rules:
    1. ONLY answer based on the provided excerpts. Never invent data.
    2. If the excerpts don't contain the answer, say: \
    "I could not find this information in the uploaded project files."
    3. When citing, reference the source: "According to [Document Name, Page X]..." \
    or "The IFC model shows [Entity Name]..."
    4. Use precise AEC terminology (GlobalID, PropertySet, BuildingStorey, etc.).
    5. Format answers in clean Markdown. Use tables for comparisons, lists for enumerations.
    6. For quantities and measurements, always include units when available.
    7. If asked about conflicts between IFC and documents, highlight both values clearly.\
""")


class RAGService:
    """
    RAG Service with Intent-Aware Retrieval.

    Improvements over original:
    1. Ported the robust Prompt Engineering from the Streamlit prototype.
    2. Added "Summary Mode" retrieval (fetches Intro + Conclusion chunks instead of vector search).
    3. Better context formatting.
    """

    def __init__(self, user=None):
        self.embedding_service = EmbeddingService()
        self.llm = get_llm(user=user, temperature=0.2)
        self.model_name = resolve_model_name(user)

    def generate_answer(
        self, project, session: ChatSession, user_text: str, scope: str = "auto"
    ) -> tuple[str, list, float]:
        """
        Pure retrieval + generation. No DB writes.

        Returns:
            Tuple of (answer_text, context_items, utilization_pct) where
            utilization_pct is the token budget usage as a 0–100 float.
        """
        is_summary_request = any(
            w in user_text.lower()
            for w in [
                "summarize",
                "summary",
                "overview",
                "explain the document",
                "what is this file",
            ]
        )

        if is_summary_request and scope != "ifc":
            context_items = self._retrieve_summary_context(project)
            analysis_mode = "General Summary"
        else:
            context_items = self._retrieve_vector_context(project, user_text, scope)
            analysis_mode = "Specific Q&A"

        project_meta = self._get_project_metadata(project)

        answer_text, utilization_pct = self._generate_response(
            user_text, context_items, project_meta, analysis_mode, session
        )

        return answer_text, context_items, utilization_pct

    def _retrieve_vector_context(self, project, query: str, scope: str) -> list[Any]:
        """Standard Vector Search for specific queries."""
        query_vector = self.embedding_service.embed_query(query)
        if not query_vector:
            return []

        ifc_hits = []
        doc_hits = []

        # We fetch slightly more candidates to ensure quality
        if scope in ["auto", "ifc"]:
            ifc_hits = list(
                IFCEntity.objects.filter(ifc_file__project=project, embedding__isnull=False)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance")[:5]
            )

        if scope in ["auto", "docs"]:
            doc_hits = list(
                DocumentChunk.objects.filter(document__project=project, embedding__isnull=False)
                .select_related("document")
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance")[:5]
            )

        # Merge and sort by relevance (distance)
        results = ifc_hits + doc_hits
        results.sort(key=lambda x: x.distance)

        return results[:8]  # Return top 8 mixed results

    def _retrieve_summary_context(self, project) -> list[DocumentChunk]:
        """
        Retrieval Strategy for Summaries:
        Vector search is bad at "summarize this".
        Instead, we grab the first few chunks (Intro) and the last chunk (Conclusion/Signatures)
        of the documents.
        """
        logger.info("Triggering Summary Retrieval Strategy")

        documents = project.documents.filter(status=Document.Status.COMPLETED)
        summary_chunks = []

        for doc in documents:
            # Get first 3 chunks (Introduction / Preamble)
            head_chunks = list(doc.chunks.all().order_by("chunk_index")[:3])
            # Get last 2 chunks (Conclusion / Signatures / Schedules)
            tail_chunks = list(doc.chunks.all().order_by("-chunk_index")[:2])

            summary_chunks.extend(head_chunks)
            summary_chunks.extend(tail_chunks)

        # Deduplicate just in case
        return list(dict.fromkeys(summary_chunks))

    def _get_project_metadata(self, project) -> str:
        """Generates the 'God View' of the project files."""
        ifc_files = project.ifc_files.filter(status="completed").values_list("name", flat=True)
        docs = project.documents.filter(status="completed").values_list("name", flat=True)

        meta_parts = []
        if ifc_files:
            meta_parts.append(f"MODELS: {', '.join(ifc_files)}")
        if docs:
            meta_parts.append(f"DOCUMENTS: {', '.join(docs)}")

        return " | ".join(meta_parts) if meta_parts else "No files active."

    def _serialize_context(self, context_items, scope: str = "auto") -> dict:
        """Convert retrieval results to JSON for UI display."""
        sources = []
        sorted_items = sorted(context_items, key=lambda x: 0 if isinstance(x, DocumentChunk) else 1)

        for item in sorted_items:
            if isinstance(item, IFCEntity):
                sources.append(
                    {
                        "type": "ifc",
                        "id": str(item.id),
                        "name": item.name or item.ifc_type,
                        "ifc_type": item.ifc_type,
                        "storey": item.building_storey or "",
                        "global_id": item.global_id or "",
                        "preview": (item.description[:120] + "...")
                        if len(item.description) > 120
                        else item.description,
                        "full_text": item.description,
                    }
                )
            elif isinstance(item, DocumentChunk):
                sources.append(
                    {
                        "type": "doc",
                        "id": str(item.id),
                        "source": item.document.name,
                        "page": item.page_number,
                        "preview": (item.content[:120] + "...")
                        if len(item.content) > 120
                        else item.content,
                        "full_text": item.content,
                    }
                )

        return {
            "scope": scope,
            "source_count": len(sources),
            "sources": sources,
        }

    def _generate_response(
        self,
        query: str,
        context_items: list,
        project_meta: str,
        analysis_mode: str,
        session: ChatSession,
    ) -> tuple[str, float]:
        """
        Build the full prompt with context, history, and query.

        Uses dynamic token budgeting to fit history within the model's
        context window. Returns both the answer text and utilization %.
        """

        # --- 1. Format retrieved context ---
        context_str = self._format_context(context_items)

        # --- 2. Mode-specific instruction ---
        if analysis_mode == "General Summary":
            mode_instruction = (
                "The user wants a high-level overview. Focus on: document purpose, "
                "key parties, dates, scope of work, and main obligations or risks."
            )
        else:
            mode_instruction = (
                "Answer the specific question directly. Use the excerpts as evidence. "
                "Quote relevant values and properties when available."
            )

        # --- 3. Compute preliminary budget (no history) to size history allowance ---
        prelim = compute_budget(
            self.model_name,
            system=SYSTEM_PROMPT,
            entity_context=context_str,
        )
        history_budget_tokens = prelim.remaining_for_injection

        # --- 4. Build history within budget ---
        history_str = self._build_history_within_budget(session, history_budget_tokens)

        # --- 5. Final budget for logging and utilization reporting ---
        final_budget = compute_budget(
            self.model_name,
            system=SYSTEM_PROMPT,
            entity_context=context_str,
            injected=history_str,
        )
        logger.debug(
            "RAG token budget — model=%s util=%.0f%% total=%d/%d",
            self.model_name,
            final_budget.utilization_pct,
            final_budget.total_input,
            final_budget.max_usable,
        )

        # --- 6. Assemble prompt ---
        template = (
            "{system_prompt}\n\n"
            "ANALYSIS MODE: {analysis_mode}\n"
            "{mode_instruction}\n\n"
            "=== PROJECT FILES ===\n"
            "{project_meta}\n\n"
            "=== RETRIEVED EXCERPTS ===\n"
            "{context_str}\n\n"
            "{history_block}"
            "=== CURRENT QUESTION ===\n"
            "{question}\n\n"
            "=== ANSWER ==="
        )

        history_block = ""
        if history_str:
            history_block = f"=== RECENT CONVERSATION ===\n{history_str}\n\n"

        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.llm | StrOutputParser()

        answer = chain.invoke(
            {
                "system_prompt": SYSTEM_PROMPT,
                "analysis_mode": analysis_mode,
                "mode_instruction": mode_instruction,
                "project_meta": project_meta,
                "context_str": context_str,
                "history_block": history_block,
                "question": query,
            }
        )

        return answer, final_budget.utilization_pct

    def _format_context(self, context_items: list) -> str:
        """Format retrieved IFC entities and document chunks into prompt text."""
        if not context_items:
            return "No relevant excerpts found."

        parts = []
        for item in context_items:
            if isinstance(item, IFCEntity):
                parts.append(
                    f"[IFC: {item.name or 'Unnamed'} ({item.ifc_type}) — "
                    f"Storey: {item.building_storey or 'N/A'}]\n"
                    f"{item.description}"
                )
            elif isinstance(item, DocumentChunk):
                parts.append(
                    f"[DOC: {item.document.name} — Page {item.page_number or '?'}]\n{item.content}"
                )

        return "\n\n---\n\n".join(parts)

    def _build_history_within_budget(self, session: ChatSession, max_history_tokens: int) -> str:
        """
        Build conversation history that fits within a token budget.

        Walks messages from most-recent backwards, adding exchanges until
        the next message would exceed ``max_history_tokens``. This replaces
        the fixed 3-exchange cap with a token-aware limit.
        """
        recent_msgs = list(
            session.messages.filter(role__in=[Message.Role.USER, Message.Role.ASSISTANT]).order_by(
                "-created_at"
            )[:20]
        )

        if not recent_msgs:
            return ""

        recent_msgs.reverse()

        lines = []
        total_tokens = 0

        for msg in recent_msgs:
            content = msg.content[:500]
            if len(msg.content) > 500:
                content += "..."
            role_label = "USER" if msg.role == Message.Role.USER else "CASTOR"
            line = f"{role_label}: {content}"
            msg_tokens = estimate_tokens(line)

            if total_tokens + msg_tokens > max_history_tokens:
                break

            lines.append(line)
            total_tokens += msg_tokens

        return "\n".join(lines)

    def _format_history(
        self, session: ChatSession, max_exchanges: int = 3, max_chars_per_msg: int = 300
    ) -> str:
        """
        Format recent conversation history for context.

        Fetches the last N exchanges (user+assistant pairs), truncates long
        messages, and formats them as a readable block. Excludes the current
        user message (which hasn't been saved yet at generation time in the
        refactored flow, or is the last one).

        Token budget: ~800 tokens for 3 exchanges at 300 chars each.
        """
        recent_msgs = list(
            session.messages.filter(role__in=[Message.Role.USER, Message.Role.ASSISTANT]).order_by(
                "-created_at"
            )[: max_exchanges * 2]
        )

        if not recent_msgs:
            return ""

        # Reverse to chronological order
        recent_msgs.reverse()

        lines = []
        for msg in recent_msgs:
            content = msg.content[:max_chars_per_msg]
            if len(msg.content) > max_chars_per_msg:
                content += "..."

            role_label = "USER" if msg.role == Message.Role.USER else "CASTOR"
            lines.append(f"{role_label}: {content}")

        return "\n".join(lines)
