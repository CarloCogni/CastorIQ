# writeback/services/execution_service.py
"""
Applies approved proposals to the IFC file and records the result.

Approval is a swap, not a second run (spec A-3): the fingerprint of the
original is compared with the one taken at proposal time; on a match the
reviewed scratch copy replaces the original atomically, the change is
committed to git with the generated code in the body, and the index is
refreshed for exactly the GlobalIds the stored diff names. The approved diff
and the applied diff are the same bytes by construction.

Approvals on one file are serialised: the file row and the proposal row are
locked from the fingerprint check through the commit, so two overlapping
approve requests (a double-click, or two proposals on the same file) cannot
both reach the swap. The loser sees the winner's outcome instead of racing it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from ifc_processor.models import IFCFile
from ifc_processor.services.fingerprint import compute_fingerprint
from ifc_processor.services.index_refresh import refresh_entities
from ifc_processor.services.processor import IFCProcessingService
from writeback.models import GitCommit, ModificationProposal

from .errors import ModificationError
from .git_service import GitService
from .proposal_service import delete_scratch
from .verifier import aggregate_rows, population_ids

logger = logging.getLogger(__name__)

STALE_MESSAGE = (
    "The IFC file changed since this proposal was made: file changed, please re-propose."
)


@dataclass(frozen=True)
class _Refusal:
    """Why an approval cannot proceed, decided under the file lock.

    ``mark_failed`` is False when the refusal is not the row's own fault (a
    wrong status); ``drop_scratch`` deletes the reviewed copy when it can
    never be applied (the file moved underneath it).
    """

    message: str
    mark_failed: bool = True
    drop_scratch: bool = False


class ExecutionService:
    """Executes approved proposals and restores historical versions."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.user = user
        self.git = GitService(project)

    # ── Execute ────────────────────────────────────────────

    def execute(self, proposal: ModificationProposal) -> GitCommit:
        """
        Apply an approved proposal: lock → fingerprint check → swap → commit → index.

        The status is read from the locked row, never from the instance the
        caller holds, so a proposal another request already applied, failed or
        rejected is refused here even if the caller's copy still says pending.

        Every failure after the view's claim, including an unreadable original
        or an unusable git repository, ends as FAILED with the scratch deleted,
        so the row never stays claimed with no way to approve or reject it.

        Raises:
            ModificationError: wrong status, incomplete row, stale file, or a
            failed swap/commit (rolled back through git where possible).
        """
        refusal = failure = git_commit = None
        parent_hash = ""
        with transaction.atomic():
            IFCFile.objects.select_for_update().get(pk=proposal.ifc_file_id)
            proposal = (
                ModificationProposal.objects.select_for_update()
                .select_related("ifc_file", "created_by")
                .get(pk=proposal.pk)
            )
            try:
                refusal = self._refusal(proposal)
                if refusal is None:
                    git_commit, failure, parent_hash = self._apply(proposal)
            except Exception as e:  # noqa: BLE001 — a missing file or an unusable repo must not strand the claimed row
                failure = e

        # The failure-path writes run outside the lock: a raise inside the
        # atomic block would roll the FAILED status back with it.
        if refusal is not None:
            self._refuse(proposal, refusal)
        if failure is not None:
            self._fail(proposal, failure, parent_hash)
            raise ModificationError(
                f"Execution failed: {failure}",
                failure_record_id=self._failure_record(proposal, failure),
            ) from failure

        diff_data = git_commit.diff_data
        touched = (
            diff_data["modified_global_ids"]
            + diff_data["added_global_ids"]
            + diff_data["removed_global_ids"]
        )
        try:
            refresh_entities(proposal.ifc_file, touched)
        except Exception as e:  # noqa: BLE001 — the file is applied and committed; the index can be reprocessed
            logger.exception("Index refresh failed after applying proposal %s: %s", proposal.id, e)

        logger.info(
            "Proposal %s applied → commit %s (%d touched)",
            proposal.id,
            git_commit.commit_hash[:8],
            len(touched),
        )
        return git_commit

    # ── Restore / time machine ─────────────────────────────

    def restore_version(self, commit_id: str, user) -> GitCommit:
        """
        Restore the IFC file to a specific historical commit state.

        Logic:
        1. Use Git to revert the file to the target hash (creates a new 'Revert' commit).
        2. Create a Django GitCommit record for this new state.
        3. CRITICAL: Re-run the full IFC parsing pipeline to sync the DB with the file.
        """
        try:
            target_commit = GitCommit.objects.get(id=commit_id, ifc_file__project=self.project)
        except GitCommit.DoesNotExist:
            raise ModificationError("Commit not found.")

        ifc_file = target_commit.ifc_file

        success = self.git.rollback(ifc_file, target_commit.commit_hash)
        if not success:
            raise ModificationError("Failed to revert file in git repository.")

        new_head_hash = self.git.get_parent_hash()

        new_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash=new_head_hash,
            parent_hash=target_commit.commit_hash,
            message=(
                f"Restored version from "
                f"{target_commit.created_at.strftime('%Y-%m-%d %H:%M')} - "
                f"{target_commit.commit_hash[:8]}"
            ),
            author=user,
            entities_modified=0,
            diff_data={
                "operation": "ROLLBACK",
                "restored_from_hash": target_commit.commit_hash,
                "restored_from_date": str(target_commit.created_at),
            },
            rolled_back=True,
        )

        logger.info(f"Re-parsing IFC file {ifc_file.name} after restore...")
        processor = IFCProcessingService(ifc_file)
        if not processor.run_pipeline():
            logger.error("Restore succeeded in Git but DB sync failed.")
            ifc_file.status = "failed"
            ifc_file.error_message = "File restored, but database sync failed. Please re-process."
            ifc_file.save()
            raise ModificationError("File restored, but database parsing failed.")

        return new_commit

    # ── Internals ──────────────────────────────────────────

    @staticmethod
    def _refusal(proposal: ModificationProposal) -> _Refusal | None:
        """None when the locked row can be applied, else why not."""
        if proposal.status not in (
            ModificationProposal.Status.PENDING,
            ModificationProposal.Status.APPROVED,
        ):
            return _Refusal(
                f"Proposal {proposal.id} is '{proposal.status}', expected 'pending' or 'approved'.",
                mark_failed=False,
            )
        complete = (
            proposal.code
            and proposal.target_global_ids
            and isinstance(proposal.diff, dict)
            and proposal.base_fingerprint
            and proposal.scratch_path
        )
        if not complete:
            return _Refusal(
                f"Proposal {proposal.id} carries no reviewed change and cannot be applied. "
                "Please make the request again."
            )
        if not Path(proposal.scratch_path).exists():
            return _Refusal(
                "The reviewed copy for this proposal no longer exists; please re-propose."
            )
        if compute_fingerprint(proposal.ifc_file.file.path) != proposal.base_fingerprint:
            return _Refusal(STALE_MESSAGE, drop_scratch=True)
        return None

    def _refuse(self, proposal: ModificationProposal, refusal: _Refusal) -> None:
        if refusal.mark_failed:
            self._mark_failed(proposal, refusal.message)
        if refusal.drop_scratch:
            delete_scratch(proposal)
        raise ModificationError(refusal.message)

    def _apply(
        self, proposal: ModificationProposal
    ) -> tuple[GitCommit | None, Exception | None, str]:
        """Swap, commit and record; returns ``(commit, failure, parent_hash)``.

        Runs under the file lock. A failure is returned, not raised, so the
        caller can leave the transaction before writing the FAILED status.
        """
        ifc_file = proposal.ifc_file
        self.git.ensure_repo()
        parent_hash = self.git.snapshot(ifc_file) or self.git.get_parent_hash()
        try:
            os.replace(proposal.scratch_path, ifc_file.file.path)
            diff_data = self._diff_data(proposal)
            commit_hash = self.git.commit_modification(
                ifc_file,
                subject=proposal.explanation or proposal.request_text,
                body=self._commit_body(proposal),
                diff_data=diff_data,
                author_name=proposal.created_by.username,
            )
            # A savepoint: the commit row, the status and the file hash land
            # together or not at all.
            with transaction.atomic():
                git_commit = GitCommit.objects.create(
                    ifc_file=ifc_file,
                    commit_hash=commit_hash,
                    parent_hash=parent_hash,
                    message=proposal.explanation or proposal.request_text,
                    author=proposal.created_by,
                    entities_modified=len(diff_data["modified_global_ids"]),
                    entities_added=len(diff_data["added_global_ids"]),
                    entities_removed=len(diff_data["removed_global_ids"]),
                    diff_data=diff_data,
                )
                proposal.status = ModificationProposal.Status.APPLIED
                proposal.applied_at = timezone.now()
                proposal.git_commit = git_commit
                proposal.save()
                ifc_file.file_hash = compute_fingerprint(ifc_file.file.path)
                ifc_file.save(update_fields=["file_hash"])
        except Exception as e:  # noqa: BLE001 — every failure is recorded and rolled back by the caller
            return None, e, parent_hash
        return git_commit, None, parent_hash

    @staticmethod
    def _mark_failed(proposal: ModificationProposal, message: str) -> None:
        proposal.status = ModificationProposal.Status.FAILED
        proposal.error_message = message
        proposal.save(update_fields=["status", "error_message", "updated_at"])

    def _fail(self, proposal: ModificationProposal, error: Exception, parent_hash: str) -> None:
        self._mark_failed(proposal, str(error))
        delete_scratch(proposal)
        if parent_hash:
            self.git.rollback(proposal.ifc_file, parent_hash)
            logger.warning("Auto-rolled back after failure: %s", error)

    def _failure_record(self, proposal: ModificationProposal, error: Exception) -> str | None:
        from metacastor.services.failure_classifier import create_failure_record

        record = create_failure_record(
            error,
            phase="EXECUTION",
            project=self.project,
            query_text=proposal.request_text,
            proposal=proposal,
        )
        return str(record.id) if record else None

    @staticmethod
    def _diff_data(proposal: ModificationProposal) -> dict:
        """The commit's semantic diff: objects only, never psets or relationships.

        ``added_global_ids`` / ``removed_global_ids`` are the IfcObjects that
        appeared or vanished (what a user calls "an entity"); the property
        sets and relationship entities that come and go with a change are
        visible through the rows, and must never reach the index refresh,
        which would otherwise upsert them as entities. Each row carries its
        card label so the History tab renders it without recomputing.
        """
        diff = proposal.diff or {}
        rows = aggregate_rows(diff)
        modified = sorted(
            {gid for row in rows if row.kind in ("property", "attribute") for gid in row.global_ids}
        )
        return {
            "affected_entities": len(proposal.target_global_ids),
            "target_global_ids": list(proposal.target_global_ids),
            "modified_global_ids": modified,
            "added_global_ids": population_ids(diff, "added"),
            "removed_global_ids": population_ids(diff, "removed"),
            "rows": [{**row.as_dict(), "label": row.label} for row in rows],
        }

    @staticmethod
    def _commit_body(proposal: ModificationProposal) -> str:
        return (
            f"Request: {proposal.request_text.strip()}\n"
            f"Targets: {len(proposal.target_global_ids)}\n\n"
            f"{proposal.code.rstrip()}"
        )
