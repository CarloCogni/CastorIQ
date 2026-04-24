# facilities/tests/test_workorder_views.py
"""HTTP-level tests for the M3 work-order, permit, and action-request views."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.models import (
    ActionRequest,
    Permit,
    WorkOrder,
    WorkOrderStatus,
)
from facilities.tests.factories import (
    ActionRequestFactory,
    PermitFactory,
    WorkOrderFactory,
)


def _setup_project(client, role: str = "facilitiesmanager", permission: str = "editor"):
    """Create a (user, project) with a role + membership and log in. Returns the pair."""
    user = UserFactory()
    project = ProjectFactory()
    ProjectMembershipFactory(user=user, project=project, permission=permission)
    if role:
        ProjectRoleFactory(user=user, project=project, role=role)
    client.force_login(user)
    return user, project


# ── Sub-nav unlock ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSubnavUnlock:
    """Confirm the subnav now renders Work / Permits / Requests as live links."""

    def test_subnav_includes_work_link(self, client):
        user, project = _setup_project(client)
        response = client.get(reverse("facilities:tab", args=[project.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert reverse("facilities:work_list", args=[project.pk]) in body
        assert reverse("facilities:permit_list", args=[project.pk]) in body
        assert reverse("facilities:ar_list", args=[project.pk]) in body


# ── Work-order views ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestWorkOrderListView:
    def test_list_renders_for_editor(self, client):
        user, project = _setup_project(client)
        WorkOrderFactory(project=project, title="Test WO")
        response = client.get(reverse("facilities:work_list", args=[project.pk]))
        assert response.status_code == 200
        assert b"Test WO" in response.content

    def test_list_htmx_returns_grid_fragment_only(self, client):
        user, project = _setup_project(client)
        WorkOrderFactory(project=project, title="HTMX WO")
        response = client.get(
            reverse("facilities:work_list", args=[project.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "HTMX WO" in body
        # Sub-nav header should not be in fragment response
        assert "Work Orders</h5>" not in body

    def test_list_filter_by_status(self, client):
        user, project = _setup_project(client)
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT, title="Draft One")
        WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED, title="Submitted One")
        response = client.get(
            reverse("facilities:work_list", args=[project.pk])
            + f"?status={WorkOrderStatus.SUBMITTED}",
        )
        assert b"Submitted One" in response.content
        assert b"Draft One" not in response.content

    def test_list_anonymous_redirects_to_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("facilities:work_list", args=[project.pk]))
        assert response.status_code in (302, 403)


@pytest.mark.django_db
class TestWorkOrderKanbanView:
    def test_kanban_renders_columns(self, client):
        user, project = _setup_project(client)
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT, title="Draft K")
        WorkOrderFactory(project=project, status=WorkOrderStatus.IN_PROGRESS, title="WIP K")
        response = client.get(reverse("facilities:work_kanban", args=[project.pk]))
        assert response.status_code == 200
        assert b"Draft K" in response.content
        assert b"WIP K" in response.content
        # Closed column doesn't appear (KANBAN_STATUSES excludes terminals)
        assert b'data-status="80"' not in response.content


@pytest.mark.django_db
class TestWorkOrderCalendarView:
    def test_calendar_renders_month(self, client):
        user, project = _setup_project(client)
        response = client.get(reverse("facilities:work_calendar", args=[project.pk]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestWorkOrderDetailView:
    def test_detail_renders(self, client):
        user, project = _setup_project(client)
        wo = WorkOrderFactory(project=project, title="Detail WO")
        response = client.get(reverse("facilities:work_detail", args=[project.pk, wo.pk]))
        assert response.status_code == 200
        assert b"Detail WO" in response.content
        assert wo.wo_number.encode() in response.content

    def test_detail_404_when_wo_in_other_project(self, client):
        user, project = _setup_project(client)
        wo = WorkOrderFactory()
        response = client.get(reverse("facilities:work_detail", args=[project.pk, wo.pk]))
        assert response.status_code == 404


@pytest.mark.django_db
class TestWorkOrderCreateView:
    def test_get_returns_drawer(self, client):
        user, project = _setup_project(client)
        response = client.get(reverse("facilities:work_create", args=[project.pk]))
        assert response.status_code == 200
        assert b"workCreateDrawer" in response.content

    def test_post_creates_wo_and_redirects(self, client):
        user, project = _setup_project(client)
        response = client.post(
            reverse("facilities:work_create", args=[project.pk]),
            data={"title": "Created via view", "category": "corrective", "priority": "3"},
        )
        assert response.status_code == 302
        wo = WorkOrder.objects.get(project=project, title="Created via view")
        assert wo.status == WorkOrderStatus.DRAFT
        # status_event was written
        assert wo.status_events.count() == 1

    def test_post_with_blank_title_returns_400(self, client):
        user, project = _setup_project(client)
        response = client.post(
            reverse("facilities:work_create", args=[project.pk]),
            data={"title": "  "},
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestWorkOrderTransitionView:
    def test_transition_with_valid_role(self, client):
        user, project = _setup_project(client)  # FM
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        response = client.post(
            reverse("facilities:work_transition", args=[project.pk, wo.pk, "triage"]),
        )
        assert response.status_code in (200, 302)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.TRIAGED

    def test_transition_unknown_name_400(self, client):
        user, project = _setup_project(client)
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        response = client.post(
            reverse("facilities:work_transition", args=[project.pk, wo.pk, "fly"]),
        )
        assert response.status_code == 400

    def test_transition_role_gate_returns_400(self, client):
        # Engineer trying to triage
        user, project = _setup_project(client, role="maintenanceengineer")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        response = client.post(
            reverse("facilities:work_transition", args=[project.pk, wo.pk, "triage"]),
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestWorkOrderBulkView:
    def test_bulk_set_priority(self, client):
        user, project = _setup_project(client)
        w1 = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        w2 = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        response = client.post(
            reverse("facilities:work_bulk", args=[project.pk]),
            data={"action": "set_priority", "priority": "1", "wo_ids": [str(w1.pk), str(w2.pk)]},
        )
        assert response.status_code == 302
        w1.refresh_from_db()
        w2.refresh_from_db()
        assert w1.priority == 1
        assert w2.priority == 1

    def test_bulk_no_selection_returns_400(self, client):
        user, project = _setup_project(client)
        response = client.post(
            reverse("facilities:work_bulk", args=[project.pk]),
            data={"action": "cancel"},
        )
        assert response.status_code == 400


# ── Permit views ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPermitViews:
    def test_list_renders(self, client):
        user, project = _setup_project(client)
        PermitFactory(project=project, permit_number="HW-001", title="Hot work")
        response = client.get(reverse("facilities:permit_list", args=[project.pk]))
        assert response.status_code == 200
        assert b"HW-001" in response.content

    def test_create_post_persists(self, client):
        user, project = _setup_project(client)
        response = client.post(
            reverse("facilities:permit_create", args=[project.pk]),
            data={"permit_number": "P-99", "title": "Test", "kind": "other", "status": "draft"},
        )
        assert response.status_code == 302
        assert Permit.objects.filter(project=project, permit_number="P-99").exists()

    def test_revoke(self, client):
        user, project = _setup_project(client)
        permit = PermitFactory(project=project, status=Permit.Status.ACTIVE)
        response = client.post(
            reverse("facilities:permit_revoke", args=[project.pk, permit.pk]),
        )
        assert response.status_code == 302
        permit.refresh_from_db()
        assert permit.status == Permit.Status.REVOKED

    def test_detail_renders(self, client):
        user, project = _setup_project(client)
        permit = PermitFactory(project=project, permit_number="X-1", title="Permit X")
        response = client.get(reverse("facilities:permit_detail", args=[project.pk, permit.pk]))
        assert response.status_code == 200
        assert b"Permit X" in response.content


# ── ActionRequest views ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestActionRequestViews:
    def test_list_renders(self, client):
        user, project = _setup_project(client)
        ActionRequestFactory(project=project, title="Cold meeting room")
        response = client.get(reverse("facilities:ar_list", args=[project.pk]))
        assert response.status_code == 200
        assert b"Cold meeting room" in response.content

    def test_create_post_persists(self, client):
        user, project = _setup_project(client)
        response = client.post(
            reverse("facilities:ar_create", args=[project.pk]),
            data={"title": "New AR", "severity": "medium"},
        )
        assert response.status_code == 302
        ar = ActionRequest.objects.get(project=project, title="New AR")
        assert ar.submitted_by == user

    def test_triage(self, client):
        user, project = _setup_project(client)
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        response = client.post(
            reverse("facilities:ar_triage", args=[project.pk, ar.pk]),
            data={"note": "Routing"},
        )
        assert response.status_code == 302
        ar.refresh_from_db()
        assert ar.status == ActionRequest.Status.TRIAGED

    def test_dismiss(self, client):
        user, project = _setup_project(client)
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        response = client.post(
            reverse("facilities:ar_dismiss", args=[project.pk, ar.pk]),
            data={"note": "Duplicate"},
        )
        assert response.status_code == 302
        ar.refresh_from_db()
        assert ar.status == ActionRequest.Status.DISMISSED

    def test_escalate_creates_wo_and_marks_ar(self, client):
        user, project = _setup_project(client)
        ar = ActionRequestFactory(
            project=project, status=ActionRequest.Status.OPEN, title="Escalate me"
        )
        response = client.post(
            reverse("facilities:ar_escalate", args=[project.pk, ar.pk]),
        )
        assert response.status_code == 302
        ar.refresh_from_db()
        assert ar.status == ActionRequest.Status.ESCALATED
        wo = WorkOrder.objects.get(project=project, source_action_request=ar)
        assert wo.title == "Escalate me"

    def test_detail_renders(self, client):
        user, project = _setup_project(client)
        ar = ActionRequestFactory(project=project, title="Detail AR")
        response = client.get(reverse("facilities:ar_detail", args=[project.pk, ar.pk]))
        assert response.status_code == 200
        assert b"Detail AR" in response.content
