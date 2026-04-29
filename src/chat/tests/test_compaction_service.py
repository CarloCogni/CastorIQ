# chat/tests/test_compaction_service.py
"""
Integration tests for CompactionService and the Ask message-list view.

Existing consumer tests mock `_run_compaction` and never exercise the real
service or the view layer, which is why the "summary hidden, compacted
originals still visible" bug shipped silently. These tests run the real
service (with the LLM stubbed) and assert end-to-end visibility:

  - service marks originals + creates a summary
  - view returns the summary card and hides compacted originals from the
    main message stream while keeping them in the folded-history block
  - the compact button reflects can_compact correctly via the OOB swap
"""

from unittest.mock import patch

import pytest
from django.urls import reverse
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from chat.models import Message
from chat.services.compaction_service import (
    KEEP_RECENT,
    MIN_MESSAGES_TO_COMPACT,
    CompactionService,
)
from chat.tests.factories import MessageFactory

FAKE_SUMMARY = "SUMMARY: The user asked questions 0-3 and Castor answered."


def _seed_exchanges(session, n_pairs: int) -> list[Message]:
    """Create n_pairs of (user, assistant) messages on the session, oldest first."""
    created: list[Message] = []
    for i in range(n_pairs):
        created.append(
            MessageFactory(
                session=session,
                role=Message.Role.USER,
                content=f"User question {i}",
            )
        )
        created.append(
            MessageFactory(
                session=session,
                role=Message.Role.ASSISTANT,
                content=f"Assistant answer {i}",
            )
        )
    return created


@pytest.fixture
def stub_llm():
    """LLM stand-in for the `prompt | llm | StrOutputParser()` chain.

    A bare ``MagicMock`` is not a Runnable, so LangChain's pipe operator
    falls through to calling it directly and the parser sees a Mock instead
    of a string. ``RunnableLambda`` returning an ``AIMessage`` plugs in as
    a real Runnable; ``StrOutputParser`` then unwraps ``.content`` cleanly.
    """
    return RunnableLambda(lambda _prompt_value: AIMessage(content=FAKE_SUMMARY))


@pytest.mark.django_db
class TestCompactionServiceReturnsCount:
    """compact_session returns the number of messages folded into the summary."""

    def test_below_threshold_returns_zero(self, chat_session, stub_llm):
        """Sessions with fewer than MIN_MESSAGES_TO_COMPACT live messages no-op."""
        _seed_exchanges(chat_session, n_pairs=1)  # 2 messages, below 3

        with patch("chat.services.compaction_service.get_llm", return_value=stub_llm):
            count = CompactionService(user=None).compact_session(chat_session)

        assert count == 0
        assert not chat_session.messages.filter(is_compaction_summary=True).exists()
        assert not chat_session.messages.filter(compacted_at__isnull=False).exists()

    def test_above_threshold_returns_count_and_creates_summary(self, chat_session, stub_llm):
        """8 messages → 2 kept recent, 6 compacted, 1 summary created."""
        _seed_exchanges(chat_session, n_pairs=4)  # 8 messages

        with patch("chat.services.compaction_service.get_llm", return_value=stub_llm):
            count = CompactionService(user=None).compact_session(chat_session)

        assert count == 8 - KEEP_RECENT
        summaries = chat_session.messages.filter(is_compaction_summary=True)
        assert summaries.count() == 1
        assert summaries.first().role == Message.Role.SYSTEM

        compacted = chat_session.messages.filter(compacted_at__isnull=False)
        assert compacted.count() == 8 - KEEP_RECENT
        # Live messages remaining (not compacted, not the summary) = KEEP_RECENT
        live = chat_session.messages.filter(
            role__in=[Message.Role.USER, Message.Role.ASSISTANT],
            compacted_at__isnull=True,
        )
        assert live.count() == KEEP_RECENT


@pytest.mark.django_db
class TestAskMessagesViewAfterCompaction:
    """The message-list partial must reveal the summary and hide compacted originals."""

    def test_summary_shown_originals_hidden_from_main_stream(
        self, client, project, user, chat_session, stub_llm
    ):
        """After compaction, the visible stream contains the summary card and the
        kept-recent messages — but the compacted originals only appear inside the
        d-none "Show full history" block, not the main flow."""
        _seed_exchanges(chat_session, n_pairs=4)  # 8 messages

        with patch("chat.services.compaction_service.get_llm", return_value=stub_llm):
            CompactionService(user=None).compact_session(chat_session)

        client.force_login(user)
        url = reverse(
            "projects:ask_session_messages",
            kwargs={"pk": project.pk, "session_id": chat_session.pk},
        )
        response = client.get(url)

        assert response.status_code == 200
        body = response.content.decode("utf-8")

        # Summary card present
        assert "compaction-summary-card" in body
        assert "Conversation summary" in body
        assert FAKE_SUMMARY in body

        # Folded history block exists, marked d-none
        assert 'class="compacted-history d-none' in body

        # Kept-recent messages (last 2 = pair 3) appear in the live stream
        assert "User question 3" in body
        assert "Assistant answer 3" in body

        # Compacted originals (pairs 0 and 1) are still rendered, but only
        # inside the folded-history block — verify by locating the block and
        # confirming the early questions appear AFTER the d-none marker.
        folded_idx = body.index('class="compacted-history d-none')
        early_idx = body.index("User question 0")
        assert early_idx > folded_idx, "compacted originals must live inside the folded block"

    def test_compact_button_disabled_when_below_threshold(
        self, client, project, user, chat_session, stub_llm
    ):
        """After compaction reduces live messages to KEEP_RECENT (< MIN_MESSAGES_TO_COMPACT),
        the compact button on the Ask tab must be rendered disabled."""
        _seed_exchanges(chat_session, n_pairs=4)  # 8 messages

        with patch("chat.services.compaction_service.get_llm", return_value=stub_llm):
            CompactionService(user=None).compact_session(chat_session)

        client.force_login(user)
        url = reverse(
            "projects:ask_session", kwargs={"pk": project.pk, "session_id": chat_session.pk}
        )
        body = client.get(url).content.decode("utf-8")

        assert KEEP_RECENT < MIN_MESSAGES_TO_COMPACT  # sanity guard
        btn_idx = body.index('id="compact-btn"')
        btn_block = body[btn_idx : btn_idx + 600]
        assert "disabled" in btn_block

    def test_compact_button_enabled_when_above_threshold(self, client, project, user, chat_session):
        """With 3+ live messages and no prior compaction, the button is enabled."""
        _seed_exchanges(chat_session, n_pairs=2)  # 4 messages

        client.force_login(user)
        url = reverse(
            "projects:ask_session", kwargs={"pk": project.pk, "session_id": chat_session.pk}
        )
        body = client.get(url).content.decode("utf-8")

        btn_idx = body.index('id="compact-btn"')
        btn_block = body[btn_idx : btn_idx + 600]
        assert "disabled" not in btn_block
