# facilities/tests/test_action_request_service.py
"""Tests for ``facilities.services.action_request_service.ActionRequestService``.

Cross-domain escalation (AR → WO) lives on WorkOrderService and is exercised
in ``test_workorder_service.TestActionRequestEscalation``. This file covers
only AR-side CRUD + triage + dismiss.
"""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import ActionRequest
from facilities.services.action_request_service import (
    ActionRequestNotFoundError,
    ActionRequestService,
    ActionRequestValidationError,
)
from facilities.tests.factories import ActionRequestFactory


def _service():
    project = ProjectFactory()
    user = UserFactory()
    return ActionRequestService(project, user), project, user


@pytest.mark.django_db
class TestReads:
    def test_get_returns_in_project_ar(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project)
        assert svc.get_action_request(ar.pk) == ar

    def test_get_misses_outside_project(self):
        svc, _, _ = _service()
        ar = ActionRequestFactory()
        with pytest.raises(ActionRequestNotFoundError):
            svc.get_action_request(ar.pk)

    def test_list_filters_by_status(self):
        svc, project, _ = _service()
        ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        ActionRequestFactory(project=project, status=ActionRequest.Status.TRIAGED)
        assert svc.list_action_requests(status=ActionRequest.Status.OPEN).count() == 1

    def test_list_excludes_closed_when_asked(self):
        svc, project, _ = _service()
        ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        ActionRequestFactory(project=project, status=ActionRequest.Status.ESCALATED)
        ActionRequestFactory(project=project, status=ActionRequest.Status.DISMISSED)
        assert svc.list_action_requests(include_closed=False).count() == 1

    def test_list_q_matches_title_or_description(self):
        svc, project, _ = _service()
        ActionRequestFactory(project=project, title="Heater broken")
        ActionRequestFactory(project=project, title="x")
        assert svc.list_action_requests(q="Heater").count() == 1


@pytest.mark.django_db
class TestMutations:
    def test_create_sets_submitter_and_project(self):
        svc, project, user = _service()
        ar = svc.create_action_request(title="Cold room")
        assert ar.project == project
        assert ar.submitted_by == user
        assert ar.status == ActionRequest.Status.OPEN

    def test_create_rejects_blank_title(self):
        svc, _, _ = _service()
        with pytest.raises(ActionRequestValidationError):
            svc.create_action_request(title="")

    def test_create_rejects_unknown_field(self):
        svc, _, _ = _service()
        with pytest.raises(ActionRequestValidationError):
            svc.create_action_request(title="x", status=ActionRequest.Status.TRIAGED)

    def test_update_changes_simple_fields(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project)
        svc.update_action_request(ar, title="renamed", severity="high")
        ar.refresh_from_db()
        assert ar.title == "renamed"
        assert ar.severity == "high"

    def test_update_rejects_cross_project(self):
        svc, _, _ = _service()
        ar = ActionRequestFactory()
        with pytest.raises(ActionRequestValidationError):
            svc.update_action_request(ar, title="x")

    def test_update_rejects_status_change(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project)
        with pytest.raises(ActionRequestValidationError):
            svc.update_action_request(ar, status=ActionRequest.Status.TRIAGED)

    def test_triage_open_to_triaged(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        svc.triage(ar, note="Routing to HVAC")
        ar.refresh_from_db()
        assert ar.status == ActionRequest.Status.TRIAGED
        assert ar.triage_note == "Routing to HVAC"

    def test_triage_rejects_dismissed(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.DISMISSED)
        with pytest.raises(ActionRequestValidationError):
            svc.triage(ar)

    def test_dismiss_records_note(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.OPEN)
        svc.dismiss(ar, note="Duplicate")
        ar.refresh_from_db()
        assert ar.status == ActionRequest.Status.DISMISSED
        assert ar.triage_note == "Duplicate"

    def test_dismiss_rejects_escalated(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.ESCALATED)
        with pytest.raises(ActionRequestValidationError):
            svc.dismiss(ar)

    def test_delete_removes_row(self):
        svc, project, _ = _service()
        ar = ActionRequestFactory(project=project)
        svc.delete_action_request(ar)
        assert not ActionRequest.objects.filter(pk=ar.pk).exists()
