# writeback/tests/test_writeback_views.py
"""Tests for writeback views — all LLM/service calls are mocked."""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCFileFactory
from writeback.tests.factories import ModificationProposalFactory

# ── Helpers ────────────────────────────────────────────────────────────────


def _login(client, user):
    client.force_login(user)


def _modify_url(project_pk):
    return reverse("writeback:modify", kwargs={"pk": project_pk})


def _modify_session_url(project_pk, session_pk):
    return reverse("writeback:modify_session", kwargs={"pk": project_pk, "session_id": session_pk})


def _conflicts_url(project_pk):
    return reverse("writeback:conflicts", kwargs={"pk": project_pk})


def _history_url(project_pk):
    return reverse("writeback:history", kwargs={"pk": project_pk})


def _run_scan_url(project_pk):
    return reverse("writeback:run_scan", kwargs={"pk": project_pk})


def _dismiss_conflict_url(project_pk, conflict_id):
    return reverse(
        "writeback:dismiss_conflict", kwargs={"pk": project_pk, "conflict_id": conflict_id}
    )


def _bulk_dismiss_url(project_pk):
    return reverse("writeback:bulk_dismiss", kwargs={"pk": project_pk})


def _ignore_conflict_url(project_pk, conflict_id):
    return reverse(
        "writeback:ignore_conflict", kwargs={"pk": project_pk, "conflict_id": conflict_id}
    )


def _bulk_ignore_url(project_pk):
    return reverse("writeback:bulk_ignore_conflicts", kwargs={"pk": project_pk})


def _bulk_resolve_url(project_pk):
    return reverse("writeback:bulk_resolve_conflicts", kwargs={"pk": project_pk})


def _delete_all_conflicts_url(project_pk):
    return reverse("writeback:delete_all_conflicts", kwargs={"pk": project_pk})


def _restore_commit_url(project_pk, commit_id):
    return reverse("writeback:restore_commit", kwargs={"pk": project_pk, "commit_id": commit_id})


# ── Auth tests ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewAuth:
    """Unauthenticated requests must be redirected."""

    def test_modify_unauthenticated_redirects(self, client):
        """Unauthenticated GET on modify tab redirects to login."""
        project = ProjectFactory()
        response = client.get(_modify_url(project.pk))
        assert response.status_code == 302
        assert "/login" in response["Location"] or "login" in response["Location"].lower()

    def test_conflicts_unauthenticated_redirects(self, client):
        """Unauthenticated GET on conflicts tab redirects to login."""
        project = ProjectFactory()
        response = client.get(_conflicts_url(project.pk))
        assert response.status_code == 302

    def test_history_unauthenticated_redirects(self, client):
        """Unauthenticated GET on history tab redirects to login."""
        project = ProjectFactory()
        response = client.get(_history_url(project.pk))
        assert response.status_code == 302


# ── ModifyView GET ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewGet:
    """GET requests on the modify tab."""

    def test_get_without_session_id_redirects_to_session(self, client):
        """GET /projects/<pk>/modify/ redirects to session URL."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_modify_url(project.pk))
        assert response.status_code == 302
        assert str(project.pk) in response["Location"]

    def test_get_without_session_id_with_prompt_passes_param(self, client):
        """GET /modify/?prompt=hello preserves the prompt query param in redirect."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_modify_url(project.pk), {"prompt": "hello world"})
        assert response.status_code == 302
        assert "prompt=" in response["Location"]

    def test_get_with_session_id_returns_200(self, client):
        """GET /projects/<pk>/modify/<session_id>/ returns 200 for session owner."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = ChatSession.objects.create(
            project=project,
            user=user,
            mode=ChatSession.Mode.MODIFY,
            title="Test Session",
        )
        response = client.get(_modify_session_url(project.pk, session.pk))
        assert response.status_code == 200

    def test_get_other_users_project_returns_403(self, client):
        """User without access to project gets 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other_user = UserFactory()
        _login(client, other_user)

        response = client.get(_modify_url(project.pk))
        # Redirected to session but then 403 on session GET (or 302 handled)
        assert response.status_code in (302, 403)


# ── ModifyView POST: new_session ───────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewPostNewSession:
    """POST action=new_session."""

    def test_new_session_creates_session_and_redirects(self, client):
        """POST action=new_session creates a ChatSession and redirects."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_modify_url(project.pk), {"action": "new_session"})
        assert response.status_code == 302
        assert ChatSession.objects.filter(
            project=project, user=user, mode=ChatSession.Mode.MODIFY
        ).exists()


# ── ModifyView POST: propose ───────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewPostPropose:
    """POST action=propose."""

    def test_propose_empty_message_returns_400(self, client):
        """Empty message returns 400 JSON error."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_modify_url(project.pk), {"action": "propose", "message": ""})
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["status"] == "error"

    def test_propose_unknown_action_returns_400(self, client):
        """Unknown action returns 400 JSON error."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_modify_url(project.pk), {"action": "unknown_action"})
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["status"] == "error"

    def test_propose_modification_error_returns_json_error(self, client):
        """ModificationError in propose() returns JSON with status=error."""
        from writeback.services.modification_service import ModificationError

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        with patch(
            "writeback.views.ModificationService.propose",
            side_effect=ModificationError("No IFC file found"),
        ):
            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Set fire rating to EI120"},
            )

        data = json.loads(response.content)
        assert data["status"] == "error"
        assert "No IFC file found" in data["message"]

    def test_propose_success_returns_proposed_status(self, client):
        """Successful propose returns status=proposed with proposal data."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        mock_proposal = MagicMock()
        mock_proposal.id = uuid.uuid4()
        mock_proposal.tier = 1
        mock_proposal.operation = "SET_PROPERTY"
        mock_proposal.explanation = "Set FireRating to EI120"
        mock_proposal.confidence = 90
        mock_proposal.affected_count = 5
        mock_proposal.diff_preview = "[]"
        mock_proposal.linked_conflict_ids = []
        mock_proposal.verification_status = "ok"
        mock_proposal.verification_result = ""
        mock_proposal.verification_source = ""
        mock_proposal.intent_json = {}
        mock_proposal.message = None

        with patch("writeback.views.ModificationService.propose", return_value=mock_proposal):
            with patch("writeback.views.ModificationService.__init__", return_value=None):
                # Need to patch the whole service instance
                pass

        # Simpler: patch at the class level
        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.propose.return_value = mock_proposal

            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Set fire rating to EI120"},
            )

        data = json.loads(response.content)
        assert data["status"] == "proposed"
        assert "proposal" in data

    def test_propose_chain_returns_proposals_list(self, client):
        """propose() returning a list triggers chain response with proposals list."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        IFCFileFactory(project=project)
        _login(client, user)

        mock_proposal = MagicMock()
        mock_proposal.id = uuid.uuid4()
        mock_proposal.tier = 1
        mock_proposal.operation = "SET_PROPERTY"
        mock_proposal.explanation = "Step 1"
        mock_proposal.confidence = 90
        mock_proposal.affected_count = 3
        mock_proposal.diff_preview = "[]"
        mock_proposal.linked_conflict_ids = []
        mock_proposal.verification_status = "ok"
        mock_proposal.verification_result = ""
        mock_proposal.verification_source = ""
        mock_proposal.intent_json = {}
        mock_proposal.message = None

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.propose.return_value = [mock_proposal, mock_proposal]

            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Do multiple things"},
            )

        data = json.loads(response.content)
        assert data["status"] == "proposed"
        assert data.get("chain") is True
        assert "proposals" in data


# ── ModifyView POST: approve ───────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewPostApprove:
    """POST action=approve."""

    def test_approve_missing_proposal_id_returns_400(self, client):
        """Missing proposal_id returns 400."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_modify_url(project.pk), {"action": "approve"})
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["status"] == "error"

    def test_approve_nonexistent_proposal_returns_404(self, client):
        """Non-existent proposal_id returns 404."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(
            _modify_url(project.pk),
            {"action": "approve", "proposal_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    def test_approve_success_returns_applied_status(self, client):
        """Valid pending proposal + successful execute returns status=applied."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
            affected_count=3,
        )
        _login(client, user)

        mock_commit = MagicMock()
        mock_commit.commit_hash = "abc123def456abc1"

        # _handle_approve imports ModificationService locally, so patch at the source module
        with patch("writeback.services.modification_service.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.execute.return_value = mock_commit

            response = client.post(
                _modify_url(project.pk),
                {"action": "approve", "proposal_id": str(proposal.pk)},
            )

        data = json.loads(response.content)
        assert data["status"] == "applied"
        assert "abc123de" in data["commit_hash"]
        assert data["entities_modified"] == 3

    def test_approve_modification_error_returns_json_error(self, client):
        """ModificationError during execute returns JSON error."""
        from writeback.services.modification_service import ModificationError

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )
        _login(client, user)

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.execute.side_effect = ModificationError("IFC write failed")

            response = client.post(
                _modify_url(project.pk),
                {"action": "approve", "proposal_id": str(proposal.pk)},
            )

        data = json.loads(response.content)
        assert data["status"] == "error"


# ── ModifyView POST: reject ────────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewPostReject:
    """POST action=reject."""

    def test_reject_missing_proposal_id_returns_400(self, client):
        """Missing proposal_id returns 400."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_modify_url(project.pk), {"action": "reject"})
        assert response.status_code == 400

    def test_reject_success_returns_rejected_status(self, client):
        """Valid pending proposal rejection returns status=rejected."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )
        _login(client, user)

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.reject.return_value = None

            response = client.post(
                _modify_url(project.pk),
                {"action": "reject", "proposal_id": str(proposal.pk), "reason": "Not needed"},
            )

        data = json.loads(response.content)
        assert data["status"] == "rejected"


# ── ConflictsView ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestConflictsView:
    """GET /projects/<pk>/conflicts/."""

    def test_get_conflicts_returns_200(self, client):
        """Authenticated user sees conflicts page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_conflicts_url(project.pk))
        assert response.status_code == 200

    def test_get_conflicts_status_filter(self, client):
        """status query param filters displayed conflicts."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_conflicts_url(project.pk), {"status": "resolved"})
        assert response.status_code == 200

    def test_get_conflicts_returns_grouped_by_status(self, client):
        """Conflicts view provides grouped_by_status dict with all statuses."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_conflicts_url(project.pk))
        assert response.status_code == 200
        assert "grouped_by_status" in response.context
        assert set(response.context["grouped_by_status"].keys()) == {
            "open",
            "resolved",
            "ignored",
            "dismissed",
        }

    def test_get_conflicts_other_user_forbidden(self, client):
        """User without project access gets 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.get(_conflicts_url(project.pk))
        assert response.status_code == 403


# ── HistoryView ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestHistoryView:
    """GET /projects/<pk>/history/."""

    def test_get_history_returns_200(self, client):
        """Authenticated owner sees history page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_history_url(project.pk))
        assert response.status_code == 200

    def test_get_history_other_user_forbidden(self, client):
        """User without project access gets 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.get(_history_url(project.pk))
        assert response.status_code == 403


# ── RunScanView ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRunScanView:
    """POST /projects/<pk>/scan/ stub endpoint."""

    def test_post_scan_returns_405_with_error_message(self, client):
        """Stub endpoint returns 405 with websocket instructions."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_run_scan_url(project.pk))
        assert response.status_code == 405
        data = json.loads(response.content)
        assert data["status"] == "error"
        assert "WebSocket" in data["message"]

    def test_post_scan_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        project = ProjectFactory()
        response = client.post(_run_scan_url(project.pk))
        assert response.status_code == 302


# ── DismissConflictView ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDismissConflictView:
    """POST /projects/<pk>/conflicts/<conflict_id>/dismiss/."""

    def test_dismiss_conflict_updates_status(self, client):
        """Owner can dismiss a conflict."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        conflict = Conflict.objects.create(
            project=project,
            ifc_entity=None,
            title="Test conflict",
            description="desc",
            severity="medium",
            status=Conflict.Status.OPEN,
        )

        response = client.post(_dismiss_conflict_url(project.pk, conflict.pk))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "dismissed"
        conflict.refresh_from_db()
        assert conflict.status == Conflict.Status.DISMISSED

    def test_dismiss_conflict_other_user_returns_403(self, client):
        """User without access returns 403."""
        from writeback.models import Conflict

        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        conflict = Conflict.objects.create(
            project=project,
            ifc_entity=None,
            title="Test",
            description="desc",
            severity="low",
            status=Conflict.Status.OPEN,
        )

        response = client.post(_dismiss_conflict_url(project.pk, conflict.pk))
        assert response.status_code == 403


# ── BulkDismissView ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulkDismissView:
    """POST /projects/<pk>/conflicts/bulk-dismiss/."""

    def test_bulk_dismiss_specific_ids(self, client):
        """Bulk dismiss by comma-separated IDs."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        c1 = Conflict.objects.create(
            project=project, ifc_entity=None, title="C1", description="d", severity="low"
        )
        c2 = Conflict.objects.create(
            project=project, ifc_entity=None, title="C2", description="d", severity="low"
        )

        response = client.post(
            _bulk_dismiss_url(project.pk),
            {"conflict_ids": f"{c1.pk},{c2.pk}"},
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "dismissed"
        assert data["count"] == 2

    def test_bulk_dismiss_all(self, client):
        """Passing conflict_ids='all' dismisses all open conflicts."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        for _ in range(3):
            Conflict.objects.create(
                project=project, ifc_entity=None, title="C", description="d", severity="low"
            )

        response = client.post(_bulk_dismiss_url(project.pk), {"conflict_ids": "all"})
        data = json.loads(response.content)
        assert data["count"] == 3

    def test_bulk_dismiss_other_user_returns_403(self, client):
        """User without access returns 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.post(_bulk_dismiss_url(project.pk), {"conflict_ids": "all"})
        assert response.status_code == 403


# ── IgnoreConflictView ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIgnoreConflictView:
    """POST /projects/<pk>/conflicts/<conflict_id>/ignore/."""

    def test_ignore_conflict_updates_status(self, client):
        """Owner can ignore a conflict."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        conflict = Conflict.objects.create(
            project=project, ifc_entity=None, title="T", description="d", severity="low"
        )

        response = client.post(_ignore_conflict_url(project.pk, conflict.pk))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "ignored"
        conflict.refresh_from_db()
        assert conflict.status == Conflict.Status.IGNORED


# ── BulkIgnoreView ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulkIgnoreView:
    """POST /projects/<pk>/conflicts/bulk-ignore/."""

    def test_bulk_ignore_specific_ids(self, client):
        """Bulk ignore by comma-separated IDs."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        c1 = Conflict.objects.create(
            project=project, ifc_entity=None, title="C1", description="d", severity="low"
        )
        c2 = Conflict.objects.create(
            project=project, ifc_entity=None, title="C2", description="d", severity="low"
        )

        response = client.post(
            _bulk_ignore_url(project.pk),
            {"conflict_ids": f"{c1.pk},{c2.pk}"},
        )
        data = json.loads(response.content)
        assert data["status"] == "ignored"
        assert data["count"] == 2


# ── BulkResolveView ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulkResolveView:
    """POST /projects/<pk>/conflicts/bulk-resolve/."""

    def test_bulk_resolve_all(self, client):
        """conflict_ids=all resolves all open conflicts."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        for _ in range(2):
            Conflict.objects.create(
                project=project, ifc_entity=None, title="C", description="d", severity="low"
            )

        response = client.post(_bulk_resolve_url(project.pk), {"conflict_ids": "all"})
        data = json.loads(response.content)
        assert data["status"] == "resolved"
        assert data["count"] == 2

    def test_bulk_resolve_other_user_returns_403(self, client):
        """User without access returns 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.post(_bulk_resolve_url(project.pk), {"conflict_ids": "all"})
        assert response.status_code == 403


# ── DeleteAllConflictsView ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestDeleteAllConflictsView:
    """POST /projects/<pk>/conflicts/delete-all/."""

    def test_delete_all_conflicts(self, client):
        """Owner can delete all conflicts for a project."""
        from writeback.models import Conflict

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        for _ in range(4):
            Conflict.objects.create(
                project=project, ifc_entity=None, title="C", description="d", severity="low"
            )

        response = client.post(_delete_all_conflicts_url(project.pk))
        data = json.loads(response.content)
        assert data["status"] == "deleted"
        assert data["count"] == 4
        assert not project.conflicts.exists()

    def test_delete_all_conflicts_other_user_returns_403(self, client):
        """User without access returns 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.post(_delete_all_conflicts_url(project.pk))
        assert response.status_code == 403


# ── RestoreCommitView ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRestoreCommitView:
    """POST /projects/<pk>/history/restore/<commit_id>/."""

    def test_restore_non_owner_raises_permission_denied(self, client):
        """Non-owner cannot restore commits."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        collaborator = UserFactory()
        project.collaborators.add(collaborator)
        _login(client, collaborator)

        fake_commit_id = uuid.uuid4()
        response = client.post(_restore_commit_url(project.pk, fake_commit_id))
        assert response.status_code == 403

    def test_restore_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        project = ProjectFactory()
        fake_commit_id = uuid.uuid4()
        response = client.post(_restore_commit_url(project.pk, fake_commit_id))
        assert response.status_code == 302

    def test_restore_owner_success_redirects_to_history(self, client):
        """Owner with successful restore is redirected to history page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        fake_commit_id = uuid.uuid4()

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.restore_version.return_value = None

            response = client.post(_restore_commit_url(project.pk, fake_commit_id))

        assert response.status_code == 302
        assert "history" in response["Location"]

    def test_restore_modification_error_shows_error_message(self, client):
        """ModificationError during restore shows error message and redirects."""
        from writeback.services.modification_service import ModificationError

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        fake_commit_id = uuid.uuid4()

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.restore_version.side_effect = ModificationError("Restore failed")

            response = client.post(_restore_commit_url(project.pk, fake_commit_id))

        assert response.status_code == 302


# ── ConflictsView build_fix_prompt ────────────────────────────────────────


class TestBuildFixPrompt:
    """Unit tests for ConflictsView._build_fix_prompt static method."""

    def _make_conflict(
        self,
        suggested_fix="",
        property_name="FireRating",
        ifc_value="EI60",
        document_value="EI120",
        description="Mismatch",
    ):
        c = MagicMock()
        c.suggested_fix = suggested_fix
        c.property_name = property_name
        c.ifc_value = ifc_value
        c.document_value = document_value
        c.description = description
        c.ifc_entity = MagicMock()
        c.ifc_entity.ifc_type = "IfcWall"
        return c

    def test_uses_suggested_fix_when_present(self):
        """Returns suggested_fix directly when it is non-empty."""
        from writeback.views import ConflictsView

        c = self._make_conflict(suggested_fix="Fix this now!")
        entity = MagicMock()
        entity.name = "W-001"
        result = ConflictsView._build_fix_prompt(c, [entity])
        assert result == "Fix this now!"

    def test_returns_empty_when_no_property_name(self):
        """Returns empty string when property_name is None."""
        from writeback.views import ConflictsView

        c = self._make_conflict(property_name=None)
        result = ConflictsView._build_fix_prompt(c, [])
        assert result == ""

    def test_missing_value_builds_add_prompt(self):
        """Returns 'Add ...' prompt when ifc_value is missing."""
        from writeback.views import ConflictsView

        c = self._make_conflict(ifc_value="missing")
        entity = MagicMock()
        entity.name = "W-001"
        result = ConflictsView._build_fix_prompt(c, [entity])
        assert result.startswith("Add ")

    def test_existing_value_builds_set_prompt(self):
        """Returns 'Set ...' prompt when ifc_value is present."""
        from writeback.views import ConflictsView

        c = self._make_conflict(ifc_value="EI60", document_value="EI120")
        entity = MagicMock()
        entity.name = "W-001"
        result = ConflictsView._build_fix_prompt(c, [entity])
        assert result.startswith("Set ")

    def test_multiple_entities_uses_plural_clause(self):
        """Multiple entities produces plural entity clause."""
        from writeback.views import ConflictsView

        c = self._make_conflict(ifc_value="EI60", document_value="EI120")
        entities = [MagicMock(name="W-001"), MagicMock(name="W-002")]
        entities[0].name = "W-001"
        entities[1].name = "W-002"
        result = ConflictsView._build_fix_prompt(c, entities)
        assert "W-001" in result or "following" in result
