# facilities/tests/test_workorder_consumer.py
"""WebSocket tests for ``facilities.consumers.WorkOrderConsumer`` (M3.D)."""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.consumers import WorkOrderConsumer
from facilities.models import WorkOrderStatus
from facilities.services.workorder_service import WorkOrderService
from facilities.tests.factories import WorkOrderFactory


def _make_communicator(project_id, user) -> WebsocketCommunicator:
    """Build a WebsocketCommunicator pre-loaded with scope (user + url_route)."""
    communicator = WebsocketCommunicator(
        WorkOrderConsumer.as_asgi(),
        f"/ws/projects/{project_id}/fm/work/",
    )
    communicator.scope["user"] = user
    communicator.scope["url_route"] = {"kwargs": {"project_id": str(project_id)}}
    return communicator


# ── Connect / auth ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestConnect:
    async def test_anonymous_rejected_4001(self):
        project = await sync_to_async(ProjectFactory)()
        communicator = _make_communicator(project.pk, AnonymousUser())
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4001

    async def test_no_project_access_rejected_4003(self):
        outsider = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        communicator = _make_communicator(project.pk, outsider)
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4003

    async def test_member_can_connect(self):
        user = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=user, project=project, permission="viewer"
        )
        communicator = _make_communicator(project.pk, user)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()


# ── receive_json (push-only) ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestReceive:
    async def _connected(self):
        user = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=user, project=project, permission="viewer"
        )
        communicator = _make_communicator(project.pk, user)
        connected, _ = await communicator.connect()
        assert connected
        return communicator, user, project

    async def test_ping_returns_pong(self):
        communicator, _, _ = await self._connected()
        await communicator.send_json_to({"action": "ping"})
        response = await communicator.receive_json_from()
        assert response == {"type": "pong"}
        await communicator.disconnect()

    async def test_subscribe_returns_ack(self):
        communicator, _, project = await self._connected()
        await communicator.send_json_to({"action": "subscribe"})
        response = await communicator.receive_json_from()
        assert response["type"] == "subscribed"
        assert response["project_id"] == str(project.pk)
        await communicator.disconnect()

    async def test_unknown_action_returns_error(self):
        communicator, _, _ = await self._connected()
        await communicator.send_json_to({"action": "fly"})
        response = await communicator.receive_json_from()
        assert response["type"] == "error"
        await communicator.disconnect()


# ── Per-project group fan-out ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestGroupFanout:
    """A transition triggered by tab A reaches tab B over the group."""

    async def test_two_clients_same_project_both_receive(self):
        # Tab A: an FM who will perform the transition
        # Tab B: a viewer subscribed to the same project
        fm_user = await sync_to_async(UserFactory)()
        viewer = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=fm_user, project=project, permission="editor"
        )
        await sync_to_async(ProjectMembershipFactory)(
            user=viewer, project=project, permission="viewer"
        )
        await sync_to_async(ProjectRoleFactory)(
            user=fm_user, project=project, role="facilitiesmanager"
        )

        wo = await sync_to_async(WorkOrderFactory)(
            project=project, status=WorkOrderStatus.SUBMITTED
        )

        comm_a = _make_communicator(project.pk, fm_user)
        comm_b = _make_communicator(project.pk, viewer)
        a_connected, _ = await comm_a.connect()
        b_connected, _ = await comm_b.connect()
        assert a_connected and b_connected

        # FM triggers the transition synchronously through the service.
        @sync_to_async
        def _trigger_triage():
            WorkOrderService(project, fm_user).triage(wo)

        await _trigger_triage()

        # Both clients should receive the wo.updated payload via group_send.
        msg_a = await comm_a.receive_json_from(timeout=2)
        msg_b = await comm_b.receive_json_from(timeout=2)
        for msg in (msg_a, msg_b):
            assert msg["type"] == "wo.updated"
            assert msg["wo_id"] == str(wo.pk)
            assert msg["to_status"] == int(WorkOrderStatus.TRIAGED)
            assert "html" in msg

        await comm_a.disconnect()
        await comm_b.disconnect()

    async def test_other_project_is_isolated(self):
        """A transition on project X should not reach a subscriber on project Y."""
        fm_user = await sync_to_async(UserFactory)()
        viewer = await sync_to_async(UserFactory)()
        project_x = await sync_to_async(ProjectFactory)()
        project_y = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=fm_user, project=project_x, permission="editor"
        )
        await sync_to_async(ProjectMembershipFactory)(
            user=viewer, project=project_y, permission="viewer"
        )
        await sync_to_async(ProjectRoleFactory)(
            user=fm_user, project=project_x, role="facilitiesmanager"
        )

        wo_x = await sync_to_async(WorkOrderFactory)(
            project=project_x, status=WorkOrderStatus.SUBMITTED
        )

        comm_y = _make_communicator(project_y.pk, viewer)
        connected, _ = await comm_y.connect()
        assert connected

        @sync_to_async
        def _trigger():
            WorkOrderService(project_x, fm_user).triage(wo_x)

        await _trigger()

        # Y should NOT receive anything in a short window.
        assert await comm_y.receive_nothing(timeout=0.5)
        await comm_y.disconnect()
