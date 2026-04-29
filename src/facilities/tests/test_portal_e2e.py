# facilities/tests/test_portal_e2e.py
"""End-to-end review-gate sentence test (M4.E).

Mirrors the §4 day-in-the-life slice: a TENANT user submits the canonical
"Meeting room 3-B is cold" message, the LLM (mocked) drafts the AR, the
occupant confirms, the AR persists, the FM escalates to a Work Order, and
the WO carries the AR provenance.

LLM is mocked at ``OccupantIntakeService._call_llm`` so the test does not
depend on Ollama. Asserts everything the human reviewer would check in the
browser walkthrough.
"""

from __future__ import annotations

from unittest.mock import patch

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
    WorkOrder,
    WorkOrderStatus,
)
from facilities.services.occupant_intake_service import OccupantIntakeService
from facilities.services.workorder_service import WorkOrderService
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)


@pytest.mark.django_db
def test_review_gate_sentence_round_trip(client):
    """The full M4 review gate, in one test.

    Tenant types 'Meeting room 3-B is cold' → AR drafted with affected_spatial
    set to the matching SPACE → confirms → AR exists with submitted_by=self
    → FM escalates with category='corrective' → WO exists with
    source_action_request set and AR moves to ESCALATED.
    """
    # Arrange: a project with the canonical SPACE and two users.
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    space_entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcSpace", name="Meeting Room 3-B")
    space = IFCSpatialElementFactory(ifc_file=ifc_file, entity=space_entity, spatial_type="space")

    tenant = UserFactory()
    ProjectMembershipFactory(user=tenant, project=project, permission="viewer")
    ProjectRoleFactory(user=tenant, project=project, role="tenant", assigned_space=space)

    fm_user = UserFactory()
    ProjectMembershipFactory(user=fm_user, project=project, permission="editor")
    ProjectRoleFactory(user=fm_user, project=project, role="facilitiesmanager")

    # Act 1: tenant submits the demo sentence.
    client.force_login(tenant)
    canned_payload = {
        "title": "Meeting Room 3-B is cold",
        "description": "Thermostat reads 17°C since this morning.",
        "severity": "medium",
        "affected_spatial_id": str(space.pk),
        "confidence": 88,
        "explanation": "HVAC complaint anchored to the assigned seat.",
    }
    with patch.object(OccupantIntakeService, "_call_llm", return_value=canned_payload):
        draft_response = client.post(
            reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
            {"user_message": "Meeting room 3-B is cold"},
        )

    assert draft_response.status_code == 200
    drafts = client.session.get("portal_drafts") or {}
    assert len(drafts) == 1
    draft_token = next(iter(drafts.keys()))

    # Act 2: tenant confirms the draft.
    confirm_response = client.post(
        reverse("facilities:portal_intake_confirm", kwargs={"pk": project.pk}),
        {
            "draft_token": draft_token,
            "title": "Meeting Room 3-B is cold",
            "description": "Thermostat reads 17°C since this morning.",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
        },
        HTTP_HX_REQUEST="true",
    )
    assert confirm_response.status_code == 204

    # Assert 1: AR persisted with the right shape.
    ar = ActionRequest.objects.get(project=project)
    assert ar.title == "Meeting Room 3-B is cold"
    assert ar.severity == "medium"
    assert ar.affected_spatial == space
    assert ar.submitted_by == tenant
    assert ar.user_text == "Meeting room 3-B is cold"
    assert ar.draft_confidence == 88
    assert ar.status == ActionRequest.Status.OPEN

    # Act 3: FM escalates to a Work Order with category=corrective.
    wo_service = WorkOrderService(project, fm_user)
    wo = wo_service.escalate_action_request(ar, category="corrective", priority=2)

    # Assert 2: WO exists, AR escalated, provenance preserved.
    wo.refresh_from_db()
    ar.refresh_from_db()
    assert isinstance(wo, WorkOrder)
    assert wo.source_action_request_id == ar.id
    assert wo.title == "Meeting Room 3-B is cold"
    assert wo.affected_spatial == space
    assert wo.category == "corrective"
    assert wo.priority == 2
    assert wo.status == WorkOrderStatus.DRAFT
    assert ar.status == ActionRequest.Status.ESCALATED

    # Assert 3: tenant's portal landing now shows the AR in their recent list.
    landing = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))
    assert landing.status_code == 200
    recent = landing.context["portal_recent_ars"]
    assert ar in recent

    # Assert 4: FM Requests page shows the AR (and its escalated status) too.
    client.force_login(fm_user)
    fm_requests = client.get(reverse("facilities:ar_list", kwargs={"pk": project.pk}))
    assert fm_requests.status_code == 200
    fm_listed = list(fm_requests.context["page_obj"].object_list)
    assert ar in fm_listed
