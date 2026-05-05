# chat/services/compaction_service.py
"""
Conversation compaction service.

Summarizes older messages in a chat session into a compact system message,
freeing context window space for new retrieval and generation. Original
messages are marked with ``compacted_at`` (not deleted) to preserve the
audit trail.
"""

import logging
from typing import Any

from django.utils import timezone
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from chat.models import ChatSession, Message
from core.llm import get_llm

logger = logging.getLogger(__name__)

# Messages to keep uncompacted (the active conversation window)
KEEP_RECENT = 2

# Minimum messages before compaction is worthwhile
MIN_MESSAGES_TO_COMPACT = 3

_COMPACTION_PROMPT = ChatPromptTemplate.from_template(
    "Summarize the following conversation between a user and Castor (a BIM/AEC "
    "assistant). Preserve key facts, decisions, entity names, property values, "
    "and any information the user might reference in follow-up questions. "
    "Be concise but complete.\n\n"
    "{previous_summary}"
    "=== CONVERSATION ===\n"
    "{conversation}\n\n"
    "=== SUMMARY ==="
)


class CompactionService:
    """Summarizes old conversation messages to free context window space."""

    def __init__(self, user=None):
        # Compaction summarises chat context for the Ask pipeline — route via Ask provider.
        self.llm = get_llm(user=user, purpose="ask", temperature=0.0)

    @staticmethod
    def _emit(
        emitter: Any,
        phase: str,
        status: str,
        message: str,
    ) -> None:
        """Emit a pipeline progress event if an emitter is provided."""
        if emitter is not None:
            emitter.emit(phase, status, message)

    def compact_session(self, session: ChatSession, emitter: Any = None) -> int:
        """
        Summarize old messages into a compaction summary message.

        Keeps the most recent ``KEEP_RECENT`` messages uncompacted and
        summarizes everything older. Previous compaction summaries are
        replaced (only one active summary per session).

        Returns the count of messages compacted (0 if nothing to compact).
        """
        self._emit(emitter, "compact", "running", "Compacting conversation history...")

        # Fetch all non-compacted user/assistant messages
        messages = list(
            session.messages.filter(
                role__in=[Message.Role.USER, Message.Role.ASSISTANT],
                compacted_at__isnull=True,
            ).order_by("created_at")
        )

        if len(messages) < MIN_MESSAGES_TO_COMPACT:
            logger.debug(
                "Session %s: only %d messages, skipping compaction",
                session.pk,
                len(messages),
            )
            self._emit(emitter, "compact", "done", "Not enough messages to compact")
            return 0

        # Split: messages to compact vs. active window to keep
        to_compact = messages[:-KEEP_RECENT]
        if not to_compact:
            self._emit(emitter, "compact", "done", "Nothing to compact")
            return 0

        # Format the conversation block for the LLM
        conversation_lines = []
        for msg in to_compact:
            role_label = "USER" if msg.role == Message.Role.USER else "CASTOR"
            conversation_lines.append(f"{role_label}: {msg.content}")
        conversation_text = "\n".join(conversation_lines)

        # Include previous compaction summary as preamble if one exists
        previous_summary = (
            session.messages.filter(
                role=Message.Role.SYSTEM,
                is_compaction_summary=True,
            )
            .order_by("-created_at")
            .first()
        )

        previous_summary_text = ""
        if previous_summary:
            previous_summary_text = f"=== PREVIOUS SUMMARY ===\n{previous_summary.content}\n\n"

        # Generate the summary
        chain = _COMPACTION_PROMPT | self.llm | StrOutputParser()
        summary_content = chain.invoke(
            {
                "previous_summary": previous_summary_text,
                "conversation": conversation_text,
            }
        )

        # Create the new compaction summary message
        summary_msg = Message.objects.create(
            session=session,
            role=Message.Role.SYSTEM,
            content=summary_content,
            is_compaction_summary=True,
        )

        # Mark compacted messages
        now = timezone.now()
        msg_ids = [m.pk for m in to_compact]
        Message.objects.filter(pk__in=msg_ids).update(compacted_at=now)

        # Delete previous compaction summary (only one active at a time)
        if previous_summary:
            previous_summary.delete()

        compacted_count = len(to_compact)
        logger.info(
            "Compacted %d messages in session %s → summary %s",
            compacted_count,
            session.pk,
            summary_msg.pk,
        )
        self._emit(
            emitter,
            "compact",
            "done",
            f"Compacted {compacted_count} messages",
        )
        return compacted_count
