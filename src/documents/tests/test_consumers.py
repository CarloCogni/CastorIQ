# documents/tests/test_consumers.py
"""Tests for OCRConsumer WebSocket consumer."""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from documents.consumers import OCRConsumer
from documents.tests.factories import DocumentFactory

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_communicator(project_id: str, user):
    """Build a WebsocketCommunicator with scope pre-configured for OCRConsumer."""
    communicator = WebsocketCommunicator(
        OCRConsumer.as_asgi(),
        f"/ws/projects/{project_id}/documents/test-doc-id/ocr/",
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
class TestOCRConsumerConnect:
    """Connection behaviour for OCRConsumer."""

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
class TestOCRConsumerMessages:
    """Message handling for OCRConsumer."""

    async def test_unknown_action_returns_ocr_failed(self, project, user):
        """Unknown action returns an ocr_failed JSON message."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "bogus"})
        response = await communicator.receive_json_from()

        assert response["type"] == "ocr_failed"
        assert "Unknown action" in response["error"]
        await communicator.disconnect()

    @override_settings(GLM_OCR_ENABLED=False)
    async def test_ocr_disabled_returns_failed(self, project, user):
        """OCR disabled setting returns ocr_failed + done."""
        document = await sync_to_async(DocumentFactory)(project=project)
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "run_ocr", "document_id": str(document.pk)})
        messages = await _drain_until(communicator, {"done"})

        types = [m["type"] for m in messages]
        assert "ocr_failed" in types
        assert "done" in types
        failed_msg = next(m for m in messages if m["type"] == "ocr_failed")
        assert "disabled" in failed_msg["error"].lower()
        await communicator.disconnect()

    @override_settings(GLM_OCR_ENABLED=True)
    async def test_missing_document_id_returns_failed(self, project, user):
        """Missing document_id returns ocr_failed + done."""
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "run_ocr"})
        messages = await _drain_until(communicator, {"done"})

        types = [m["type"] for m in messages]
        assert "ocr_failed" in types
        assert "done" in types
        failed_msg = next(m for m in messages if m["type"] == "ocr_failed")
        assert "document_id" in failed_msg["error"].lower()
        await communicator.disconnect()

    @override_settings(GLM_OCR_ENABLED=True)
    async def test_happy_path_returns_ocr_complete(self, project, user):
        """Successful OCR returns ocr_complete with counts, then done."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        document = await sync_to_async(DocumentFactory)(project=project)
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        mock_result = SimpleNamespace(chunks_created=5, pages_processed=3)

        with patch.object(OCRConsumer, "_run_ocr", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result

            await communicator.send_json_to({"action": "run_ocr", "document_id": str(document.pk)})
            messages = await _drain_until(communicator, {"done"})

        types = [m["type"] for m in messages]
        assert "ocr_complete" in types
        assert "done" in types
        complete_msg = next(m for m in messages if m["type"] == "ocr_complete")
        assert complete_msg["chunks_created"] == 5
        assert complete_msg["pages_ocrd"] == 3
        await communicator.disconnect()

    @override_settings(GLM_OCR_ENABLED=True)
    async def test_service_error_returns_ocr_failed(self, project, user):
        """OCR service exception is surfaced as ocr_failed + done."""
        from unittest.mock import AsyncMock, patch

        document = await sync_to_async(DocumentFactory)(project=project)
        communicator = _make_communicator(project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        with patch.object(OCRConsumer, "_run_ocr", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("GLM-OCR model not available")

            await communicator.send_json_to({"action": "run_ocr", "document_id": str(document.pk)})
            messages = await _drain_until(communicator, {"done"})

        types = [m["type"] for m in messages]
        assert "ocr_failed" in types
        assert "done" in types
        await communicator.disconnect()
