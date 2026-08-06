# writeback/services/execution_service.py
"""
Applies approved proposals to the IFC file and records the result.

Owns everything after human approval: the git safety snapshot, the write
itself, the git commit, the proposal status transition, and the DB re-sync
that keeps the queryable index aligned with the file.

Every tier writes through the MutationJournal, so there is one execution
path. Proposals are still checked for the ``schema_version`` key their
``changes`` payload carries — a proposal without one predates the cutover
and is refused rather than silently mis-executed.
"""

from __future__ import annotations

import logging

import ifcopenshell
from django.db import transaction
from django.utils import timezone

from ifc_processor.models import IFCEntity, IFCSpatialElement
from ifc_processor.services.ifc_writer import EntityChange, IFCWriteError
from ifc_processor.services.journal import (
    AppliedJournal,
    MutationJournal,
    MutationOp,
    applied_to_entity_changes,
)
from ifc_processor.services.journal_executor import JournalExecutor
from ifc_processor.services.processor import IFCProcessingService
from writeback.models import GitCommit, ModificationProposal

from .errors import ModificationError
from .git_service import GitService

logger = logging.getLogger(__name__)

#: IFC classes that get an IFCSpatialElement node alongside their entity row.
_SPATIAL_TYPE_MAP = {
    "IfcSite": "site",
    "IfcBuilding": "building",
    "IfcBuildingStorey": "building_storey",
    "IfcSpace": "space",
    "IfcFacility": "facility",
    "IfcFacilityPart": "facility_part",
}

#: Classes with no GlobalId — they never enter the IFCEntity index.
_NON_ROOTED_CLASSES = frozenset({"IfcMaterial", "IfcClassification"})


class ExecutionService:
    """Executes approved proposals and restores historical versions."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.user = user
        self.git = GitService(project)

    # ── Execute ────────────────────────────────────────────

    def execute(self, proposal: ModificationProposal) -> GitCommit:
        """
        Execute an approved modification proposal.

        Steps:
            1. Safety snapshot (git)
            2. Write changes to IFC file
            3. Commit to git
            4. Update proposal status
            5. Update entity properties in DB

        Returns:
            GitCommit record.

        Raises:
            ModificationError if execution fails (auto-rollback attempted).
        """
        if proposal.status not in (
            ModificationProposal.Status.PENDING,
            ModificationProposal.Status.APPROVED,
        ):
            raise ModificationError(
                f"Proposal {proposal.id} is '{proposal.status}', expected 'pending' or 'approved'."
            )

        ifc_file = proposal.ifc_file

        # Refuse pre-journal proposals BEFORE snapshotting: nothing has been
        # written, so the failure branch below (rollback + commit) must not run.
        if not self._is_journal_proposal(proposal):
            message = (
                f"Proposal {proposal.id} predates the mutation-journal pipeline and "
                f"can no longer be executed. Please make the request again."
            )
            proposal.status = ModificationProposal.Status.FAILED
            proposal.error_message = message
            proposal.save()
            raise ModificationError(message)

        # 1. Safety snapshot
        self.git.ensure_repo()
        parent_hash = self.git.snapshot(ifc_file) or self.git.get_parent_hash()

        # 2. Execute the write operation — every tier writes through the journal.
        try:
            changes, applied = self._execute_journal(proposal)
        except (IFCWriteError, Exception) as e:
            proposal.status = ModificationProposal.Status.FAILED
            proposal.error_message = str(e)
            proposal.save()

            if parent_hash:
                self.git.rollback(ifc_file, parent_hash)
                logger.warning(f"Auto-rolled back after failure: {e}")

            from metacastor.services.failure_classifier import create_failure_record

            failure_rec = create_failure_record(
                e,
                phase="EXECUTION",
                project=self.project,
                query_text=proposal.request_text,
                intent_json=proposal.intent_json,
                proposal=proposal,
            )
            failure_id = str(failure_rec.id) if failure_rec else None
            raise ModificationError(f"Execution failed: {e}", failure_record_id=failure_id)

        # 3. Build semantic diff
        diff_data = {
            "tier": proposal.tier,
            "operation": proposal.operation,
            "affected_entities": len(changes),
            "changes": [
                {
                    "entity": c.global_id,
                    "name": c.entity_name,
                    "ifc_type": c.ifc_type,
                    "pset": c.pset,
                    "property": c.property,
                    "old": c.old_value,
                    "new": c.new_value,
                }
                for c in changes
            ],
        }

        # 4. Git commit
        commit_hash = self.git.commit_modification(
            ifc_file=ifc_file,
            message=proposal.explanation,
            tier=proposal.tier,
            diff_data=diff_data,
            author_name=proposal.created_by.username,
        )

        # 5. Create GitCommit record
        git_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash=commit_hash,
            parent_hash=parent_hash,
            message=proposal.explanation,
            author=proposal.created_by,
            entities_modified=len(changes),
            diff_data=diff_data,
        )

        # 6. Update proposal status + link to commit
        proposal.status = ModificationProposal.Status.APPLIED
        proposal.applied_at = timezone.now()
        proposal.git_commit = git_commit
        proposal.save()

        # 7. Sync the DB index to the new file state. The lifecycle-aware
        # sync needs the AppliedJournal rather than the flattened
        # EntityChange rows, which drop the typed ops and their results.
        self._sync_journal(applied, ifc_file)

        logger.info(
            f"Proposal {proposal.id} applied → commit {commit_hash[:8]} ({len(changes)} changes)"
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
        # 1. Fetch Target
        try:
            target_commit = GitCommit.objects.get(id=commit_id, ifc_file__project=self.project)
        except GitCommit.DoesNotExist:
            raise ModificationError("Commit not found.")

        ifc_file = target_commit.ifc_file

        # 2. Git Level Revert
        # This checks out the file and commits the result as a NEW commit.
        success = self.git.rollback(ifc_file, target_commit.commit_hash)

        if not success:
            raise ModificationError("Failed to revert file in git repository.")

        # 3. Get the new HEAD hash (git.rollback created a new commit).
        new_head_hash = self.git.get_parent_hash()

        # 4. Create Audit Record (The 'Revert' Commit)
        new_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash=new_head_hash,
            parent_hash=target_commit.commit_hash,  # The source we reverted TO
            message=(
                f"Restored version from "
                f"{target_commit.created_at.strftime('%Y-%m-%d %H:%M')} - "
                f"{target_commit.commit_hash[:8]}"
            ),
            author=user,
            entities_modified=0,  # Unknown until we re-parse
            diff_data={
                "operation": "ROLLBACK",
                "restored_from_hash": target_commit.commit_hash,
                "restored_from_date": str(target_commit.created_at),
            },
            rolled_back=True,
        )

        # 5. DB Synchronization — the file on disk is now completely different
        # from what the DB thinks it is, so re-parse it wholesale.
        logger.info(f"Re-parsing IFC file {ifc_file.name} after restore...")

        processor = IFCProcessingService(ifc_file)
        pipeline_success = processor.run_pipeline()

        if not pipeline_success:
            # Parsing failed — we are in a dangerous state (File != DB).
            logger.error("Restore succeeded in Git but DB sync failed.")
            ifc_file.status = "failed"
            ifc_file.error_message = "File restored, but database sync failed. Please re-process."
            ifc_file.save()
            raise ModificationError("File restored, but database parsing failed.")

        return new_commit

    # ── Write paths ────────────────────────────────────────

    @staticmethod
    def _is_journal_proposal(proposal: ModificationProposal) -> bool:
        """Journal proposals carry a ``schema_version`` key in ``changes``."""
        changes = proposal.changes
        return isinstance(changes, dict) and "schema_version" in changes

    def _execute_journal(
        self, proposal: ModificationProposal
    ) -> tuple[list[EntityChange], AppliedJournal]:
        """Execute a journal proposal via the unified JournalExecutor.

        The journal is decoded from ``proposal.changes``, replayed onto a
        temp copy, and atomically swapped over the original. Old values are
        re-read from the model at apply time.

        Returns both the flattened ``EntityChange`` rows (for GitCommit) and
        the ``AppliedJournal`` itself — entity-lifecycle sync needs the typed
        ops and their execution-time results, which the flattened rows drop.
        """
        journal = MutationJournal.from_json_dict(proposal.changes)
        executor = JournalExecutor(proposal.ifc_file.file.path)
        applied = executor.apply(journal)
        if applied.stale_count:
            logger.warning(
                "Proposal %s: %d journal mutation(s) had drifted old values.",
                proposal.id,
                applied.stale_count,
            )
        return applied_to_entity_changes(applied), applied

    # ── DB sync ────────────────────────────────────────────

    @transaction.atomic
    def _sync_journal(self, applied: AppliedJournal, ifc_file) -> None:
        """Sync the DB index after a journal ran, lifecycle ops included.

        The file has already been replaced by the time this runs and there is
        no git safety net for the database, so the whole sync is atomic: a
        half-applied lifecycle would leave the index lying about the model.
        """
        passthrough: list[EntityChange] = []
        code_touched: list[str] = []

        for item in applied.applied:
            op = item.mutation.op
            if op == MutationOp.CREATE_ENTITY:
                self._sync_created(item, ifc_file)
            elif op == MutationOp.DELETE_ENTITY:
                self._sync_deleted(item, ifc_file)
            elif op == MutationOp.ASSIGN_RELATIONSHIP:
                self._sync_relationship(item, ifc_file)
            elif op == MutationOp.RUN_CODE:
                # Generated code reports every entity it touched; refresh
                # exactly those from the file rather than re-parsing it all.
                global_id = (item.result or {}).get("global_id") or ""
                if global_id:
                    code_touched.append(global_id)
            else:
                passthrough.extend(
                    applied_to_entity_changes(
                        AppliedJournal(journal=applied.journal, applied=(item,))
                    )
                )

        if passthrough:
            self._sync_entity_properties(passthrough, ifc_file)

        if code_touched:
            self._sync_run_code(code_touched, ifc_file)

    def _sync_created(self, item, ifc_file) -> None:
        """Insert the DB row (and spatial node) for a newly created entity."""
        result = item.result or {}
        mutation = item.mutation
        global_id = result.get("global_id") or ""

        if not global_id:
            if mutation.ifc_type in _NON_ROOTED_CLASSES:
                # Materials and classifications have no GlobalId and never
                # enter the entity index — nothing to sync.
                logger.info(
                    "Skipping DB sync for non-rooted %s %r",
                    mutation.ifc_type,
                    mutation.entity_name,
                )
                return
            raise ModificationError(
                f"Created {mutation.ifc_type} has no GlobalId — the DB index "
                f"cannot be synced. This is a bug in the executor."
            )

        entity, _created = IFCEntity.objects.update_or_create(
            ifc_file=ifc_file,
            global_id=global_id,
            defaults={
                "ifc_type": mutation.ifc_type,
                "name": mutation.entity_name,
                "ifc_description": (mutation.params or {}).get("description", ""),
                "properties": {},
            },
        )

        spatial_type = _SPATIAL_TYPE_MAP.get(mutation.ifc_type)
        if not spatial_type:
            return

        parent_global_id = (mutation.params or {}).get("parent_global_id") or ""
        parent_node = (
            IFCSpatialElement.objects.filter(
                ifc_file=ifc_file, entity__global_id=parent_global_id
            ).first()
            if parent_global_id
            else None
        )
        IFCSpatialElement.objects.update_or_create(
            ifc_file=ifc_file,
            entity=entity,
            defaults={
                "spatial_type": spatial_type,
                "parent": parent_node,
                "long_name": (mutation.params or {}).get("long_name", ""),
            },
        )

    def _sync_deleted(self, item, ifc_file) -> None:
        """Drop the DB row for a deleted entity.

        The spatial node follows via the OneToOne CASCADE, and other
        entities' ``spatial_container`` is SET_NULL, so nothing dangles.
        """
        global_id = item.mutation.global_id
        deleted, _ = IFCEntity.objects.filter(ifc_file=ifc_file, global_id=global_id).delete()
        if not deleted:
            logger.warning("Deleted entity %s was not in the DB index", global_id)

    def _sync_relationship(self, item, ifc_file) -> None:
        """Repoint ``IFCEntity.spatial_container`` after a container move.

        Without this the file says the element sits on the new storey while
        the index still says the old one — and Explore, spatial filters and
        the resolver all read the index, not the file.
        """
        result = item.result or {}
        global_id = item.mutation.global_id
        destination_global_id = result.get("destination_global_id") or (
            item.mutation.params or {}
        ).get("destination_global_id", "")

        entity = IFCEntity.objects.filter(ifc_file=ifc_file, global_id=global_id).first()
        if entity is None:
            logger.warning("Moved entity %s was not in the DB index", global_id)
            return

        destination_node = IFCSpatialElement.objects.filter(
            ifc_file=ifc_file, entity__global_id=destination_global_id
        ).first()
        if destination_node is None:
            raise ModificationError(
                f"Destination {destination_global_id} has no spatial node in the index — "
                f"the container move cannot be recorded."
            )

        entity.spatial_container = destination_node
        entity.save(update_fields=["spatial_container"])

    def _sync_run_code(self, global_ids: list[str], ifc_file) -> None:
        """Refresh only the entities generated code reported touching.

        Re-opens the modified file once and re-reads each reported entity,
        upserting the row from the file itself. Property extraction reuses
        :meth:`IFCParser._get_properties`, so the shape is identical to a
        full parse (``Pset.Prop`` keys, ``Type.Pset.Prop`` fallbacks, the
        same value coercion) — no bespoke second implementation to drift.

        This deliberately replaces a full ``run_pipeline()`` re-index, which
        re-parses every entity AND regenerates every embedding (hundreds of
        sequential Ollama calls — minutes on a real model, synchronously,
        inside the approve request).

        Two accepted limitations, both bounded by the human code-review gate
        that generated code still has to pass:

        * It trusts the code's self-reported change list. Code that mutates
          something it does not report leaves that row stale.
        * Embeddings are NOT regenerated, so an entity whose description
          changed keeps a stale vector until the file is processed again.
        """
        from ifc_processor.services.parser import IFCParser

        try:
            model = ifcopenshell.open(ifc_file.file.path)
        except Exception as e:  # noqa: BLE001 — the write already succeeded
            logger.error("Could not re-open %s to sync code changes: %s", ifc_file.name, e)
            return

        # Constructor just stores the file — cheap to build purely for its
        # property-extraction logic.
        parser = IFCParser(ifc_file)
        refreshed = removed = 0

        for global_id in dict.fromkeys(global_ids):  # de-dupe, keep order
            element = self._find_element(model, global_id)
            if element is None:
                deleted, _ = IFCEntity.objects.filter(
                    ifc_file=ifc_file, global_id=global_id
                ).delete()
                removed += deleted
                continue

            IFCEntity.objects.update_or_create(
                ifc_file=ifc_file,
                global_id=global_id,
                defaults={
                    "ifc_type": element.is_a(),
                    "name": getattr(element, "Name", None) or "",
                    "ifc_description": getattr(element, "Description", None) or "",
                    "tag": getattr(element, "Tag", None) or "",
                    "properties": parser._get_properties(element),
                },
            )
            refreshed += 1

        logger.info(
            "RUN_CODE sync on %s: %d entit%s refreshed, %d removed",
            ifc_file.name,
            refreshed,
            "y" if refreshed == 1 else "ies",
            removed,
        )

    @staticmethod
    def _find_element(model, global_id: str):
        """Resolve a GlobalId in the model, or None when it is gone."""
        try:
            return model.by_guid(global_id)
        except (RuntimeError, KeyError):
            return None

    def _sync_entity_properties(self, changes: list[EntityChange], ifc_file) -> None:
        """
        Update entity properties in the database after a successful write.

        This keeps the DB in sync with the IFC file so that
        subsequent queries and validations reflect the new state.
        """
        for change in changes:
            try:
                entity = IFCEntity.objects.get(
                    ifc_file=ifc_file,
                    global_id=change.global_id,
                )

                if change.pset == "(attribute)":
                    # Direct attribute change — mirror the modeled IFC attributes
                    # to their dedicated columns. ObjectType / LongName remain in
                    # the properties JSON since they aren't first-class on the model.
                    if change.property == "Name":
                        entity.name = change.new_value
                    elif change.property == "Description":
                        entity.ifc_description = change.new_value
                    elif change.property == "Tag":
                        entity.tag = change.new_value
                elif change.new_value == "(removed)":
                    key = f"{change.pset}.{change.property}"
                    entity.properties.pop(key, None)
                else:
                    key = f"{change.pset}.{change.property}"
                    entity.properties[key] = change.new_value

                entity.save(update_fields=["properties", "name", "ifc_description", "tag"])

            except IFCEntity.DoesNotExist:
                logger.warning(f"Could not sync entity {change.global_id} — not in DB")
