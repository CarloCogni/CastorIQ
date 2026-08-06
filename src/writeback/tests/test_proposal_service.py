# writeback/tests/test_proposal_service.py
"""Tests for ProposalService.run_guardian — the collapsed Guardian helper.

The Guardian check used to be three near-identical copy-pasted blocks (one
per tier dispatch). These pin the contract of the single shared version:
advisory for ordinary failures, but a client disconnect must propagate.
"""

from unittest.mock import MagicMock, patch

import pytest

from writeback.services.emitters import CancellationError, CapturingEmitter
from writeback.services.proposal_service import ProposalService


def _service() -> ProposalService:
    return ProposalService(project=None, user=None)


def test_run_guardian_emits_done_on_success():
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
        _service().run_guardian(proposal, emitter)  # must not raise

    assert emitter.events[-1]["detail"] == {"verdict": "failed"}
    assert "unavailable" in emitter.events[-1]["message"]


def test_run_guardian_propagates_cancellation():
    """A disconnected client must stop the pipeline, not be swallowed."""
    proposal = MagicMock(verification_status="pending")

    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        mock_cls.return_value.check.side_effect = CancellationError("client gone")
        with pytest.raises(CancellationError):
            _service().run_guardian(proposal, CapturingEmitter())
