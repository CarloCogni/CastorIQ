# writeback/tests/test_modification_service_execute.py
"""Tests for ModificationService.execute(), reject(), restore_version(), and helper statics."""

from unittest.mock import patch

import pytest

from writeback.models import GitCommit, ModificationProposal
from writeback.services.modification_service import ModificationError, ModificationService
from writeback.tests.factories import ModificationProposalFactory

# ── Shared mocks ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_git():
    """Patches GitService so no filesystem operations are attempted."""
    with patch("writeback.services.execution_service.GitService") as mock_git_cls:
        instance = mock_git_cls.return_value
        instance.ensure_repo.return_value = None
        instance.snapshot.return_value = "abc123sha"
        instance.get_parent_hash.return_value = "abc123sha"
        instance.commit_modification.return_value = "def456sha"
        instance.rollback.return_value = True
        yield instance


# ── execute() tests ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExecuteHappyPath:
    """execute() happy-path tests — Tier 1 proposal."""

    def test_execute_wrong_status_raises_modification_error(
        self, project, ifc_file, user, mock_git
    ):
        """execute() raises ModificationError if proposal is already APPLIED."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            status="applied",
        )
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        with pytest.raises(ModificationError, match="applied"):
            svc.execute(proposal)


@pytest.mark.django_db
class TestReject:
    """Tests for ModificationService.reject()."""

    def test_reject_sets_status_to_rejected(self, project, ifc_file, user, mock_git):
        """reject() sets proposal status to REJECTED."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user, reason="Not approved")

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.REJECTED

    def test_reject_records_reviewer_and_reason(self, project, ifc_file, user, mock_git):
        """reject() stores reviewed_by user and rejection_reason."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user, reason="Wrong entities targeted")

        proposal.refresh_from_db()
        assert proposal.reviewed_by == user
        assert proposal.rejection_reason == "Wrong entities targeted"

    def test_reject_sets_reviewed_at_timestamp(self, project, ifc_file, user, mock_git):
        """reject() populates the reviewed_at timestamp."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user)

        proposal.refresh_from_db()
        assert proposal.reviewed_at is not None


def _fake_apply(journal, **_kwargs):
    """Stand in for JournalExecutor.apply — no IFC file is opened.

    Reports every mutation as applied with the old value the journal
    recorded, which is what the DB sync and the git diff read.
    """
    from ifc_processor.services.journal import AppliedJournal, AppliedMutation

    return AppliedJournal(
        journal=journal,
        applied=tuple(
            AppliedMutation(mutation=m, actual_old_value=m.old_value, stale=False)
            for m in journal.mutations
        ),
    )


def _journal_payload(ifc_file, entity) -> dict:
    """A serialized single-mutation T1 journal targeting one wall."""
    from ifc_processor.services.journal import (
        Mutation,
        MutationJournal,
        MutationOp,
        new_journal_id,
        new_mutation_id,
    )

    journal = MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id=str(ifc_file.id),
        source_tier=1,
        base_fingerprint="",
        captured_at="2026-08-03T12:00:00+00:00",
        mutations=(
            Mutation(
                id=new_mutation_id(),
                op=MutationOp.SET_PROPERTY,
                global_id=entity.global_id,
                entity_name=entity.name,
                ifc_type=entity.ifc_type,
                pset="Pset_WallCommon",
                prop="FireRating",
                old_value="EI60",
                new_value="EI120",
            ),
        ),
    )
    return journal.to_json_dict()


@pytest.mark.django_db
class TestExecuteJournalPath:
    """Journal proposals (changes carries schema_version) execute via JournalExecutor."""

    def test_journal_proposal_executes_and_syncs_db(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """Journal path: APPLIED status, GitCommit row, and DB property sync."""
        entity = wall_entities[0]
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            changes=_journal_payload(ifc_file, entity),
            intent_json={"operation": "SET_PROPERTY"},
            filter_spec={},
        )

        with patch("writeback.services.execution_service.JournalExecutor") as mock_executor_cls:
            mock_executor_cls.return_value.apply.side_effect = _fake_apply
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            git_commit = svc.execute(proposal)

        mock_executor_cls.return_value.apply.assert_called_once()
        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.APPLIED
        assert isinstance(git_commit, GitCommit)
        assert git_commit.diff_data["changes"][0]["new"] == "EI120"

        entity.refresh_from_db()
        assert entity.properties["Pset_WallCommon.FireRating"] == "EI120"

    def test_pre_cutover_proposal_is_refused(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """A proposal without schema_version has no executor left.

        The 0010 migration supersedes these, so reaching this branch means one
        slipped through — refusing beats writing something nothing validated.
        """
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            changes={},  # no schema_version → pre-cutover artifact
            intent_json={"operation": "SET_PROPERTY"},
            filter_spec={"ifc_type": "IfcWall"},
        )

        with patch("writeback.services.execution_service.JournalExecutor") as mock_executor_cls:
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            with pytest.raises(ModificationError, match="predates the mutation-journal"):
                svc.execute(proposal)

        mock_executor_cls.return_value.apply.assert_not_called()
        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.FAILED

    def test_failed_journal_execute_marks_proposal_failed(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """A JournalExecutionError rolls back and marks the proposal FAILED."""
        from ifc_processor.services.journal_executor import JournalExecutionError

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            changes=_journal_payload(ifc_file, wall_entities[0]),
            intent_json={"operation": "SET_PROPERTY"},
            filter_spec={},
        )

        with patch("writeback.services.execution_service.JournalExecutor") as mock_executor_cls:
            mock_executor_cls.return_value.apply.side_effect = JournalExecutionError("boom")
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            with pytest.raises(ModificationError, match="boom"):
                svc.execute(proposal)

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.FAILED
        mock_git.rollback.assert_called_once()
