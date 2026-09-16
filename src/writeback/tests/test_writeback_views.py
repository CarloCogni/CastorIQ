# writeback/tests/test_writeback_views.py
"""Tests for writeback views — all LLM/service calls are mocked."""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from chat.models import Message
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

    def test_propose_success_returns_the_card_with_html(self, client):
        """Successful propose returns status=proposed with the serialised card and its HTML."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user)
        _login(client, user)

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.propose_in_session.return_value = (proposal, [])

            response = client.post(
                _modify_url(project.pk),
                {"action": "propose", "message": "Set fire rating to EI120", "skip_guardian": "1"},
            )

        data = json.loads(response.content)
        assert data["status"] == "proposed"
        assert data["proposal"]["id"] == str(proposal.id)
        assert data["proposal"]["rows"][0]["label"] == "Pset_WallCommon.FireRating"
        assert f'id="proposal-card-{proposal.id}"' in data["proposal"]["html"]
        assert instance.propose_in_session.call_args.kwargs["skip_guardian"] is True

    def test_propose_no_change_returns_no_change_status(self, client):
        """An 'already so' outcome is a no_change answer, not an error and not a proposal."""
        from writeback.services.modification_service import NoChangeError

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        with patch("writeback.views.ModificationService") as MockSvc:
            instance = MockSvc.return_value
            instance.propose_in_session.side_effect = NoChangeError(
                "Already so: the 5 selected entities already have the requested values."
            )

            response = client.post(_modify_url(project.pk), {"action": "propose", "message": "x"})

        data = json.loads(response.content)
        assert data["status"] == "no_change"
        assert "Already so" in data["message"]

    def test_acknowledge_review_action_no_longer_exists(self, client):
        """The V2 two-request acknowledge dance is gone: the action is unknown."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(
            _modify_url(project.pk), {"action": "acknowledge_review", "proposal_id": "x"}
        )
        assert response.status_code == 400


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
        mock_commit.entities_modified = 3

        from writeback.services.modification_service import ModificationService

        with patch.object(ModificationService, "execute", return_value=mock_commit):
            response = client.post(
                _modify_url(project.pk),
                {"action": "approve", "proposal_id": str(proposal.pk)},
            )

        data = json.loads(response.content)
        assert data["status"] == "applied"
        assert "abc123de" in data["commit_hash"]
        assert data["entities_modified"] == 3
        proposal.refresh_from_db()
        assert proposal.reviewed_by == user
        assert proposal.flags_acknowledged_at is None  # nothing was flagged

    def test_approve_claims_the_row_once_so_a_second_post_is_refused(self, client):
        """The claim is one guarded UPDATE: a double-click's second POST gets 409, not a second run."""
        from writeback.services.modification_service import ModificationService

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user)
        _login(client, user)

        def claim_then_stay_approved(*args, **kwargs):
            # Simulate the winner still executing: the row is APPROVED, not yet APPLIED.
            return MagicMock(commit_hash="abc123def456abc1", entities_modified=1)

        with patch.object(ModificationService, "execute", side_effect=claim_then_stay_approved):
            first = client.post(
                _modify_url(project.pk), {"action": "approve", "proposal_id": str(proposal.pk)}
            )
        proposal.refresh_from_db()
        assert first.status_code == 200 and proposal.status == "approved"

        with patch.object(ModificationService, "execute") as execute:
            second = client.post(
                _modify_url(project.pk), {"action": "approve", "proposal_id": str(proposal.pk)}
            )

        assert second.status_code == 409
        assert "no longer pending" in json.loads(second.content)["message"]
        assert not execute.called

    def test_approve_with_flagged_rows_untouched_is_refused_with_422(self, client):
        """A flagged row that was not ticked refuses the approval; nothing executes."""
        from writeback.tests.factories import sample_diff

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file, created_by=user, diff=sample_diff(after="EI999")
        )
        _login(client, user)
        from writeback.services.modification_service import ModificationService

        with patch.object(ModificationService, "execute") as execute:
            response = client.post(
                _modify_url(project.pk),
                {"action": "approve", "proposal_id": str(proposal.pk)},
            )

        assert response.status_code == 422
        data = json.loads(response.content)
        assert data["needs_flag_ack"] is True
        assert data["missing"] == sorted(proposal.flagged_keys)
        assert not execute.called
        proposal.refresh_from_db()
        assert proposal.flags_acknowledged_at is None
        assert proposal.status == "pending"  # not claimed

    def test_approve_with_every_flagged_row_ticked_executes_and_stamps(self, client):
        """The ticked keys travel in the approve POST; equal sets execute and stamp the time."""
        from writeback.tests.factories import sample_diff

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file, created_by=user, diff=sample_diff(after="EI999")
        )
        _login(client, user)
        keys = ",".join(sorted(proposal.flagged_keys))

        mock_commit = MagicMock()
        mock_commit.commit_hash = "abc123def456abc1"
        mock_commit.entities_modified = 3
        from writeback.services.modification_service import ModificationService

        with patch.object(ModificationService, "execute", return_value=mock_commit):
            response = client.post(
                _modify_url(project.pk),
                {"action": "approve", "proposal_id": str(proposal.pk), "acknowledged_keys": keys},
            )

        data = json.loads(response.content)
        assert data["status"] == "applied"
        proposal.refresh_from_db()
        assert proposal.flags_acknowledged_at is not None
        assert proposal.reviewed_at == proposal.flags_acknowledged_at  # one statement stamps both
        assert proposal.reviewed_by == user

    def test_approve_with_extra_keys_is_refused(self, client):
        """The sets must be equal: a key the server did not compute is refused too."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user)
        _login(client, user)

        from writeback.services.modification_service import ModificationService

        with patch.object(ModificationService, "execute"):
            response = client.post(
                _modify_url(project.pk),
                {
                    "action": "approve",
                    "proposal_id": str(proposal.pk),
                    "acknowledged_keys": "bogus",
                },
            )

        assert response.status_code == 422

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
            instance.reject.return_value = True

            response = client.post(
                _modify_url(project.pk),
                {"action": "reject", "proposal_id": str(proposal.pk), "reason": "Not needed"},
            )

        data = json.loads(response.content)
        assert data["status"] == "rejected"

    def test_reject_that_lost_the_race_to_an_approve_returns_409(self, client):
        """The row read as pending but the guarded UPDATE matched nothing: nothing changed, 409."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        _login(client, user)

        with patch("writeback.views.ModificationService") as MockSvc:
            MockSvc.return_value.reject.return_value = False
            response = client.post(
                _modify_url(project.pk), {"action": "reject", "proposal_id": str(proposal.pk)}
            )

        assert response.status_code == 409
        assert json.loads(response.content)["status"] == "error"
        assert not Message.objects.filter(content__contains="rejected").exists()


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
        IFCFileFactory(project=project)
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
        from environments.services import ProjectAccessService

        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        collaborator = UserFactory()
        ProjectAccessService.add_member(project=project, user=collaborator, permission="editor")
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


# ── Permissions ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestModifyViewPermissions:
    """POST needs EDITOR or OWNER, the same gate as the WebSocket consumer; viewers may read."""

    def test_viewer_can_read_but_not_propose_approve_or_reject(self, client):
        from environments.tests.factories import ProjectMembershipFactory

        owner = UserFactory()
        viewer = UserFactory()
        project = ProjectFactory(owner=owner)
        ProjectMembershipFactory(project=project, user=viewer, permission="viewer")
        _login(client, viewer)

        assert client.get(_modify_url(project.pk)).status_code == 302

        for action in ("propose", "approve", "reject"):
            response = client.post(
                _modify_url(project.pk),
                {"action": action, "message": "Set fire rating to EI120", "proposal_id": "x"},
            )
            assert response.status_code == 403, action
            assert json.loads(response.content)["status"] == "error"


@pytest.mark.django_db
class TestModifyPageRendering:
    """The persisted chat renders each proposal's explanation once, on the card, with the request."""

    def test_a_message_with_a_card_prints_the_explanation_once(self, client):
        from chat.models import ChatSession, Message

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        session = ChatSession.objects.create(
            project=project, user=user, mode=ChatSession.Mode.MODIFY, title="t"
        )
        Message.objects.create(session=session, role=Message.Role.USER, content="Set it to EI120")
        explanation = "Sets FireRating to EI120 on three walls, uniquely-worded."
        assistant = Message.objects.create(
            session=session, role=Message.Role.ASSISTANT, content=explanation
        )
        ModificationProposalFactory(
            ifc_file=ifc_file, created_by=user, message=assistant, explanation=explanation
        )
        _login(client, user)

        html = client.get(_modify_session_url(project.pk, session.pk)).content.decode()

        assert html.count(explanation) == 1
        assert "You asked:" in html


@pytest.mark.django_db
class TestHistoryDiffPanel:
    """A V3 commit stores the aggregated rows; the History tab renders them."""

    def test_history_renders_the_stored_rows(self, client):
        from writeback.models import GitCommit

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="a" * 40,
            message="Set fire rating",
            author=user,
            entities_modified=5,
            diff_data={
                "affected_entities": 5,
                "target_global_ids": ["W1"],
                "modified_global_ids": ["W1"],
                "added_global_ids": [],
                "removed_global_ids": [],
                "rows": [
                    {
                        "key": "k1",
                        "kind": "property",
                        "pset": "Pset_WallCommon",
                        "prop": "FireRating",
                        "before": None,
                        "after": "EI60",
                        "count": 5,
                        "global_ids": ["W1"],
                        "flagged": True,
                        "label": "Pset_WallCommon.FireRating",
                    }
                ],
            },
        )
        _login(client, user)

        html = client.get(_history_url(project.pk)).content.decode()

        assert "Pset_WallCommon.FireRating" in html
        assert "× 5" in html
        assert "EI60" in html
        assert 'id="diff-panel-aaaaaaaa"' in html
