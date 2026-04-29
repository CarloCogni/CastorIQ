# facilities/tests/test_portal_consumer.py
"""WebSocket tests for the OccupantPortalConsumer + AR broadcasts (M4.C)."""

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
from facilities.consumers import OccupantPortalConsumer
from facilities.services.action_request_service import ActionRequestService
from facilities.services.workorder_service import WorkOrderService
from facilities.tests.factories import ActionRequestFactory


def _make_communicator(project_id, user) -> WebsocketCommunicator:
    """Build a WebsocketCommunicator pre-loaded with scope (user + url_route)."""
    communicator = WebsocketCommunicator(
        OccupantPortalConsumer.as_asgi(),
        f"/ws/projects/{project_id}/fm/portal/",
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

    async def test_outsider_rejected_4003(self):
        outsider = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        communicator = _make_communicator(project.pk, outsider)
        connected, code = await communicator.connect()
        assert not connected
        assert code == 4003

    async def test_tenant_can_connect(self):
        user = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=user, project=project, permission="viewer"
        )
        await sync_to_async(ProjectRoleFactory)(user=user, project=project, role="tenant")
        communicator = _make_communicator(project.pk, user)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()


# ── Push-side broadcasts ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestBroadcast:
    async def _seat(self, role="tenant"):
        user = await sync_to_async(UserFactory)()
        project = await sync_to_async(ProjectFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=user, project=project, permission="viewer"
        )
        await sync_to_async(ProjectRoleFactory)(user=user, project=project, role=role)
        return user, project

    async def test_triage_pushes_status_card_to_submitter(self):
        """Triaging an AR fans out the rendered card to the submitter's portal channel."""
        user, project = await self._seat()
        ar = await sync_to_async(ActionRequestFactory)(
            project=project, submitted_by=user, title="Cold room"
        )
        communicator = _make_communicator(project.pk, user)
        connected, _ = await communicator.connect()
        assert connected

        # Triage the AR (FM-side actor — for the test we just call the service)
        fm_user = await sync_to_async(UserFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=fm_user, project=project, permission="editor"
        )
        ar = await sync_to_async(ActionRequestService(project, fm_user).triage)(
            ar, note="On its way"
        )

        message = await communicator.receive_json_from(timeout=2.0)
        assert message["type"] == "ar.updated"
        assert message["ar_id"] == str(ar.pk)
        assert message["event"] == "triaged"
        assert "Cold room" in message["html"]
        await communicator.disconnect()

    async def test_other_occupant_does_not_receive_push(self):
        """Group isolation — another tenant's portal channel must not receive the push."""
        user_a, project = await self._seat()
        user_b = await sync_to_async(UserFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=user_b, project=project, permission="viewer"
        )
        await sync_to_async(ProjectRoleFactory)(user=user_b, project=project, role="tenant")
        ar = await sync_to_async(ActionRequestFactory)(
            project=project, submitted_by=user_a, title="A's request"
        )

        comm_a = _make_communicator(project.pk, user_a)
        comm_b = _make_communicator(project.pk, user_b)
        await comm_a.connect()
        await comm_b.connect()

        fm_user = await sync_to_async(UserFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=fm_user, project=project, permission="editor"
        )
        await sync_to_async(ActionRequestService(project, fm_user).triage)(ar)

        # User A receives the push
        msg_a = await comm_a.receive_json_from(timeout=2.0)
        assert msg_a["type"] == "ar.updated"
        # User B's group must NOT receive anything — channels has a built-in
        # helper for the negative assertion that doesn't poison the
        # communicator's pending-receive coroutine.
        assert await comm_b.receive_nothing(timeout=0.5)

        await comm_a.disconnect()
        await comm_b.disconnect()

    async def test_escalation_pushes_to_submitter(self):
        """Escalating an AR to a WO also pushes the AR's new status."""
        user, project = await self._seat()
        ar = await sync_to_async(ActionRequestFactory)(
            project=project, submitted_by=user, title="Leak"
        )
        communicator = _make_communicator(project.pk, user)
        connected, _ = await communicator.connect()
        assert connected

        fm_user = await sync_to_async(UserFactory)()
        await sync_to_async(ProjectMembershipFactory)(
            user=fm_user, project=project, permission="editor"
        )
        await sync_to_async(ProjectRoleFactory)(
            user=fm_user, project=project, role="facilitiesmanager"
        )
        await sync_to_async(WorkOrderService(project, fm_user).escalate_action_request)(ar)

        message = await communicator.receive_json_from(timeout=2.0)
        assert message["type"] == "ar.updated"
        assert message["event"] == "escalated"
        assert message["status"] == "escalated"
        await communicator.disconnect()
