# writeback/services/modification_service.py
"""
Facade over the writeback services.

Coordinates the full lifecycle:
    ground → generate → run → verify → (human) → approve

The work lives in three focused services:

  * :class:`~writeback.services.pipeline.ModifyPipeline` — the model call,
    the sandbox run and the deterministic checks; produces a ``PipelineOutcome``.
  * :class:`~writeback.services.proposal_service.ProposalService` — persists
    outcomes as proposals, runs Guardian, rejects/supersedes.
  * :class:`~writeback.services.execution_service.ExecutionService` — swaps
    the approved scratch copy in, commits to git, refreshes the index.

This module stays as the stable import surface for views, consumers and
tests; it re-exports :class:`ModificationError` from ``.errors`` so existing
``from ...modification_service import ModificationError`` imports resolve to
the same class object every other module raises.
"""

from __future__ import annotations

import logging

from writeback.models import GitCommit, ModificationProposal

from .emitters import NullEmitter, PipelineEmitter
from .errors import ModificationError, NoChangeError
from .execution_service import ExecutionService
from .pipeline import ModifyPipeline
from .proposal_service import ProposalService

logger = logging.getLogger(__name__)

__all__ = ["ModificationError", "ModificationService", "NoChangeError"]


class ModificationService:
    """
    Orchestrates IFC modifications from request to commit.

    Two-phase workflow:
        1. propose() — generate, run, verify, create a pending proposal
        2. execute() — swap the approved copy in, commit to git

    Usage:
        svc = ModificationService(project)
        proposal = svc.propose("Set fire rating to EI120", user=request.user)
        # ... user reviews and approves in UI ...
        svc.execute(proposal)
    """

    def __init__(self, project, user=None):
        self.project = project
        self.user = user
        self.pipeline = ModifyPipeline(project, user=user)
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
        skip_guardian: bool = False,
    ) -> ModificationProposal:
        """
        Generate and verify the change on a scratch copy, then create a pending proposal.

        This does NOT modify the original IFC file.

        Args:
            user_message:  Natural language modification request
            user:          The requesting user
            ifc_file:      Specific IFC file (newest processed file if None)
            message_obj:   Optional chat Message to link
            emitter:       Progress emitter
            skip_guardian: Skip the document check for this request (recorded)

        Raises:
            NoChangeError:     the file is already so; nothing to propose.
            ModificationError: no processed file or three failed attempts.
        """
        emitter = emitter or NullEmitter()
        outcome = self.pipeline.run(user_message, ifc_file=ifc_file, emitter=emitter)
        return self.proposals.create_proposal(
            outcome,
            user=user,
            request_text=user_message,
            message_obj=message_obj,
            emitter=emitter,
            skip_guardian=skip_guardian,
        )

    def propose_in_session(
        self,
        session,
        user_message: str,
        user,
        *,
        conflict_ids: str = "",
        skip_guardian: bool = False,
        emitter: PipelineEmitter | None = None,
    ) -> tuple[ModificationProposal, list[str]]:
        """Run one Modify request inside a chat session, on either transport.

        Records the user's message, supersedes the session's pending proposals,
        runs :meth:`propose` and, on success, records the assistant's message
        (the explanation), links the proposal to it and titles a new session.
        A :class:`NoChangeError` or :class:`ModificationError` is recorded as
        the assistant's answer and re-raised so the transport can render it;
        a cancellation and the typed provider errors pass through untouched.

        Returns the proposal and the ids of the proposals it superseded.
        """
        from chat.models import Message

        from .explainer import UNAVAILABLE

        Message.objects.create(session=session, role=Message.Role.USER, content=user_message)
        superseded_ids = self.supersede_pending(session, user)
        try:
            proposal = self.propose(
                user_message, user=user, emitter=emitter, skip_guardian=skip_guardian
            )
        except NoChangeError as e:
            Message.objects.create(session=session, role=Message.Role.ASSISTANT, content=f"ℹ️ {e}")
            raise
        except ModificationError as e:
            Message.objects.create(session=session, role=Message.Role.ASSISTANT, content=f"⚠️ {e}")
            raise

        assistant = Message.objects.create(
            session=session,
            role=Message.Role.ASSISTANT,
            content=proposal.explanation or UNAVAILABLE,
        )
        proposal.message = assistant
        fields = ["message"]
        linked = [i.strip() for i in (conflict_ids or "").split(",") if i.strip()]
        if linked:
            proposal.linked_conflict_ids = linked
            fields.append("linked_conflict_ids")
        proposal.save(update_fields=fields)

        if session.title == "New Modification":
            session.title = user_message[:50]
            session.save(update_fields=["title"])
        return proposal, superseded_ids

    # ── Phase 2: Execute ───────────────────────────────────

    def claim_for_approval(
        self, proposal: ModificationProposal, user, *, acknowledge_flags: bool
    ) -> bool:
        """Atomically move a pending proposal to approved; False when it is no longer pending."""
        return self.proposals.claim_for_approval(
            proposal, user, acknowledge_flags=acknowledge_flags
        )

    def execute(self, proposal: ModificationProposal) -> GitCommit:
        """Apply an approved proposal: swap, commit, refresh. See ExecutionService."""
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
    ) -> bool:
        """Reject a pending proposal; False when it was no longer pending."""
        return self.proposals.reject(proposal, user=user, reason=reason)

    def supersede_pending(self, session, user) -> list[str]:
        """Mark every PENDING proposal in this session as SUPERSEDED."""
        return self.proposals.supersede_pending(session, user)
