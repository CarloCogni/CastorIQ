# facilities/tests/test_occupant_intake_service.py
"""Tests for OccupantIntakeService — single-pass LLM intake (M4.B).

LLM calls are mocked at ``_call_llm`` so the harness does not depend on
Ollama. The service contract under test:

* draft() returns an IntakeDraft for any non-empty input;
* LLM-returned values are clamped server-side (severity enum, length,
  hallucinated spatial ids);
* LLM failures degrade to a fallback draft (fallback_used=True);
* submit() forces submitted_by to the request user, never the impersonation
  payload, and stamps user_text + draft_payload + draft_confidence.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.models import ActionRequest
from facilities.services.occupant_intake_service import (
    OccupantIntakeService,
    OccupantIntakeValidationError,
)
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)


def _seat_user(user, project, name="Meeting Room 3-B"):
    """Helper — return (assigned_space, project) with the user seated as TENANT."""
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcSpace", name=name)
    space = IFCSpatialElementFactory(ifc_file=ifc_file, entity=entity, spatial_type="space")
    ProjectMembershipFactory(user=user, project=project, permission="viewer")
    ProjectRoleFactory(user=user, project=project, role="tenant", assigned_space=space)
    return space


@pytest.mark.django_db
class TestDraft:
    """draft() turns raw text into an IntakeDraft."""

    def test_empty_message_raises_validation_error(self):
        """Blank input is rejected before the LLM is called."""
        user = UserFactory()
        project = ProjectFactory()
        service = OccupantIntakeService(project, user)

        with pytest.raises(OccupantIntakeValidationError):
            service.draft("")
        with pytest.raises(OccupantIntakeValidationError):
            service.draft("   ")

    def test_happy_path_canned_payload_yields_draft(self):
        """Mocked LLM returns full payload → draft picks up every field."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "Meeting Room 3-B is cold",
            "description": "Thermostat reads 17°C since this morning.",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
            "confidence": 88,
            "explanation": "HVAC complaint anchored to the assigned seat.",
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("Meeting room 3-B is cold")

        assert draft.fallback_used is False
        assert draft.title == "Meeting Room 3-B is cold"
        assert draft.severity == "medium"
        assert draft.affected_spatial == space
        assert draft.confidence == 88
        assert draft.user_text == "Meeting room 3-B is cold"
        assert space in draft.candidates  # candidate slice non-empty

    def test_severity_outside_enum_clamped_to_default(self):
        """LLM-returned severity 'critical' (not in enum) collapses to 'medium'."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "Leak",
            "description": "Bad",
            "severity": "critical",  # not in enum
            "affected_spatial_id": None,
            "confidence": 50,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("water leak")

        assert draft.severity == "medium"

    def test_hallucinated_spatial_id_falls_back_to_assigned_space(self):
        """LLM returns a uuid for a space that doesn't belong to the project."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "Cold",
            "description": "It's cold",
            "severity": "low",
            "affected_spatial_id": "00000000-0000-0000-0000-000000000000",
            "confidence": 60,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("the room is cold")

        # Hallucinated id is filtered; assigned space prior takes over.
        assert draft.affected_spatial == space

    def test_title_overlong_is_truncated(self):
        """A 1000-char title is truncated to 255 with ellipsis."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "X" * 1000,
            "description": "ok",
            "severity": "medium",
            "affected_spatial_id": None,
            "confidence": 50,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("anything")

        assert len(draft.title) <= 255
        assert draft.title.endswith("…")

    def test_description_overlong_is_truncated(self):
        """A description >4000 chars is clamped."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "ok",
            "description": "Y" * 5000,
            "severity": "medium",
            "affected_spatial_id": None,
            "confidence": 50,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("anything")

        assert len(draft.description) <= 4000

    def test_llm_timeout_falls_back_to_stub_draft(self):
        """LLM ReadTimeout → fallback_used=True draft, not an exception."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        with patch.object(
            OccupantIntakeService,
            "_call_llm",
            side_effect=httpx.ReadTimeout("hard cap"),
        ):
            draft = service.draft("anything")

        assert draft.fallback_used is True
        assert draft.title  # non-empty fallback title
        assert draft.confidence == 0

    def test_llm_returns_garbage_falls_back(self):
        """Non-JSON / ValueError on parse → fallback draft."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        with patch.object(
            OccupantIntakeService,
            "_call_llm",
            side_effect=ValueError("non-JSON garbage"),
        ):
            draft = service.draft("anything")

        assert draft.fallback_used is True


@pytest.mark.django_db
class TestSubmit:
    """submit() persists the draft as an ActionRequest."""

    def test_submit_persists_ar_with_provenance(self):
        """A confirmed draft writes title, description, severity, spatial, payload."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "Meeting Room 3-B is cold",
            "description": "Thermostat reads 17°C.",
            "severity": "medium",
            "affected_spatial_id": str(space.pk),
            "confidence": 88,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("Meeting room 3-B is cold")

        ar = service.submit(draft)

        assert ar.pk is not None
        assert ar.project == project
        assert ar.submitted_by == user
        assert ar.title == "Meeting Room 3-B is cold"
        assert ar.severity == "medium"
        assert ar.affected_spatial == space
        assert ar.user_text == "Meeting room 3-B is cold"
        assert ar.draft_confidence == 88
        # Payload echoes the LLM raw + service-side decisions
        assert ar.draft_payload["severity"] == "medium"
        assert ar.draft_payload["title"] == "Meeting Room 3-B is cold"
        assert ar.draft_payload["fallback_used"] is False

    def test_submit_overrides_apply(self):
        """User edits in the review modal override the LLM draft."""
        user = UserFactory()
        project = ProjectFactory()
        space = _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        canned = {
            "title": "Auto-drafted",
            "description": "auto",
            "severity": "low",
            "affected_spatial_id": str(space.pk),
            "confidence": 70,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("foo")

        ar = service.submit(
            draft,
            title_override="User-corrected title",
            description_override="User-supplied details",
            severity_override="high",
        )

        assert ar.title == "User-corrected title"
        assert ar.description == "User-supplied details"
        assert ar.severity == "high"

    def test_submit_forces_submitted_by_to_self(self):
        """Even if a buggy view passes another user, submitted_by is forced to self."""
        user = UserFactory()
        attacker = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        # Service is constructed with `user`; no API takes submitted_by from
        # callers, but we double-check the persisted row.
        service = OccupantIntakeService(project, user)
        canned = {
            "title": "ok",
            "description": "ok",
            "severity": "low",
            "affected_spatial_id": None,
            "confidence": 50,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("anything")

        ar = service.submit(draft)

        assert ar.submitted_by == user
        assert ar.submitted_by != attacker

    def test_submit_fallback_draft_still_creates_ar(self):
        """The fallback path must still let the AR land — no LLM, no problem."""
        user = UserFactory()
        project = ProjectFactory()
        _seat_user(user, project)
        service = OccupantIntakeService(project, user)

        with patch.object(
            OccupantIntakeService,
            "_call_llm",
            side_effect=httpx.ReadTimeout("hard cap"),
        ):
            draft = service.draft("My ceiling is leaking")

        ar = service.submit(draft)

        assert ar.pk is not None
        assert ar.title == "My ceiling is leaking"
        assert ar.draft_payload["fallback_used"] is True
        assert ar.status == ActionRequest.Status.OPEN

    def test_submit_off_project_spatial_override_rejected(self):
        """An override pointing at a space from another project is refused."""
        user = UserFactory()
        project = ProjectFactory()
        other_project = ProjectFactory()
        _seat_user(user, project)
        # A SPACE on another project.
        other_ifc = IFCFileFactory(project=other_project)
        other_entity = IFCEntityFactory(ifc_file=other_ifc, ifc_type="IfcSpace", name="Foreign")
        other_space = IFCSpatialElementFactory(
            ifc_file=other_ifc, entity=other_entity, spatial_type="space"
        )

        service = OccupantIntakeService(project, user)
        canned = {
            "title": "ok",
            "description": "ok",
            "severity": "low",
            "affected_spatial_id": None,
            "confidence": 50,
        }
        with patch.object(OccupantIntakeService, "_call_llm", return_value=canned):
            draft = service.draft("anything")

        with pytest.raises(OccupantIntakeValidationError):
            service.submit(
                draft,
                affected_spatial_override=other_space,
                affected_spatial_override_explicit=True,
            )
