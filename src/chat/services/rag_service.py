# chat/services/rag_service.py
import logging
from textwrap import dedent
from typing import Any

from django.db.models import Count
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pgvector.django import CosineDistance

from chat.models import ChatSession, Message
from core.llm import get_llm, resolve_model_name
from core.token_budget import compute_budget, compute_retrieval_budget
from core.token_utils import estimate_tokens
from documents.models import Document, DocumentChunk
from embeddings.services.embedding_service import EmbeddingService
from ifc_processor.models import IFCEntity, IFCFile

logger = logging.getLogger(__name__)

# Auto-compaction at the 80% history-budget threshold. Currently dormant:
# during early development of the compaction UX we want compaction to fire
# only via the manual button in _ask.html so the behaviour is observable
# and reproducible. Flip to True to restore the safety net.
_AUTO_COMPACT_ENABLED = False


def _get_entity_storey_name(entity: IFCEntity) -> str:
    """Walk the spatial_container chain to find the storey name, or return empty."""
    node = getattr(entity, "spatial_container", None)
    while node:
        if node.spatial_type == "building_storey":
            return node.entity.name if hasattr(node, "entity") and node.entity else ""
        node = node.parent
    return ""


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

_IFC_INVENTORY_KEYWORDS: frozenset[str] = frozenset(
    {
        "what elements",
        "element types",
        "what types",
        "list all",
        "how many",
        "what ifc",
        "building elements",
        "element count",
        "inventory",
        "all elements",
        "types of elements",
        "what kind of elements",
    }
)

_DOC_SUMMARY_KEYWORDS: frozenset[str] = frozenset(
    {
        "summarize",
        "summary",
        "overview",
        "explain the document",
        "what is this file",
    }
)

# Facilities (7D FM) asset-query keywords — routes to a DB-backed answer path
# that injects FacilityAsset rows into the prompt instead of relying on IFC
# vector search. Registered-in-project assets carry FM metadata (tag,
# manufacturer, warranty) that vectors over entity descriptions cannot surface.
_FM_ASSET_KEYWORDS: frozenset[str] = frozenset(
    {
        "which assets",
        "list assets",
        "list the assets",
        "asset inventory",
        "assets in",
        "assets with",
        "assets under warranty",
        "asset register",
        "facility asset",
        "facility assets",
    }
)


class RAGService:
    """
    RAG Service with Intent-Aware Retrieval.

    Retrieval is routed by intent and scope:
    - ifc_inventory + auto/ifc  → aggregate GROUP BY query (every element type represented)
    - doc_summary + auto        → IFC aggregate + document head/tail chunks
    - doc_summary + docs        → document head/tail chunks only
    - specific_qa / fallback    → cosine similarity vector search
    """

    def __init__(self, user=None):
        self._user = user
        self.embedding_service = EmbeddingService()
        self.llm = get_llm(user=user, purpose="ask", temperature=0.2)
        self.model_name = resolve_model_name(user, purpose="ask")

    @staticmethod
    def _emit(
        emitter: Any,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Emit a pipeline progress event if an emitter is provided."""
        if emitter is not None:
            emitter.emit(phase, status, message, detail)

    def generate_answer(
        self,
        project,
        session: ChatSession,
        user_text: str,
        scope: str = "auto",
        emitter: Any = None,
    ) -> tuple[str, list, float]:
        """
        Pure retrieval + generation. No DB writes.

        Args:
            project: The Project instance to query against.
            session: The active ChatSession for history context.
            user_text: The user's natural language question.
            scope: Search scope — "auto", "ifc", or "docs".
            emitter: Optional PipelineEmitter for streaming progress events.

        Returns:
            Tuple of (answer_text, context_items, utilization_pct) where
            utilization_pct is the token budget usage as a 0–100 float.

        Raises:
            CancellationError: If the client cancels mid-pipeline.
        """

        self._emit(emitter, "intent", "running", "Classifying intent...")
        self._check_cancelled(emitter)
        intent = self._detect_intent(user_text)
        aggregate_context_str = ""

        self._emit(emitter, "retrieve", "running", "Retrieving context...")
        self._check_cancelled(emitter)

        # Compute dynamic retrieval budget based on available context window
        history_estimate = self._estimate_session_history_tokens(session)
        retrieval_budget = compute_retrieval_budget(
            self.model_name, SYSTEM_PROMPT, history_tokens=history_estimate
        )

        if intent in ("ifc_inventory", "doc_summary") and scope == "ifc":
            # User restricted scope to IFC — any summary/inventory query uses aggregate
            aggregate_rows = self._retrieve_ifc_aggregate_context(project)
            if aggregate_rows:
                aggregate_context_str = self._format_ifc_aggregate_context(aggregate_rows)
                context_items: list = []
                analysis_mode = "IFC Inventory Summary"
            else:
                context_items = self._retrieve_vector_context(
                    project, user_text, scope, budget_tokens=retrieval_budget
                )
                analysis_mode = "Specific Q&A"
        elif intent == "ifc_inventory" and scope == "auto":
            aggregate_rows = self._retrieve_ifc_aggregate_context(project)
            if aggregate_rows:
                aggregate_context_str = self._format_ifc_aggregate_context(aggregate_rows)
                context_items = []
                analysis_mode = "IFC Inventory Summary"
            else:
                context_items = self._retrieve_vector_context(
                    project, user_text, scope, budget_tokens=retrieval_budget
                )
                analysis_mode = "Specific Q&A"
        elif intent == "doc_summary" and scope == "auto":
            # Combined: IFC aggregate + document summary chunks
            aggregate_rows = self._retrieve_ifc_aggregate_context(project)
            aggregate_context_str = (
                self._format_ifc_aggregate_context(aggregate_rows) if aggregate_rows else ""
            )
            context_items = self._retrieve_summary_context(project)
            analysis_mode = "General Summary"
        elif intent == "doc_summary" and scope == "docs":
            context_items = self._retrieve_summary_context(project)
            analysis_mode = "General Summary"
        elif intent == "fm_asset_query":
            # 7D Facilities asset queries — DB-backed answer path. Vector
            # search over IFCEntity descriptions cannot surface FM metadata
            # like manufacturer, warranty, or responsible party; inject
            # matching FacilityAsset rows as a structured context blob.
            aggregate_context_str = self._format_fm_asset_context(
                self._retrieve_fm_asset_context(project, user_text)
            )
            context_items = []
            analysis_mode = "FM Asset Register"
        else:
            context_items = self._retrieve_vector_context(
                project, user_text, scope, budget_tokens=retrieval_budget
            )
            analysis_mode = "Specific Q&A"

        self._emit(emitter, "intent", "done", analysis_mode)
        retrieve_detail = (
            f"Found {len(context_items)} result{'s' if len(context_items) != 1 else ''}"
            if context_items
            else analysis_mode
        )
        self._emit(emitter, "retrieve", "done", retrieve_detail)
        self._check_cancelled(emitter)

        project_meta = self._get_project_metadata(project)

        # Auto-compact when history consumes too much of the budget.
        # Gated by _AUTO_COMPACT_ENABLED — currently False so compaction only
        # runs from the manual button in _ask.html. See module-level flag.
        if (
            _AUTO_COMPACT_ENABLED
            and retrieval_budget > 0
            and history_estimate > retrieval_budget * 0.8
        ):
            from chat.services.compaction_service import CompactionService

            compactor = CompactionService(user=self._user)
            compactor.compact_session(session, emitter=emitter)

        self._emit(emitter, "generate", "running", "Generating answer...")
        self._check_cancelled(emitter)  # Last chance to cancel before LLM call
        answer_text, utilization_pct = self._generate_response(
            user_text,
            context_items,
            project_meta,
            analysis_mode,
            session,
            aggregate_context_str=aggregate_context_str,
        )
        self._emit(emitter, "generate", "done", "Answer ready")

        return answer_text, context_items, utilization_pct

    @staticmethod
    def _check_cancelled(emitter: Any) -> None:
        """Raise CancellationError if the emitter signals cancellation."""
        from writeback.services.emitters import CancellationError

        if emitter is not None and hasattr(emitter, "is_cancelled") and emitter.is_cancelled():
            raise CancellationError("Pipeline cancelled by user.")

    def _detect_intent(self, user_text: str) -> str:
        """
        Classify query into one of the retrieval intents.

        FM asset keywords are checked first (most specific), then IFC inventory,
        then document summary. Everything else falls through to specific Q&A.

        Returns:
            "fm_asset_query" — FacilityAsset register queries (7D FM)
            "ifc_inventory"  — aggregate/count questions about IFC element types
            "doc_summary"    — high-level document overview requests
            "specific_qa"    — default for targeted questions
        """
        lowered = user_text.lower()
        if any(kw in lowered for kw in _FM_ASSET_KEYWORDS):
            return "fm_asset_query"
        if any(kw in lowered for kw in _IFC_INVENTORY_KEYWORDS):
            return "ifc_inventory"
        if any(kw in lowered for kw in _DOC_SUMMARY_KEYWORDS):
            return "doc_summary"
        return "specific_qa"

    _FM_ASSET_ROW_LIMIT = 25

    def _retrieve_fm_asset_context(self, project, query: str) -> list:
        """Return up to ``_FM_ASSET_ROW_LIMIT`` facility assets matching ``query``.

        Uses :class:`facilities.services.AssetService` for consistent scoping
        and eager-loading; this keeps the RAG layer decoupled from the asset
        model's internals.
        """
        # Deferred import: RAG does not require the facilities app to be
        # installed at import time, and the circular dep risk is non-zero.
        from facilities.services import AssetService

        service = AssetService(project=project, user=self._user)
        return list(service.list_assets(q=query)[: self._FM_ASSET_ROW_LIMIT])

    @staticmethod
    def _format_fm_asset_context(assets: list) -> str:
        """Render a concise text block describing the matched assets."""
        if not assets:
            return (
                "FM ASSET REGISTER: No matching assets found. Answer from the "
                "user's question directly and suggest they check the Facilities "
                "tab if they expected results."
            )

        lines = ["FM ASSET REGISTER — matching assets:"]
        for asset in assets:
            entity = asset.ifc_entity
            parts: list[str] = []
            if asset.asset_tag:
                parts.append(f"Tag={asset.asset_tag}")
            if asset.manufacturer or asset.model_number:
                parts.append(
                    "Manufacturer="
                    + "/".join(x for x in [asset.manufacturer, asset.model_number] if x)
                )
            if asset.serial_number:
                parts.append(f"Serial={asset.serial_number}")
            if asset.condition_score is not None:
                parts.append(f"Condition={asset.condition_score}/100")
            if asset.warranty_end:
                parts.append(f"WarrantyEnd={asset.warranty_end.isoformat()}")
            if entity is not None:
                parts.append(f"IFCType={entity.ifc_type}")
                if entity.spatial_container and entity.spatial_container.entity:
                    parts.append(
                        f"Location={entity.spatial_container.entity.name or entity.spatial_container.spatial_type}"
                    )
            lines.append("- " + ", ".join(parts))
        return "\n".join(lines)

    # Retrieval depth guardrails
    _MIN_CANDIDATES_PER_SOURCE = 8
    _MAX_CANDIDATES_PER_SOURCE = 30

    def _retrieve_vector_context(
        self, project, query: str, scope: str, budget_tokens: int = 0
    ) -> list[Any]:
        """
        Budget-aware vector search for specific queries.

        Fetches a generous ceiling of candidates from pgvector, then greedily
        fills the token budget in relevance order. When ``budget_tokens`` is 0
        (legacy/fallback), behaves like a fixed top-8 retrieval.
        """
        query_vector = self.embedding_service.embed_query(query)
        if not query_vector:
            return []

        # Determine how many candidates to fetch per source
        if budget_tokens > 0:
            ceiling = min(
                self._MAX_CANDIDATES_PER_SOURCE,
                max(self._MIN_CANDIDATES_PER_SOURCE, budget_tokens // 200),
            )
        else:
            ceiling = self._MIN_CANDIDATES_PER_SOURCE

        ifc_hits: list = []
        doc_hits: list = []

        if scope in ["auto", "ifc"]:
            ifc_hits = list(
                IFCEntity.objects.filter(ifc_file__project=project, embedding__isnull=False)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance")[:ceiling]
            )

        if scope in ["auto", "docs"]:
            doc_hits = list(
                DocumentChunk.objects.filter(document__project=project, embedding__isnull=False)
                .select_related("document")
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance")[:ceiling]
            )

        # Merge and sort by relevance (distance)
        candidates = ifc_hits + doc_hits
        candidates.sort(key=lambda x: x.distance)

        if budget_tokens <= 0:
            return candidates[:8]

        # Greedy trim: fill budget in relevance order, skip items that don't fit
        selected: list = []
        used_tokens = 0
        for item in candidates:
            item_tokens = estimate_tokens(self._format_single_item(item))
            if used_tokens + item_tokens <= budget_tokens:
                selected.append(item)
                used_tokens += item_tokens

        logger.debug(
            "Dynamic retrieval: %d/%d candidates selected, %d/%d tokens used",
            len(selected),
            len(candidates),
            used_tokens,
            budget_tokens,
        )
        return selected

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

    def _retrieve_ifc_aggregate_context(self, project) -> list[dict]:
        """
        Build an aggregate inventory of IFC entities grouped by type.

        Uses GROUP BY instead of cosine similarity so every element type present
        in the model is represented, not just the top-5 most semantically similar.

        Returns a list of dicts with keys:
            ifc_type, filename, count,
            representative_name, representative_storey, representative_properties
        Returns [] when no completed IFC files exist for the project.
        """
        if not project.ifc_files.filter(status=IFCFile.Status.COMPLETED).exists():
            logger.info("No completed IFC files for project %s — skipping aggregate", project.pk)
            return []

        type_counts = (
            IFCEntity.objects.filter(
                ifc_file__project=project,
                ifc_file__status=IFCFile.Status.COMPLETED,
            )
            .values("ifc_type", "ifc_file__name")
            .annotate(count=Count("id"))
            .order_by("ifc_type")
        )

        results = []
        for row in type_counts:
            representative = (
                IFCEntity.objects.filter(
                    ifc_file__project=project,
                    ifc_file__name=row["ifc_file__name"],
                    ifc_type=row["ifc_type"],
                )
                .order_by("name")
                .first()
            )
            results.append(
                {
                    "ifc_type": row["ifc_type"],
                    "filename": row["ifc_file__name"],
                    "count": row["count"],
                    "representative_name": representative.name if representative else "",
                    "representative_storey": _get_entity_storey_name(representative)
                    if representative
                    else "",
                    "representative_properties": representative.properties
                    if representative
                    else {},
                }
            )

        return results

    def _format_ifc_aggregate_context(self, aggregate_rows: list[dict]) -> str:
        """
        Format aggregate IFC inventory as a structured text block for the LLM prompt.

        Groups by filename when multiple IFC files exist in the project.
        Returns a sentinel string when aggregate_rows is empty.
        """
        if not aggregate_rows:
            return "No IFC entities found."

        grouped: dict[str, list[dict]] = {}
        for row in aggregate_rows:
            grouped.setdefault(row["filename"], []).append(row)

        sections = ["[IFC INVENTORY SUMMARY]"]
        for filename, rows in grouped.items():
            sections.append(f"File: {filename}")
            for row in rows:
                line = f"  - {row['ifc_type']}: {row['count']} elements"
                if row["representative_name"]:
                    rep = f"representative: '{row['representative_name']}'"
                    if row["representative_storey"]:
                        rep += f", {row['representative_storey']}"
                    line += f" ({rep})"
                sections.append(line)

        return "\n".join(sections)

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
                        "storey": _get_entity_storey_name(item),
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
        aggregate_context_str: str = "",
    ) -> tuple[str, float]:
        """
        Build the full prompt with context, history, and query.

        Uses dynamic token budgeting to fit history within the model's
        context window. Returns both the answer text and utilization %.

        When aggregate_context_str is provided, it is used as the context block
        (or prepended to formatted context_items when both are present).
        """

        # --- 1. Format retrieved context ---
        if aggregate_context_str and context_items:
            context_str = aggregate_context_str + "\n\n" + self._format_context(context_items)
        elif aggregate_context_str:
            context_str = aggregate_context_str
        else:
            context_str = self._format_context(context_items)

        # --- 2. Mode-specific instruction ---
        if analysis_mode == "General Summary":
            mode_instruction = (
                "The user wants a high-level overview. Focus on: document purpose, "
                "key parties, dates, scope of work, and main obligations or risks."
            )
        elif analysis_mode == "IFC Inventory Summary":
            mode_instruction = (
                "The user wants an inventory of all element types in the IFC model. "
                "Present the element types in a structured table or list. "
                "Include element counts and representative examples where available."
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

    @staticmethod
    def _format_single_item(item) -> str:
        """Format a single retrieval result (IFC entity or document chunk) as prompt text."""
        if isinstance(item, IFCEntity):
            return (
                f"[IFC: {item.name or 'Unnamed'} ({item.ifc_type}) — "
                f"Storey: {_get_entity_storey_name(item) or 'N/A'}]\n"
                f"{item.description}"
            )
        if isinstance(item, DocumentChunk):
            return f"[DOC: {item.document.name} — Page {item.page_number or '?'}]\n{item.content}"
        return ""

    def _format_context(self, context_items: list) -> str:
        """Format retrieved IFC entities and document chunks into prompt text."""
        if not context_items:
            return "No relevant excerpts found."

        return "\n\n---\n\n".join(self._format_single_item(item) for item in context_items)

    @staticmethod
    def _estimate_session_history_tokens(session: ChatSession, max_msgs: int = 20) -> int:
        """
        Lightweight estimate of how many tokens the conversation history will consume.

        Used to pre-allocate retrieval budget before the full history is formatted.
        Mirrors the truncation in ``_build_history_within_budget`` (500 chars/msg).
        """
        recent = session.messages.filter(
            role__in=[Message.Role.USER, Message.Role.ASSISTANT],
            compacted_at__isnull=True,
        ).order_by("-created_at")[:max_msgs]
        total = sum(estimate_tokens(m.content[:500]) for m in recent)

        # Add compaction summary tokens if one exists
        summary = session.messages.filter(
            role=Message.Role.SYSTEM,
            is_compaction_summary=True,
        ).first()
        if summary:
            total += estimate_tokens(summary.content)

        return total

    def _build_history_within_budget(self, session: ChatSession, max_history_tokens: int) -> str:
        """
        Build conversation history that fits within a token budget.

        If a compaction summary exists, it is prepended first. Then walks
        non-compacted messages from most-recent backwards, adding exchanges
        until the next message would exceed ``max_history_tokens``.
        """
        lines: list[str] = []
        total_tokens = 0

        # Include compaction summary if one exists
        summary = (
            session.messages.filter(
                role=Message.Role.SYSTEM,
                is_compaction_summary=True,
            )
            .order_by("-created_at")
            .first()
        )
        if summary:
            summary_line = f"[CONVERSATION SUMMARY]: {summary.content}"
            summary_tokens = estimate_tokens(summary_line)
            if summary_tokens <= max_history_tokens:
                lines.append(summary_line)
                total_tokens += summary_tokens

        # Fetch recent non-compacted messages
        recent_msgs = list(
            session.messages.filter(
                role__in=[Message.Role.USER, Message.Role.ASSISTANT],
                compacted_at__isnull=True,
            ).order_by("-created_at")[:20]
        )

        if not recent_msgs and not lines:
            return ""

        recent_msgs.reverse()

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
