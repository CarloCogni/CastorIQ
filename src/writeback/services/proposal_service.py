# writeback/services/proposal_service.py
"""
Persistence for pipeline outcomes.

The pipeline decides *what* should change and produces a
:class:`~writeback.services.proposal_pipeline.PipelineOutcome`; this service
turns that outcome into a ``ModificationProposal`` row, runs the Guardian
(RAV) document check, and owns the other proposal lifecycle transitions
(reject, supersede).

The Guardian check lives here once — it used to be three near-identical
copy-pasted try/except blocks, one per tier dispatch.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.utils import timezone

from writeback.models import ModificationProposal
from writeback.services.guardian_service import GuardianService

from .emitters import CancellationError, NullEmitter, PipelineEmitter

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from .proposal_pipeline import PipelineOutcome

logger = logging.getLogger(__name__)


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
    ) -> ModificationProposal:
        """Persist a pipeline outcome as a PENDING proposal, then run Guardian.

        Args:
            outcome:      What the pipeline decided (journal or legacy payload).
            user:         The requesting user (proposal author).
            request_text: The original natural-language request.
            message_obj:  Optional chat Message to link the proposal to.
            emitter:      Pipeline emitter for the Guardian progress events.
        """
        emitter = emitter or NullEmitter()

        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=outcome.ifc_file,
            created_by=user,
            request_text=request_text,
            explanation=outcome.explanation,
            changes=outcome.changes,
            diff_preview=json.dumps(outcome.diff_preview),
            affected_count=outcome.affected_count,
            status=ModificationProposal.Status.PENDING,
            tier=outcome.tier,
            operation=outcome.operation,
            intent_json=outcome.intent_json,
            filter_spec=outcome.filter_spec,
            confidence=outcome.confidence,
        )

        logger.info(
            "Tier %d proposal %s: %s on %d entities (journal=%s)",
            outcome.tier,
            proposal.id,
            outcome.operation,
            outcome.affected_count,
            outcome.is_journal,
        )

        self.run_guardian(proposal, emitter)
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
        emitter.emit("guardian", "running", "Checking project documents…")
        try:
            GuardianService().check(proposal)
            emitter.emit(
                "guardian",
                "done",
                "Document check complete",
                {"verdict": proposal.verification_status},
            )
        except CancellationError:
            raise
        except Exception as e:  # noqa: BLE001 — Guardian must never block a proposal
            logger.warning("Guardian check failed (non-blocking): %s", e)
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

    # ── Reject ─────────────────────────────────────────────

    def reject(
        self,
        proposal: ModificationProposal,
        user=None,
        reason: str = "",
    ) -> None:
        """Mark a proposal as rejected."""
        proposal.status = ModificationProposal.Status.REJECTED
        proposal.reviewed_by = user
        proposal.reviewed_at = timezone.now()
        proposal.rejection_reason = reason
        proposal.save()
        logger.info(f"Proposal {proposal.id} rejected")

    # ── Supersede ──────────────────────────────────────────

    def supersede_pending(self, session, user) -> list[str]:
        """
        Mark every PENDING proposal in this session as SUPERSEDED.

        Called when the user sends a new modify request before resolving the
        previous one. Captures the abandon as a queryable status (with reviewer
        and timestamp) so it remains available later as soft-negative training
        data instead of vanishing as an orphaned PENDING row.

        Args:
            session: ChatSession whose prior pending proposals should be superseded.
            user:    The user whose new request triggered the supersede.

        Returns:
            List of stringified proposal IDs that were marked SUPERSEDED.
        """
        pending = ModificationProposal.objects.filter(
            message__session=session,
            status=ModificationProposal.Status.PENDING,
            ifc_file__project=self.project,
        )
        ids = [str(p.id) for p in pending]
        if not ids:
            return []

        pending.update(
            status=ModificationProposal.Status.SUPERSEDED,
            reviewed_by=user,
            reviewed_at=timezone.now(),
        )
        logger.info("Superseded %d pending proposal(s) in session %s", len(ids), session.id)
        return ids
