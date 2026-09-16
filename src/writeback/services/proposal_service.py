# writeback/services/proposal_service.py
"""
Persistence for pipeline outcomes.

The pipeline decides *what* changes and produces a
:class:`~writeback.services.pipeline.PipelineOutcome`; this service turns
that outcome into a ``ModificationProposal`` row — the row is the journal —
runs the Guardian (RAV) document check unless the user skipped it, and owns
the other lifecycle transitions (reject, supersede). Every terminal
transition deletes the proposal's scratch copy.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.utils import timezone

from writeback.models import ModificationProposal
from writeback.services.guardian_service import GuardianService

from .emitters import CancellationError, NullEmitter, Phase, PipelineEmitter

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from .pipeline import PipelineOutcome

logger = logging.getLogger(__name__)


def delete_scratch(proposal: ModificationProposal) -> None:
    """Remove the proposal's scratch copy if it still exists. Never raises."""
    if not proposal.scratch_path:
        return
    try:
        Path(proposal.scratch_path).unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover — a locked file must not break the transition
        logger.warning("Could not delete scratch %s: %s", proposal.scratch_path, e)


class ProposalService:
    """Creates, rejects and supersedes ModificationProposal rows."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.user = user

    # ── Create ─────────────────────────────────────────────

    def create_proposal(
        self,
        outcome: PipelineOutcome,
        *,
        user,
        request_text: str,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        skip_guardian: bool = False,
    ) -> ModificationProposal:
        """Persist a pipeline outcome as a PENDING proposal, then run Guardian.

        Args:
            outcome:       Code, targets, diff, scratch path and fingerprint.
            user:          The requesting user (proposal author).
            request_text:  The original natural-language request.
            message_obj:   Optional chat Message to link the proposal to.
            emitter:       Pipeline emitter for the Guardian progress events.
            skip_guardian: The user switched the document check off; recorded.
        """
        emitter = emitter or NullEmitter()

        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=outcome.ifc_file,
            created_by=user,
            request_text=request_text,
            explanation=outcome.explanation,
            explainer_model=outcome.explainer_model,
            code=outcome.code,
            target_global_ids=outcome.targets,
            diff=outcome.diff,
            base_fingerprint=outcome.base_fingerprint,
            scratch_path=outcome.scratch_path,
            affected_count=len(outcome.targets),
            guardian_skipped=skip_guardian,
            # A skipped check is a settled state on the row, not "checking…" forever.
            verification_status=(
                ModificationProposal.VerificationStatus.UNKNOWN
                if skip_guardian
                else ModificationProposal.VerificationStatus.PENDING
            ),
            verification_result="Document check skipped by the user." if skip_guardian else "",
            status=ModificationProposal.Status.PENDING,
        )

        logger.info(
            "Proposal %s: %d targets, %d diff rows, %d flagged, %d attempt(s)",
            proposal.id,
            len(outcome.targets),
            len(outcome.rows),
            outcome.flagged_count,
            outcome.attempts,
        )

        try:
            if skip_guardian:
                emitter.emit(
                    Phase.GUARDIAN, "done", "Document check skipped", {"verdict": "skipped"}
                )
            else:
                self.run_guardian(proposal, emitter)
        except CancellationError:
            # The client is gone before the row was linked to its message: nothing
            # could ever supersede it, so it must not stay pending with its scratch.
            self.reject(proposal, user=user, reason="Cancelled before the document check finished.")
            raise
        return proposal

    # ── Guardian (RAV) ─────────────────────────────────────

    def run_guardian(
        self,
        proposal: ModificationProposal,
        emitter: PipelineEmitter | None = None,
    ) -> None:
        """Run the Guardian document check. Advisory — never blocks.

        Per the project's design rules the Guardian advises but never
        blocks: any failure is logged and surfaced as "unavailable". The one
        exception is :class:`CancellationError`, which means the user
        disconnected and must propagate so the pipeline stops.
        """
        emitter = emitter or NullEmitter()
        emitter.emit(Phase.GUARDIAN, "running", "Checking project documents…")
        try:
            GuardianService(user=self.user).check(proposal)
            emitter.emit(
                Phase.GUARDIAN,
                "done",
                "Document check complete",
                {"verdict": proposal.verification_status},
            )
        except CancellationError:
            raise
        except Exception as e:  # noqa: BLE001 — Guardian must never block a proposal
            logger.warning("Guardian check failed (non-blocking): %s", e)
            emitter.emit(
                Phase.GUARDIAN, "done", "Document check unavailable", {"verdict": "failed"}
            )

    # ── Claim for approval ─────────────────────────────────

    def claim_for_approval(
        self, proposal: ModificationProposal, user, *, acknowledge_flags: bool
    ) -> bool:
        """Move a PENDING row to APPROVED in one guarded UPDATE; False when it was not pending.

        The guard is what makes approval idempotent: two overlapping approve
        requests both read PENDING, but only one UPDATE matches, so only one
        reaches the swap. The flagged-row acknowledgement is stamped in the
        same statement (spec U-2: one POST, one transaction).
        """
        now = timezone.now()
        claimed = ModificationProposal.objects.filter(
            pk=proposal.pk, status=ModificationProposal.Status.PENDING
        ).update(
            status=ModificationProposal.Status.APPROVED,
            reviewed_by=user,
            reviewed_at=now,
            flags_acknowledged_at=now if acknowledge_flags else None,
        )
        if claimed:
            proposal.refresh_from_db(
                fields=["status", "reviewed_by", "reviewed_at", "flags_acknowledged_at"]
            )
        return bool(claimed)

    # ── Reject ─────────────────────────────────────────────

    def reject(
        self,
        proposal: ModificationProposal,
        user=None,
        reason: str = "",
    ) -> bool:
        """Reject a PENDING row in one guarded UPDATE and delete its scratch; False when it was not pending.

        The guard is the same one approval uses: a reject that races an
        approve in another tab finds the row already claimed and changes
        nothing, instead of writing REJECTED over APPLIED from a stale instance.
        """
        rejected = ModificationProposal.objects.filter(
            pk=proposal.pk, status=ModificationProposal.Status.PENDING
        ).update(
            status=ModificationProposal.Status.REJECTED,
            reviewed_by=user,
            reviewed_at=timezone.now(),
            rejection_reason=reason,
        )
        if not rejected:
            logger.info("Proposal %s was no longer pending; reject changed nothing", proposal.id)
            return False
        proposal.refresh_from_db(
            fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"]
        )
        delete_scratch(proposal)
        logger.info("Proposal %s rejected", proposal.id)
        return True

    # ── Supersede ──────────────────────────────────────────

    def supersede_pending(self, session, user) -> list[str]:
        """
        Mark every PENDING proposal in this session as SUPERSEDED.

        Called when the user sends a new modify request before resolving the
        previous one. Captures the abandon as a queryable status (with reviewer
        and timestamp) and deletes each superseded scratch copy.

        Returns:
            List of stringified proposal IDs that were marked SUPERSEDED.
        """
        pending = ModificationProposal.objects.filter(
            message__session=session,
            status=ModificationProposal.Status.PENDING,
            ifc_file__project=self.project,
        )
        rows = list(pending)
        if not rows:
            return []

        for proposal in rows:
            delete_scratch(proposal)
        pending.update(
            status=ModificationProposal.Status.SUPERSEDED,
            reviewed_by=user,
            reviewed_at=timezone.now(),
        )
        ids = [str(p.id) for p in rows]
        logger.info("Superseded %d pending proposal(s) in session %s", len(ids), session.id)
        return ids
