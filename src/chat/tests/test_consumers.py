# chat/tests/test_consumers.py
"""Tests for AskConsumer WebSocket consumer."""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from chat.consumers import AskConsumer

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_communicator(project_id: str, user):
    """Build a WebsocketCommunicator with scope pre-configured."""
    communicator = WebsocketCommunicator(
        AskConsumer.as_asgi(),
        f"/ws/projects/{project_id}/ask/",
    )
    communicator.scope["user"] = user
    communicator.scope["url_route"] = {"kwargs": {"project_id": str(project_id)}}
    return communicator


async def _drain_until(communicator, terminal_types, max_msgs=10, timeout=2):
    """Collect messages from the communicator until a terminal type is received."""
    messages = []
    for _ in range(max_msgs):
        try:
            msg = await communicator.receive_json_from(timeout=timeout)
            messages.append(msg)
            if msg.get("type") in terminal_types:
                break
        except Exception:
            break
    return messages


# ── Connection ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestAskConsumerConnect:
    """Connection behaviour for AskConsumer."""

    async def test_authenticated_member_connects(self, project, user):
        """Authenticated project member can connect and is accepted."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()

    async def test_anonymous_user_rejected_4001(self, project):
        """Anonymous user is rejected with close code 4001."""
        from django.contrib.auth.models import AnonymousUser

        communicator = _make_communicator(project.id, AnonymousUser())
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4001

    async def test_non_member_rejected_4003(self, project):
        """User without project access is rejected with close code 4003."""
        from environments.tests.factories import UserFactory

        other_user = await sync_to_async(UserFactory)()
        communicator = _make_communicator(project.id, other_user)
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4003


# ── Messages ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestAskConsumerMessages:
    """Message handling for AskConsumer."""

    async def test_unknown_action_returns_error(self, project, user):
        """Sending an unknown action returns a JSON error response."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "bogus"})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        assert "Unknown action" in response["message"]
        await communicator.disconnect()

    async def test_ask_empty_message_returns_error(self, project, user):
        """Ask action with empty message returns an error."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "ask", "message": ""})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        assert "required" in response["message"].lower()
        await communicator.disconnect()

    async def test_ask_happy_path_streams_done(self, project, user):
        """Successful ask action ends with a 'done' message."""
        from unittest.mock import AsyncMock, patch

        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(AskConsumer, "_run_pipeline", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None

            await communicator.send_json_to(
                {"action": "ask", "message": "What is the fire rating?"}
            )
            messages = await _drain_until(communicator, {"done", "error"})

        types = [m["type"] for m in messages]
        assert "done" in types
        await communicator.disconnect()

    async def test_ask_service_error_streams_error(self, project, user):
        """Pipeline exception is surfaced as an error message."""
        from unittest.mock import AsyncMock, patch

        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(AskConsumer, "_run_pipeline", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("Ollama unreachable")

            await communicator.send_json_to({"action": "ask", "message": "hello"})
            messages = await _drain_until(communicator, {"done", "error"})

        types = [m["type"] for m in messages]
        assert "error" in types
        error_msg = next(m for m in messages if m["type"] == "error")
        assert "Ollama unreachable" in error_msg["message"]
        await communicator.disconnect()

    async def test_ask_cancellation_streams_cancelled(self, project, user):
        """CancellationError results in a 'cancelled' message."""
        from unittest.mock import AsyncMock, patch

        from writeback.services.emitters import CancellationError

        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(AskConsumer, "_run_pipeline", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = CancellationError()

            await communicator.send_json_to({"action": "ask", "message": "hello"})
            messages = await _drain_until(communicator, {"cancelled", "error"})

        types = [m["type"] for m in messages]
        assert "cancelled" in types
        await communicator.disconnect()

    async def test_compact_missing_session_id_returns_error(self, project, user):
        """Compact action without session_id returns an error."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "compact"})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        assert "session_id" in response["message"].lower()
        await communicator.disconnect()

    async def test_compact_happy_path_streams_compacted(self, project, user, chat_session):
        """Successful compact action ends with a 'compacted' message carrying a count."""
        from unittest.mock import AsyncMock, patch

        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(AskConsumer, "_run_compaction", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = 5

            await communicator.send_json_to(
                {"action": "compact", "session_id": str(chat_session.pk)}
            )
            messages = await _drain_until(communicator, {"compacted", "error"})

        compacted = next((m for m in messages if m["type"] == "compacted"), None)
        assert compacted is not None
        assert compacted["count"] == 5
        await communicator.disconnect()

    async def test_compact_service_error_streams_error(self, project, user, chat_session):
        """Compaction service exception is surfaced as an error message."""
        from unittest.mock import AsyncMock, patch

        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(AskConsumer, "_run_compaction", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = ValueError("Session not found.")

            await communicator.send_json_to(
                {"action": "compact", "session_id": str(chat_session.pk)}
            )
            messages = await _drain_until(communicator, {"compacted", "error"})

        types = [m["type"] for m in messages]
        assert "error" in types
        await communicator.disconnect()
