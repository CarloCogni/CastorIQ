# writeback/tests/test_supersede.py
"""Tests for the implicit supersede behavior on Modify chat."""

import json
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse

from chat.models import ChatSession, Message
from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCFileFactory
from writeback.models import ModificationProposal
from writeback.services.modification_service import ModificationService
from writeback.tests.factories import ModificationProposalFactory


def _modify_url(project_pk):
    return reverse("writeback:modify", kwargs={"pk": project_pk})


def _make_session(project, user) -> ChatSession:
    return ChatSession.objects.create(
        project=project,
        user=user,
        mode=ChatSession.Mode.MODIFY,
        title="Test session",
    )


def _make_pending_proposal(ifc_file, user, session) -> ModificationProposal:
    """Create a pending proposal linked to a fresh assistant message in the session."""
    msg = Message.objects.create(
        session=session,
        role=Message.Role.ASSISTANT,
        content="Pending proposal explanation.",
    )
    proposal = ModificationProposalFactory(
        ifc_file=ifc_file,
        created_by=user,
        status="pending",
        message=msg,
    )
    return proposal


# ── Service: supersede_pending() ────────────────────────────────────────────


@pytest.mark.django_db
class TestSupersedePending:
    """ModificationService.supersede_pending()."""

    def test_marks_pending_proposal_as_superseded(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        session = _make_session(project, user)
        proposal = _make_pending_proposal(ifc_file, user, session)

        svc = ModificationService(project, user=user)
        ids = svc.supersede_pending(session, user)

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.SUPERSEDED
        assert proposal.reviewed_by == user
        assert proposal.reviewed_at is not None
        assert ids == [str(proposal.id)]

    def test_returns_empty_list_when_no_pending(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        IFCFileFactory(project=project)
        session = _make_session(project, user)

        svc = ModificationService(project, user=user)
        assert svc.supersede_pending(session, user) == []

    def test_does_not_touch_already_resolved_proposals(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        session = _make_session(project, user)

        applied_msg = Message.objects.create(
            session=session, role=Message.Role.ASSISTANT, content="x"
        )
        applied = ModificationProposalFactory(
            ifc_file=ifc_file, created_by=user, status="applied", message=applied_msg
        )
        rejected_msg = Message.objects.create(
            session=session, role=Message.Role.ASSISTANT, content="y"
        )
        rejected = ModificationProposalFactory(
            ifc_file=ifc_file, created_by=user, status="rejected", message=rejected_msg
        )

        svc = ModificationService(project, user=user)
        svc.supersede_pending(session, user)

        applied.refresh_from_db()
        rejected.refresh_from_db()
        assert applied.status == "applied"
        assert rejected.status == "rejected"

    def test_only_supersedes_proposals_in_target_session(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        session_a = _make_session(project, user)
        session_b = _make_session(project, user)

        prop_a = _make_pending_proposal(ifc_file, user, session_a)
        prop_b = _make_pending_proposal(ifc_file, user, session_b)

        svc = ModificationService(project, user=user)
        svc.supersede_pending(session_a, user)

        prop_a.refresh_from_db()
        prop_b.refresh_from_db()
        assert prop_a.status == ModificationProposal.Status.SUPERSEDED
        assert prop_b.status == ModificationProposal.Status.PENDING

    def test_does_not_cross_project_boundary(self):
        user = UserFactory()
        project_a = ProjectFactory(owner=user)
        project_b = ProjectFactory(owner=user)
        ifc_a = IFCFileFactory(project=project_a)
        ifc_b = IFCFileFactory(project=project_b)
        session_a = _make_session(project_a, user)
        session_b = _make_session(project_b, user)

        prop_a = _make_pending_proposal(ifc_a, user, session_a)
        prop_b = _make_pending_proposal(ifc_b, user, session_b)

        # Service scoped to project_a should leave project_b's proposal alone
        # even if its session was somehow passed in.
        svc = ModificationService(project_a, user=user)
        svc.supersede_pending(session_b, user)

        prop_a.refresh_from_db()
        prop_b.refresh_from_db()
        assert prop_a.status == ModificationProposal.Status.PENDING
        assert prop_b.status == ModificationProposal.Status.PENDING


# ── View: approve/reject on a SUPERSEDED proposal ───────────────────────────


@pytest.mark.django_db
class TestApproveRejectAfterSupersede:
    """Once a proposal is SUPERSEDED, approve/reject must surface a clear error."""

    def test_approve_superseded_returns_410(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status=ModificationProposal.Status.SUPERSEDED,
        )
        client.force_login(user)

        response = client.post(
            _modify_url(project.pk),
            {"action": "approve", "proposal_id": str(proposal.pk)},
        )

        assert response.status_code == 410
        data = json.loads(response.content)
        assert data["superseded"] is True
        assert "superseded" in data["message"].lower()

    def test_reject_superseded_returns_410(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status=ModificationProposal.Status.SUPERSEDED,
        )
        client.force_login(user)

        response = client.post(
            _modify_url(project.pk),
            {"action": "reject", "proposal_id": str(proposal.pk)},
        )

        assert response.status_code == 410
        data = json.loads(response.content)
        assert data["superseded"] is True

    def test_approve_unknown_proposal_still_returns_404(self, client):
        """Genuine 'not found' is still 404, not 410."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        client.force_login(user)

        response = client.post(
            _modify_url(project.pk),
            {"action": "approve", "proposal_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404


# ── View: new propose call supersedes prior pending ─────────────────────────


@pytest.mark.django_db
class TestProposeSupersedesPriorPending:
    """When the user posts action=propose, prior pending proposals are SUPERSEDED."""

    def test_propose_marks_prior_pending_as_superseded(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        client.force_login(user)

        # Seed: one prior pending proposal in the session that the propose call will resolve
        # to (same user, same project, MODIFY mode → same auto-resolved session).
        session = ChatSession.objects.create(
            project=project,
            user=user,
            mode=ChatSession.Mode.MODIFY,
            title="Test session",
        )
        prior_msg = Message.objects.create(
            session=session, role=Message.Role.ASSISTANT, content="prior"
        )
        prior = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status=ModificationProposal.Status.PENDING,
            message=prior_msg,
        )

        # Mock the propose pipeline so we don't need a real LLM/Ollama.
        new_proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status=ModificationProposal.Status.PENDING,
        )
        new_proposal.diff_preview = "[]"
        new_proposal.save(update_fields=["diff_preview"])

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            # The view will call supersede_pending() on this real session;
            # delegate to the actual implementation to verify end-to-end.
            real_svc = ModificationService(project, user=user)
            instance.supersede_pending.side_effect = lambda s, u: real_svc.supersede_pending(s, u)
            instance.propose.return_value = new_proposal

            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Set fire rating to EI120"},
            )

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "proposed"
        assert str(prior.id) in data["superseded_ids"]

        prior.refresh_from_db()
        assert prior.status == ModificationProposal.Status.SUPERSEDED
        assert prior.reviewed_by == user
        assert prior.reviewed_at is not None

    def test_propose_with_no_prior_returns_empty_superseded_ids(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        client.force_login(user)

        new_proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status=ModificationProposal.Status.PENDING,
        )
        new_proposal.diff_preview = "[]"
        new_proposal.save(update_fields=["diff_preview"])

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.supersede_pending.return_value = []
            instance.propose.return_value = new_proposal

            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Set fire rating to EI120"},
            )

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["superseded_ids"] == []
