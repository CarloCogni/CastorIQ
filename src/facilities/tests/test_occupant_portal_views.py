# facilities/tests/test_occupant_portal_views.py
"""End-to-end tests for the Occupant Portal views (M4.B)."""

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
from facilities.models import ActionRequest
from facilities.services.occupant_intake_service import OccupantIntakeService
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)


def _login(client, user):
    client.force_login(user)


def _seat_user(user, project, name="Meeting Room 3-B"):
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcSpace", name=name)
    space = IFCSpatialElementFactory(ifc_file=ifc_file, entity=entity, spatial_type="space")
    ProjectMembershipFactory(user=user, project=project, permission="viewer")
    ProjectRoleFactory(user=user, project=project, role="tenant", assigned_space=space)
    return space


@pytest.mark.django_db
class TestAccess:
    """OccupantPortalAccessMixin — who can hit the portal endpoints."""

    def test_unauthenticated_redirects(self, client):
        project = ProjectFactory()
        url = reverse("facilities:portal_intake", kwargs={"pk": project.pk})
        response = client.get(url)
        assert response.status_code in {302, 403}

    def test_non_member_403(self, client):
        outsider = UserFactory()
        project = ProjectFactory()
        _login(client, outsider)
        response = client.get(reverse("facilities:portal_intake", kwargs={"pk": project.pk}))
        assert response.status_code == 403

    def test_viewer_without_role_403(self, client):
        """A bare VIEWER without a TENANT/OCCUPANT role cannot use the portal."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(user=user, project=project, permission="viewer")
        _login(client, user)
        response = client.get(reverse("facilities:portal_intake", kwargs={"pk": project.pk}))
        assert response.status_code == 403

    def test_tenant_viewer_can_access(self, client):
        """TENANT + VIEWER passes the OR-write gate."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        _login(client, user)

        response = client.get(reverse("facilities:portal_intake", kwargs={"pk": project.pk}))
        assert response.status_code == 200
        assert "Tell us what you need" in response.content.decode()

    def test_editor_without_role_can_access(self, client):
        """FM staff (EDITOR+) also pass — useful for QA dogfooding."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(user=user, project=project, permission="editor")
        _login(client, user)

        response = client.get(reverse("facilities:portal_intake", kwargs={"pk": project.pk}))
        assert response.status_code == 200


@pytest.mark.django_db
class TestDraftPost:
    """POST intake/ — draft fragment renders the review modal with candidates."""

    def test_blank_message_renders_drawer_with_error(self, client):
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        _login(client, user)

        response = client.post(
            reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
            {"user_message": "  "},
        )

        assert response.status_code == 400
        body = response.content.decode()
        assert "Tell us what you need" in body  # drawer re-rendered

    def test_happy_draft_renders_review_fragment(self, client):
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        _login(client, user)

        canned = {
            "title": "Meeting Room 3-B is cold",
            "description": "Thermostat reads 17°C.",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
            "confidence": 88,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            response = client.post(
                reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
                {"user_message": "Meeting room 3-B is cold"},
            )

        assert response.status_code == 200
        body = response.content.decode()
        assert "Looks right? Confirm to send." in body
        assert "Meeting Room 3-B is cold" in body  # extracted title rendered
        # Session should have cached the draft for the confirm POST.
        drafts = client.session.get("portal_drafts") or {}
        assert len(drafts) == 1
        token, payload = next(iter(drafts.items()))
        assert payload["title"] == "Meeting Room 3-B is cold"
        assert payload["affected_spatial_id"] == str(space.pk)


@pytest.mark.django_db
class TestConfirmPost:
    """POST intake/confirm/ — persists the AR or replays the review fragment."""

    def test_confirm_persists_ar(self, client):
        """Happy path: occupant confirms → ActionRequest exists with submitted_by=self."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        _login(client, user)

        canned = {
            "title": "Cold room",
            "description": "freezing",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
            "confidence": 80,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            client.post(
                reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
                {"user_message": "Meeting room 3-B is cold"},
            )

        drafts = client.session.get("portal_drafts") or {}
        token = next(iter(drafts.keys()))

        response = client.post(
            reverse("facilities:portal_intake_confirm", kwargs={"pk": project.pk}),
            {
                "draft_token": token,
                "title": "Cold room",
                "description": "freezing",
                "severity": "medium",
                "affected_spatial_id": str(space.pk),
            },
            HTTP_HX_REQUEST="true",
        )

        # HTMX-style success: 204 + HX-Redirect header.
        assert response.status_code == 204
        assert response.headers["HX-Redirect"].endswith(
            reverse("facilities:tab", kwargs={"pk": project.pk})
        )

        ar = ActionRequest.objects.get(project=project)
        assert ar.title == "Cold room"
        assert ar.submitted_by == user
        assert ar.severity == "medium"
        assert ar.affected_spatial == space
        assert ar.user_text == "Meeting room 3-B is cold"
        assert ar.draft_confidence == 80
        # Cached draft was dropped — no double-submit on back-button.
        assert token not in (client.session.get("portal_drafts") or {})

    def test_confirm_with_unknown_token_returns_error(self, client):
        """A confirm POST with no matching cached draft fails cleanly."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        _login(client, user)

        response = client.post(
            reverse("facilities:portal_intake_confirm", kwargs={"pk": project.pk}),
            {"draft_token": "deadbeef" * 8},
        )
        assert response.status_code == 400

    def test_user_overrides_take_priority(self, client):
        """User edits in the review modal override the LLM output on submit."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        _login(client, user)

        canned = {
            "title": "Auto-drafted",
            "description": "auto",
            "severity": "low",
            "affected_spatial_id": str(space.pk),
            "confidence": 60,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            client.post(
                reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
                {"user_message": "anything"},
            )

        token = next(iter((client.session.get("portal_drafts") or {}).keys()))

        client.post(
            reverse("facilities:portal_intake_confirm", kwargs={"pk": project.pk}),
            {
                "draft_token": token,
                "title": "User-corrected title",
                "description": "User-corrected detail",
                "severity": "high",
                "affected_spatial_id": str(space.pk),
            },
            HTTP_HX_REQUEST="true",
        )

        ar = ActionRequest.objects.get(project=project)
        assert ar.title == "User-corrected title"
        assert ar.description == "User-corrected detail"
        assert ar.severity == "high"

    def test_non_htmx_confirm_returns_redirect(self, client):
        """Without the HX-Request header, the confirm view returns a 302 redirect."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        _login(client, user)

        canned = {
            "title": "Cold",
            "description": "ok",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
            "confidence": 70,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            client.post(
                reverse("facilities:portal_intake", kwargs={"pk": project.pk}),
                {"user_message": "anything"},
            )

        token = next(iter((client.session.get("portal_drafts") or {}).keys()))

        response = client.post(
            reverse("facilities:portal_intake_confirm", kwargs={"pk": project.pk}),
            {
                "draft_token": token,
                "title": "Cold",
                "description": "ok",
                "severity": "medium",
            },
        )
        assert response.status_code == 302
        assert ActionRequest.objects.filter(project=project).exists()
