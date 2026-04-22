# writeback/tests/test_consumers.py
"""Tests for ProposalConsumer and ScanConsumer WebSocket consumers."""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from writeback.consumers import ProposalConsumer, ScanConsumer

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_communicator(consumer_class, project_id: str, user):
    """Build a WebsocketCommunicator with scope pre-configured."""
    communicator = WebsocketCommunicator(
        consumer_class.as_asgi(),
        f"/ws/projects/{project_id}/modify/",
    )
    communicator.scope["user"] = user
    communicator.scope["url_route"] = {"kwargs": {"project_id": str(project_id)}}
    return communicator


# ── ProposalConsumer ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestProposalConsumerConnect:
    """Connection behaviour for ProposalConsumer."""

    async def test_authenticated_user_with_access_can_connect(self, project, user):
        """Authenticated project member can connect and is accepted."""
        communicator = _make_communicator(ProposalConsumer, project.id, user)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()

    async def test_anonymous_user_is_rejected_with_4001(self, project):
        """Anonymous user is rejected with close code 4001."""
        from django.contrib.auth.models import AnonymousUser

        communicator = _make_communicator(ProposalConsumer, project.id, AnonymousUser())
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4001

    async def test_user_without_project_access_rejected_4003(self, project):
        """User not in the project is rejected with close code 4003."""
        from environments.tests.factories import UserFactory

        other_user = await sync_to_async(UserFactory)()
        communicator = _make_communicator(ProposalConsumer, project.id, other_user)
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4003


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestProposalConsumerMessages:
    """Message handling for ProposalConsumer."""

    async def test_unknown_action_returns_error_message(self, project, user):
        """Sending an unknown action returns a JSON error response."""
        communicator = _make_communicator(ProposalConsumer, project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "invalid_action"})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        assert "Unknown action" in response["message"]

        await communicator.disconnect()

    async def test_propose_action_with_empty_message_returns_error(self, project, user):
        """Proposing with an empty message text returns an error."""
        communicator = _make_communicator(ProposalConsumer, project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "propose", "message": ""})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        assert "required" in response["message"].lower()

        await communicator.disconnect()

    async def test_propose_action_with_service_error_returns_error(self, project, user):
        """ModificationService.propose() exception is surfaced as error message."""
        from unittest.mock import patch

        communicator = _make_communicator(ProposalConsumer, project.id, user)
        connected, _ = await communicator.connect()
        assert connected

        from writeback.services.modification_service import ModificationError

        with patch(
            "writeback.services.modification_service.ModificationService",
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.propose.side_effect = ModificationError("No IFC entities found.")

            await communicator.send_json_to(
                {"action": "propose", "message": "Set fire rating to EI120"}
            )
            response = await communicator.receive_json_from()

        assert response["type"] == "error"

        await communicator.disconnect()


# ── ScanConsumer ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestScanConsumerConnect:
    """Connection behaviour for ScanConsumer."""

    async def test_authenticated_user_with_access_can_connect(self, project, user):
        """Authenticated project member can connect to scan consumer."""
        communicator = WebsocketCommunicator(
            ScanConsumer.as_asgi(),
            f"/ws/projects/{project.id}/scan/",
        )
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"project_id": str(project.id)}}

        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()

    async def test_anonymous_user_is_rejected(self, project):
        """Anonymous user is rejected by ScanConsumer."""
        from django.contrib.auth.models import AnonymousUser

        communicator = WebsocketCommunicator(
            ScanConsumer.as_asgi(),
            f"/ws/projects/{project.id}/scan/",
        )
        communicator.scope["user"] = AnonymousUser()
        communicator.scope["url_route"] = {"kwargs": {"project_id": str(project.id)}}

        connected, code = await communicator.connect()
        assert not connected
        assert code == 4001


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestScanConsumerMessages:
    """Message handling for ScanConsumer."""

    async def test_unknown_action_returns_error(self, project, user):
        """Unknown action returns an error JSON message."""
        communicator = WebsocketCommunicator(
            ScanConsumer.as_asgi(),
            f"/ws/projects/{project.id}/scan/",
        )
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"project_id": str(project.id)}}

        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"action": "unknown"})
        response = await communicator.receive_json_from()

        assert response["type"] == "error"
        await communicator.disconnect()

    async def test_start_scan_action_completes_with_scan_complete_message(self, project, user):
        """start_scan action returns scan_complete and done messages."""
        from unittest.mock import patch

        communicator = WebsocketCommunicator(
            ScanConsumer.as_asgi(),
            f"/ws/projects/{project.id}/scan/",
        )
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"project_id": str(project.id)}}

        connected, _ = await communicator.connect()
        assert connected

        with patch(
            "writeback.services.conflict_scan_service.ConflictScanService",
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.full_scan.return_value = {
                "entities_scanned": 0,
                "conflicts_found": 0,
                "conflicts_updated": 0,
            }

            await communicator.send_json_to({"action": "start_scan", "skip_low_value": True})

            # Drain messages until "done"
            messages = []
            for _ in range(10):
                try:
                    msg = await communicator.receive_json_from(timeout=2)
                    messages.append(msg)
                    if msg.get("type") == "done":
                        break
                except Exception:
                    break

        types = [m["type"] for m in messages]
        assert "scan_complete" in types
        assert "done" in types

        await communicator.disconnect()

    async def test_cancellation_error_from_scan_returns_cancelled_message(self, project, user):
        """CancellationError raised by full_scan surfaces as {type: cancelled}."""
        from unittest.mock import patch

        from writeback.services.emitters import CancellationError

        communicator = WebsocketCommunicator(
            ScanConsumer.as_asgi(),
            f"/ws/projects/{project.id}/scan/",
        )
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"project_id": str(project.id)}}

        connected, _ = await communicator.connect()
        assert connected

        with patch(
            "writeback.services.conflict_scan_service.ConflictScanService",
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.full_scan.side_effect = CancellationError("Pipeline cancelled by user.")

            await communicator.send_json_to({"action": "start_scan", "skip_low_value": True})

            messages = []
            for _ in range(5):
                try:
                    msg = await communicator.receive_json_from(timeout=2)
                    messages.append(msg)
                    if msg.get("type") in ("cancelled", "error", "done"):
                        break
                except Exception:
                    break

        types = [m["type"] for m in messages]
        assert "cancelled" in types
        assert "error" not in types

        await communicator.disconnect()

    async def test_disconnect_sets_cancel_event(self, project, user):
        """disconnect() flips the cancel event so any running scan aborts.

        Exercises the consumer directly — WebsocketCommunicator doesn't expose
        the live instance, and we only need to verify the method contract.
        """
        import threading

        consumer = ScanConsumer()
        consumer.user = user
        consumer.scope = {"url_route": {"kwargs": {"project_id": str(project.id)}}}
        consumer._cancel_event = threading.Event()

        assert not consumer._cancel_event.is_set()

        await consumer.disconnect(close_code=1000)

        assert consumer._cancel_event.is_set()


# ── _serialize_result ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestSerializeResult:
    """Tests for ProposalConsumer._serialize_result() helper."""

    async def test_serialize_single_proposal_returns_proposal_key(self, project, user, ifc_file):
        """Single proposal result has 'proposal' key (not 'proposals')."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = await sync_to_async(ModificationProposalFactory)(
            ifc_file=ifc_file,
            created_by=user,
            diff_preview='[{"field": "FireRating"}]',
        )

        consumer = ProposalConsumer()
        result = await consumer._serialize_result(proposal, [proposal])

        assert "proposal" in result
        assert result["status"] == "proposed"

    async def test_serialize_chain_proposals_returns_chain_key(self, project, user, ifc_file):
        """Chain result (list of proposals) has 'chain': True and 'proposals' list."""
        from writeback.tests.factories import ModificationProposalFactory

        p1 = await sync_to_async(ModificationProposalFactory)(
            ifc_file=ifc_file, created_by=user, diff_preview="[]"
        )
        p2 = await sync_to_async(ModificationProposalFactory)(
            ifc_file=ifc_file, created_by=user, diff_preview="[]"
        )

        consumer = ProposalConsumer()
        result = await consumer._serialize_result([p1, p2], [p1, p2])

        assert result["chain"] is True
        assert "proposals" in result
        assert len(result["proposals"]) == 2
