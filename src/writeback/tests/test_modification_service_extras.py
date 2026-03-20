# writeback/tests/test_modification_service_extras.py
"""Additional tests for ModificationService — reject() and restore_version()."""

from unittest.mock import MagicMock, patch

import pytest

from writeback.services.modification_service import ModificationError, ModificationService


# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_git():
    """Patch GitService to avoid filesystem operations."""
    with patch("writeback.services.modification_service.GitService") as MockGit:
        yield MockGit.return_value


@pytest.fixture
def mock_llm():
    """Patch all LLM instantiations to avoid Ollama calls."""
    mock = MagicMock()
    with patch("writeback.services.intent_classifier.get_llm", return_value=mock):
        with patch("writeback.services.tier2_planner.get_llm", return_value=mock):
            with patch("writeback.services.tier3_planner.get_llm", return_value=mock):
                with patch("writeback.services.tier3_reviewer.get_llm", return_value=mock):
                    yield mock


# ── ModificationService.reject() ─────────────────────────────────────────────


@pytest.mark.django_db
class TestRejectProposal:
    """Tests for ModificationService.reject()."""

    def test_reject_sets_status_to_rejected(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() sets proposal status to REJECTED."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="Not approved")

        proposal.refresh_from_db()
        assert proposal.status == "rejected"

    def test_reject_sets_reviewed_by_and_reason(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() stores reviewer, timestamp, and reason."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="Does not meet requirements")

        proposal.refresh_from_db()
        assert proposal.reviewed_by == user
        assert proposal.reviewed_at is not None
        assert proposal.rejection_reason == "Does not meet requirements"

    def test_reject_empty_reason_accepted(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() works with empty reason string."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="")

        proposal.refresh_from_db()
        assert proposal.status == "rejected"
        assert proposal.rejection_reason == ""


# ── ModificationService.restore_version() ────────────────────────────────────


@pytest.mark.django_db
class TestRestoreVersion:
    """Tests for ModificationService.restore_version()."""

    def test_restore_commit_not_found_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Non-existent commit_id raises ModificationError."""
        import uuid

        svc = ModificationService(project, user=user)

        with pytest.raises(ModificationError, match="not found"):
            svc.restore_version(str(uuid.uuid4()), user)

    def test_restore_git_failure_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Git rollback failure raises ModificationError."""
        from writeback.models import GitCommit

        git_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="0000000000000000000000000000000000000001",
            message="Initial commit",
            author=user,
            entities_modified=0,
            diff_data={},
        )

        mock_git.rollback.return_value = False

        svc = ModificationService(project, user=user)

        with pytest.raises(ModificationError, match="Failed to revert"):
            svc.restore_version(str(git_commit.id), user)

    def test_restore_success_creates_audit_commit(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Successful restore creates a ROLLBACK GitCommit record."""
        from writeback.models import GitCommit

        original_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="0000000000000000000000000000000000000001",
            message="Original commit",
            author=user,
            entities_modified=3,
            diff_data={"tier": 1},
        )

        mock_git.rollback.return_value = True
        mock_git.get_parent_hash.return_value = "newhead123456789012345678901234567890"

        with patch("writeback.services.modification_service.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True

            svc = ModificationService(project, user=user)
            new_commit = svc.restore_version(str(original_commit.id), user)

        assert new_commit is not None
        assert new_commit.diff_data.get("operation") == "ROLLBACK"
        assert new_commit.diff_data.get("restored_from_hash") == original_commit.commit_hash

    def test_restore_db_sync_failure_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Failed DB re-parse after restore raises ModificationError."""
        from writeback.models import GitCommit

        commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="parent0000000000000000000000000000000001",
            message="Commit",
            author=user,
            entities_modified=0,
            diff_data={},
        )

        mock_git.rollback.return_value = True
        mock_git.get_parent_hash.return_value = "newhash0000000000000000000000000000001"

        with patch("writeback.services.modification_service.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = False

            svc = ModificationService(project, user=user)

            with pytest.raises(ModificationError, match="parsing failed"):
                svc.restore_version(str(commit.id), user)
