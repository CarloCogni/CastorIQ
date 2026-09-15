# writeback/tests/test_proposal_service.py
"""ProposalService: the row is the journal, Guardian is advisory, scratch files die with the row."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.tests.factories import IFCFileFactory
from writeback.models import ModificationProposal
from writeback.services.emitters import CancellationError, CapturingEmitter
from writeback.services.pipeline import PipelineOutcome
from writeback.services.proposal_service import ProposalService
from writeback.services.verifier import aggregate_rows, flag_rows
from writeback.tests.factories import WALL_IDS, ModificationProposalFactory, sample_diff


def _service(project=None) -> ProposalService:
    return ProposalService(project=project, user=None)


def _outcome(ifc_file, scratch: Path) -> PipelineOutcome:
    diff = sample_diff()
    return PipelineOutcome(
        ifc_file=ifc_file,
        code="def select(model):\n    return []\n\ndef modify(model, targets):\n    pass\n",
        targets=list(WALL_IDS),
        diff=diff,
        rows=flag_rows(aggregate_rows(diff), "Set fire rating to EI120 on all walls"),
        scratch_path=str(scratch),
        base_fingerprint="f" * 64,
        explanation="Sets FireRating to EI120 on three walls.",
        explainer_model="test-model",
        attempts=1,
        grounding=MagicMock(),
    )


# ── run_guardian ──────────────────────────────────────────────────


def test_run_guardian_emits_done_on_success():
    """A successful check emits running then done with the verdict."""
    proposal = MagicMock(verification_status="verified")
    emitter = CapturingEmitter()

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.return_value = None
        _service().run_guardian(proposal, emitter)

    phases = [(e["phase"], e["status"]) for e in emitter.events]
    assert phases == [("guardian", "running"), ("guardian", "done")]
    assert emitter.events[-1]["detail"] == {"verdict": "verified"}


def test_run_guardian_swallows_ordinary_failures():
    """Guardian advises, never blocks — an exception must not propagate."""
    proposal = MagicMock(verification_status="pending")
    emitter = CapturingEmitter()

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.side_effect = RuntimeError("embeddings down")
        _service().run_guardian(proposal, emitter)

    assert emitter.events[-1]["detail"] == {"verdict": "failed"}
    assert "unavailable" in emitter.events[-1]["message"]


def test_run_guardian_propagates_cancellation():
    """A disconnected client must stop the pipeline, not be swallowed."""
    proposal = MagicMock(verification_status="pending")

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.side_effect = CancellationError("client gone")
        with pytest.raises(CancellationError):
            _service().run_guardian(proposal, CapturingEmitter())


# ── create_proposal ───────────────────────────────────────────────


@pytest.mark.django_db
def test_create_proposal_stores_code_targets_diff_fingerprint_and_scratch(tmp_path):
    """The seven V3 columns are written from the outcome; Guardian runs by default."""
    ifc_file = IFCFileFactory()
    scratch = tmp_path / ".model.proposal-abc.ifc"
    scratch.write_bytes(b"x")
    emitter = CapturingEmitter()

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.return_value = None
        proposal = _service(ifc_file.project).create_proposal(
            _outcome(ifc_file, scratch),
            user=ifc_file.project.owner,
            request_text="Set fire rating to EI120 on all walls",
            emitter=emitter,
        )

    assert proposal.status == ModificationProposal.Status.PENDING
    assert proposal.code.startswith("def select")
    assert proposal.target_global_ids == WALL_IDS
    assert proposal.diff["property_changes"][0]["after"] == "EI120"
    assert proposal.base_fingerprint == "f" * 64
    assert proposal.scratch_path == str(scratch)
    assert proposal.explainer_model == "test-model"
    assert proposal.guardian_skipped is False
    assert proposal.affected_count == 3
    assert mock_cls.return_value.check.called
    assert [e["phase"] for e in emitter.events] == ["guardian", "guardian"]


@pytest.mark.django_db
def test_create_proposal_with_skip_guardian_records_the_skip(tmp_path):
    """skip_guardian=True: no Guardian call, guardian_skipped stored, 'skipped' emitted."""
    ifc_file = IFCFileFactory()
    scratch = tmp_path / "s.ifc"
    scratch.write_bytes(b"x")
    emitter = CapturingEmitter()

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        proposal = _service(ifc_file.project).create_proposal(
            _outcome(ifc_file, scratch),
            user=ifc_file.project.owner,
            request_text="r",
            emitter=emitter,
            skip_guardian=True,
        )

    assert proposal.guardian_skipped is True
    assert proposal.verification_status == ModificationProposal.VerificationStatus.UNKNOWN
    assert "skipped" in proposal.verification_result
    assert not mock_cls.return_value.check.called
    assert emitter.events[-1]["detail"] == {"verdict": "skipped"}


# ── claim_for_approval ────────────────────────────────────────────


@pytest.mark.django_db
def test_claim_for_approval_moves_pending_to_approved_once():
    """The first claim wins and stamps the flags; a second claim finds nothing pending."""
    proposal = ModificationProposalFactory()
    user = proposal.created_by
    service = _service(proposal.ifc_file.project)

    assert service.claim_for_approval(proposal, user, acknowledge_flags=True) is True
    assert proposal.status == ModificationProposal.Status.APPROVED
    assert proposal.reviewed_by == user
    assert proposal.flags_acknowledged_at == proposal.reviewed_at

    assert service.claim_for_approval(proposal, user, acknowledge_flags=True) is False
    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.APPROVED


@pytest.mark.django_db
def test_claim_without_flagged_rows_leaves_the_acknowledgement_empty():
    proposal = ModificationProposalFactory()
    _service(proposal.ifc_file.project).claim_for_approval(
        proposal, proposal.created_by, acknowledge_flags=False
    )
    proposal.refresh_from_db()
    assert proposal.reviewed_at is not None and proposal.flags_acknowledged_at is None


def test_run_guardian_is_built_for_the_requesting_user():
    """BYOK overrides, the token budget and the call log follow the user, as on the code call."""
    proposal = MagicMock(verification_status="verified")
    user = MagicMock(name="user")

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        ProposalService(project=None, user=user).run_guardian(proposal, CapturingEmitter())

    mock_cls.assert_called_once_with(user=user)


# ── reject / supersede delete the scratch ─────────────────────────


@pytest.mark.django_db
def test_reject_deletes_the_scratch_copy(tmp_path):
    """After reject the scratch file is gone and the status is rejected."""
    scratch = tmp_path / "s.ifc"
    scratch.write_bytes(b"x")
    proposal = ModificationProposalFactory(scratch_path=str(scratch))

    assert _service(proposal.ifc_file.project).reject(
        proposal, user=proposal.created_by, reason="no"
    )

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.REJECTED
    assert proposal.rejection_reason == "no"
    assert not scratch.exists()


@pytest.mark.django_db
def test_reject_changes_nothing_on_a_row_that_is_no_longer_pending(tmp_path):
    """A reject that lost the race to an approve leaves the applied row and its copy alone."""
    scratch = tmp_path / "s.ifc"
    scratch.write_bytes(b"x")
    proposal = ModificationProposalFactory(
        scratch_path=str(scratch), status=ModificationProposal.Status.APPLIED
    )
    stale = ModificationProposal.objects.get(pk=proposal.pk)
    stale.status = ModificationProposal.Status.PENDING  # what a second tab still holds

    assert not _service(proposal.ifc_file.project).reject(stale, user=proposal.created_by)

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.APPLIED
    assert scratch.exists()


@pytest.mark.django_db
def test_supersede_pending_deletes_each_scratch_copy(tmp_path):
    """Every superseded proposal loses its scratch file in the same call."""
    from chat.models import ChatSession, Message

    proposal = ModificationProposalFactory()
    project = proposal.ifc_file.project
    session = ChatSession.objects.create(
        project=project, user=proposal.created_by, mode=ChatSession.Mode.MODIFY, title="t"
    )
    proposal.message = Message.objects.create(
        session=session, role=Message.Role.ASSISTANT, content="p"
    )
    scratch = tmp_path / "s.ifc"
    scratch.write_bytes(b"x")
    proposal.scratch_path = str(scratch)
    proposal.save()

    ids = _service(project).supersede_pending(session, proposal.created_by)

    assert ids == [str(proposal.id)]
    assert not scratch.exists()
    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.SUPERSEDED


@pytest.mark.django_db
def test_cancel_during_guardian_rejects_the_row_and_deletes_the_scratch(tmp_path):
    """A row nobody could ever supersede must not stay pending; the cancellation still propagates."""
    ifc_file = IFCFileFactory()
    scratch = tmp_path / "s.ifc"
    scratch.write_bytes(b"x")

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.side_effect = CancellationError("client gone")
        with pytest.raises(CancellationError):
            _service(ifc_file.project).create_proposal(
                _outcome(ifc_file, scratch),
                user=ifc_file.project.owner,
                request_text="r",
                emitter=CapturingEmitter(),
            )

    proposal = ModificationProposal.objects.get()
    assert proposal.status == ModificationProposal.Status.REJECTED
    assert "Cancelled" in proposal.rejection_reason
    assert not scratch.exists()
