# writeback/services/modification_service.py
"""
Facade over the writeback services.

Coordinates the full lifecycle:
    classify → filter → validate → preview → execute → commit

The work lives in three focused services:

  * :class:`~writeback.services.proposal_pipeline.ProposalPipeline` — the LLM
    stages, routing and tier dispatch; produces a ``PipelineOutcome``.
  * :class:`~writeback.services.proposal_service.ProposalService` — persists
    outcomes as proposals, runs Guardian, rejects/supersedes.
  * :class:`~writeback.services.execution_service.ExecutionService` — applies
    approved proposals, commits to git, re-syncs the DB.

This module stays as the stable import surface for views, consumers and
tests; it re-exports :class:`ModificationError` from ``.errors`` so existing
``from ...modification_service import ModificationError`` imports resolve to
the same class object every other module raises.
"""

from __future__ import annotations

import logging

from writeback.models import GitCommit, ModificationProposal

from .emitters import NullEmitter, PipelineEmitter
from .errors import ModificationError
from .execution_service import ExecutionService
from .proposal_pipeline import ProposalPipeline
from .proposal_service import ProposalService

logger = logging.getLogger(__name__)

__all__ = ["ModificationError", "ModificationService"]


class ModificationService:
    """
    Orchestrates IFC modifications from intent to commit.

    Two-phase workflow:
        1. propose() — classify, validate, create a pending proposal
        2. execute() — apply the approved proposal, commit to git

    Usage:
        svc = ModificationService(project)
        proposal = svc.propose("Set fire rating to EI120", user=request.user)
        # ... user reviews and approves in UI ...
        svc.execute(proposal)
    """

    def __init__(self, project, user=None):
        self.project = project
        self.user = user
        self.pipeline = ProposalPipeline(project, user=user)
        self.proposals = ProposalService(project, user=user)
        self.execution = ExecutionService(project, user=user)

    # ── Phase 1: Propose ───────────────────────────────────

    def propose(
        self,
        user_message: str,
        user,
        ifc_file=None,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        retry_of=None,
    ) -> ModificationProposal:
        """
        Classify user intent, validate, and create a pending proposal.

        This does NOT modify the IFC file. It creates a proposal for the
        user to review and approve.

        Args:
            user_message: Natural language modification request
            user:         The requesting user
            ifc_file:     Specific IFC file (auto-detected if None)
            message_obj:  Optional chat Message to link
            retry_of:     Optional FailureRecord from a prior attempt. Its
                          stored structured boundary errors are fed back into
                          the stage that failed so the LLM can self-correct.

        Returns:
            ModificationProposal with status=PENDING

        Raises:
            ModificationError on classification/validation failure.
        """
        emitter = emitter or NullEmitter()
        outcome = self.pipeline.run(
            user_message,
            user=user,
            ifc_file=ifc_file,
            emitter=emitter,
            retry_of=retry_of,
        )
        return self.proposals.create_proposal(
            outcome,
            user=user,
            request_text=user_message,
            message_obj=message_obj,
            emitter=emitter,
        )

    # ── Phase 2: Execute ───────────────────────────────────

    def execute(self, proposal: ModificationProposal) -> GitCommit:
        """Execute an approved proposal: write, commit, sync. See ExecutionService."""
        return self.execution.execute(proposal)

    def restore_version(self, commit_id: str, user) -> GitCommit:
        """Restore the IFC file to a historical commit and re-sync the DB."""
        return self.execution.restore_version(commit_id, user)

    # ── Lifecycle ──────────────────────────────────────────

    def reject(
        self,
        proposal: ModificationProposal,
        user=None,
        reason: str = "",
    ) -> None:
        """Mark a proposal as rejected."""
        return self.proposals.reject(proposal, user=user, reason=reason)

    def supersede_pending(self, session, user) -> list[str]:
        """Mark every PENDING proposal in this session as SUPERSEDED."""
        return self.proposals.supersede_pending(session, user)
