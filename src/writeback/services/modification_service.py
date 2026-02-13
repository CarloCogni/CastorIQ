# writeback/services/modification_service.py
"""
Modification orchestrator for IFC write-back.

Coordinates the full lifecycle:
    classify → filter → validate → preview → execute → commit

Business logic lives here. Views stay thin.
"""

import json
import logging
from django.utils import timezone
from ifc_processor.models import IFCEntity
from writeback.models import ModificationProposal, GitCommit
from ifc_processor.services.processor import IFCProcessingService
from .git_service import GitService
from .filter_engine import FilterEngine
from .ifc_writer import Tier1Writer, EntityChange, IFCWriteError
from .tier1_validator import Tier1Validator, ValidationResult
from .intent_classifier import IntentClassifier, IntentParseError
from .ifc_standard_psets import lookup_property, get_applicable_psets

logger = logging.getLogger(__name__)


class ModificationError(Exception):
    """User-facing error for modification failures."""
    pass


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

    def __init__(self, project):
        self.project = project
        self.git = GitService(project)
        self.filter_engine = FilterEngine(project)
        self.classifier = IntentClassifier()
        self.validator = Tier1Validator()

    # ── Phase 1: Propose ───────────────────────────────────

    def propose(
        self,
        user_message: str,
        user,
        ifc_file=None,
        message_obj=None,
    ) -> ModificationProposal:
        """
        Classify user intent, validate, and create a pending proposal.

        This does NOT modify the IFC file. It creates a proposal
        for the user to review and approve.

        Args:
            user_message: Natural language modification request
            user: The requesting user
            ifc_file: Specific IFC file (auto-detected if None)
            message_obj: Optional chat Message to link

        Returns:
            ModificationProposal with status=PENDING

        Raises:
            ModificationError on classification/validation failure.
        """
        # 0. Sanity check — do we have any entities?
        all_entities = list(
            IFCEntity.objects.filter(
                ifc_file__project=self.project,
                ifc_file__status="completed",
            )[:100]
        )

        if not all_entities:
            raise ModificationError(
                "No processed IFC entities found in this project. "
                "Please upload and process an IFC file first."
            )

        # 1. Build entity context for the LLM
        entity_context = self.classifier.build_entity_context(all_entities)

        # 2. Classify intent via LLM
        try:
            classified = self.classifier.classify(user_message, entity_context)

            # Handle chained operations
            # Handle chained operations
            if isinstance(classified, list):
                return self._propose_chain(
                    classified, user_message, user=user,
                    ifc_file=ifc_file, message_obj=message_obj,
                )

            intent = classified
        except IntentParseError as e:
            raise ModificationError(f"Could not understand the request: {e}")

        # 3. Check tier — for now we only handle Tier 1
        tier = intent.get("tier", 0)
        if tier != 1:
            raise ModificationError(
                f"This request requires Tier {tier} capabilities "
                f"(not yet implemented). "
                f"Reason: {intent.get('explanation', 'complex operation')}"
            )

        # 3b. Confidence threshold
        confidence = intent.get("confidence", 0)
        if confidence < 60:
            raise ModificationError(
                f"Low confidence ({confidence}%). "
                f"Please be more specific. For example, use exact property "
                f"names and entity types. "
                f"LLM interpretation: {intent.get('explanation', '?')}"
            )

        # 4. Resolve entity filter
        filter_spec = intent.get("filter", {})
        try:
            matched_qs = self.filter_engine.resolve(filter_spec)
        except ValueError as e:
            raise ModificationError(str(e))

        matched_entities = list(matched_qs)

        # 5. Validate operation against real entity data
        validation = self.validator.validate(intent, matched_entities)

        if not validation.valid:
            # Auto-fallback: SET_PROPERTY failed because property doesn't
            # exist → retry as ADD_PROPERTY if it's a standard pset property
            if (
                intent.get("operation") == "SET_PROPERTY"
                and "not found on any" in validation.error
            ):
                from .ifc_standard_psets import lookup_property
                pset = intent.get("pset", "")
                prop = intent.get("property", "")
                if lookup_property(pset, prop) is not None:
                    logger.info(
                        f"Auto-fallback: SET_PROPERTY → ADD_PROPERTY "
                        f"for {pset}.{prop} (standard property, not yet on entities)"
                    )
                    intent["operation"] = "ADD_PROPERTY"
                    validation = self.validator.validate(intent, matched_entities)

            if not validation.valid:
                raise ModificationError(
                    f"Validation failed: {validation.error}"
                )
        # 6. Build diff preview
        diff_preview = self._build_diff_preview(intent, validation.entities)

        # 6b. Warn if SET_ATTRIBUTE hits multiple entities (likely unintended rename)
        operation = intent.get("operation", "")
        MAX_NUM_ENTITIES = 10
        if operation == "SET_ATTRIBUTE" and len(validation.entities) > MAX_NUM_ENTITIES:
            raise ModificationError(
                f"SET_ATTRIBUTE would affect {len(validation.entities)} entities. "
                f"Renaming this many entities is likely unintended. "
                f"Please use a more specific filter (e.g. include the entity name)."
                f"For testing purposes, can't modify more than {MAX_NUM_ENTITIES} entities at once."
            )

        # 7. Resolve IFC file
        if ifc_file is None:
            ifc_file = matched_entities[0].ifc_file

        # 8. Create the proposal
        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=intent.get("explanation", ""),
            changes=intent,
            diff_preview=json.dumps(diff_preview),
            affected_count=len(validation.entities),
            status=ModificationProposal.Status.PENDING,
            # RSAA fields
            tier=tier,
            operation=intent.get("operation", ""),
            intent_json=intent,
            filter_spec=filter_spec,
            confidence=intent.get("confidence", 0),  # already 0-100 from classifier
        )

        logger.info(
            f"Proposal {proposal.id} created: {intent['operation']} "
            f"on {len(validation.entities)} entities (Tier {tier})"
        )

        return proposal

    # ── Phase 2: Execute ───────────────────────────────────

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
                f"Proposal {proposal.id} is '{proposal.status}', "
                f"expected 'pending' or 'approved'."
            )

        ifc_file = proposal.ifc_file

        # 1. Safety snapshot
        self.git.ensure_repo()
        parent_hash = self.git.snapshot(ifc_file) or self.git.get_parent_hash()

        # 2. Execute the write operation
        try:
            changes = self._execute_tier1(proposal)
        except (IFCWriteError, Exception) as e:
            proposal.status = ModificationProposal.Status.FAILED
            proposal.error_message = str(e)
            proposal.save()

            if parent_hash:
                self.git.rollback(ifc_file, parent_hash)
                logger.warning(f"Auto-rolled back after failure: {e}")

            raise ModificationError(f"Execution failed: {e}")

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

        # 7. Sync changed properties back to DB
        self._sync_entity_properties(changes, ifc_file)

        logger.info(
            f"Proposal {proposal.id} applied → commit {commit_hash[:8]} "
            f"({len(changes)} changes)"
        )

        return git_commit

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

    # ── Rollback ───────────────────────────────────────────

    def rollback(self, git_commit: GitCommit) -> bool:
        """
        Rollback a previously applied modification.

        Restores the IFC file to pre-modification state.
        """
        if not git_commit.parent_hash:
            raise ModificationError("No parent commit to rollback to.")

        success = self.git.rollback(
            git_commit.ifc_file, git_commit.parent_hash
        )

        if success:
            git_commit.rolled_back = True
            git_commit.save()

            # Find the linked proposal and update its status
            try:
                proposal = git_commit.proposal  # reverse OneToOne
                proposal.status = ModificationProposal.Status.FAILED
                proposal.error_message = "Rolled back by user"
                proposal.save()
            except ModificationProposal.DoesNotExist:
                pass

            logger.info(
                f"Rolled back commit {git_commit.commit_hash[:8]} "
                f"to {git_commit.parent_hash[:8]}"
            )

        return success

        # ─── RESTORE / TIME MACHINE ─────────────────────────────

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
        # This function (in your GitService) checks out the file and commits the result as a NEW commit.
        # It returns True if successful.
        success = self.git.rollback(ifc_file, target_commit.commit_hash)

        if not success:
            raise ModificationError("Failed to revert file in git repository.")

        # 3. Get the new HEAD hash
        # Since git.rollback created a new commit, we grab that hash.
        new_head_hash = self.git.get_parent_hash()

        # 4. Create Audit Record (The 'Revert' Commit)
        new_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash=new_head_hash,
            parent_hash=target_commit.commit_hash,  # Pointing to the source we reverted TO
            message=f"Restored version from {target_commit.created_at.strftime('%Y-%m-%d %H:%M')} - {target_commit.commit_hash[:8]}",
            author=user,
            entities_modified=0,  # Unknown until we re-parse, or we leave as 0 for resets
            diff_data={
                "operation": "ROLLBACK",
                "restored_from_hash": target_commit.commit_hash,
                "restored_from_date": str(target_commit.created_at)
            },
            rolled_back=True
        )

        # 5. DB Synchronization (The Heavy Lifting)
        # We MUST re-parse the file because the file on disk is now completely different
        # from what the DB thinks it is.
        logger.info(f"Re-parsing IFC file {ifc_file.name} after restore...")

        processor = IFCProcessingService(ifc_file)

        # We run the pipeline. This handles:
        # - Deleting old entities
        # - Re-parsing from the reverted file
        # - Re-generating embeddings (essential for RAG to work on the restored version)
        pipeline_success = processor.run_pipeline()

        if not pipeline_success:
            # If parsing fails, we are in a dangerous state (File != DB).
            # We flag the file as failed so the user knows to fix it.
            logger.error("Restore succeeded in Git but DB sync failed.")
            ifc_file.status = "failed"
            ifc_file.error_message = "File restored, but database sync failed. Please re-process."
            ifc_file.save()
            raise ModificationError("File restored, but database parsing failed.")

        return new_commit

    # ── Internals ──────────────────────────────────────────

    def _execute_tier1(
        self, proposal: ModificationProposal
    ) -> list[EntityChange]:
        """Execute a Tier 1 operation on the IFC file."""
        intent = proposal.intent_json
        ifc_path = proposal.ifc_file.file.path

        writer = Tier1Writer(ifc_path)

        # Resolve global IDs from the filter
        entities = self.filter_engine.resolve(proposal.filter_spec)
        global_ids = list(entities.values_list("global_id", flat=True))

        operation = intent["operation"]

        if operation == "SET_PROPERTY":
            changes = writer.set_property(
                global_ids=global_ids,
                pset=intent["pset"],
                prop=intent["property"],
                value=intent["new_value"],
            )
        elif operation == "ADD_PROPERTY":
            changes = writer.add_property(
                global_ids=global_ids,
                pset=intent["pset"],
                prop=intent["property"],
                value=intent["new_value"],
            )
        elif operation == "REMOVE_PROPERTY":
            changes = writer.remove_property(
                global_ids=global_ids,
                pset=intent["pset"],
                prop=intent["property"],
            )
        elif operation == "SET_ATTRIBUTE":
            changes = writer.set_attribute(
                global_ids=global_ids,
                attribute=intent["attribute"],
                value=intent["new_value"],
            )
        else:
            raise IFCWriteError(f"Unknown Tier 1 operation: {operation}")

        writer.save()
        return changes

    def _build_diff_preview(
        self, intent: dict, entities: list[IFCEntity]
    ) -> list[dict]:
        """
        Build a diff preview for the UI without touching the IFC file.

        Reads current values from the DB entity properties.
        """
        operation = intent.get("operation", "")
        pset = intent.get("pset", "")
        prop = intent.get("property", "")
        attribute = intent.get("attribute", "")
        new_value = intent.get("new_value", "")

        preview = []

        for entity in entities:
            if operation in ("SET_PROPERTY", "REMOVE_PROPERTY"):
                key = f"{pset}.{prop}"
                old_value = (entity.properties or {}).get(key, "(not set)")
            elif operation == "ADD_PROPERTY":
                old_value = "(none)"
            elif operation == "SET_ATTRIBUTE":
                old_value = entity.name if attribute == "Name" else "(attribute)"
            else:
                old_value = "?"

            preview.append({
                "global_id": entity.global_id,
                "name": entity.name or entity.global_id[:8],
                "ifc_type": entity.ifc_type,
                "field": f"{pset}.{prop}" if pset else attribute,
                "old_value": str(old_value),
                "new_value": str(new_value) if operation != "REMOVE_PROPERTY" else "(removed)",
            })

        return preview

    def _sync_entity_properties(
        self, changes: list[EntityChange], ifc_file
    ) -> None:
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
                    # Direct attribute change
                    if change.property == "Name":
                        entity.name = change.new_value
                    # Could extend for Description, etc.
                elif change.new_value == "(removed)":
                    key = f"{change.pset}.{change.property}"
                    entity.properties.pop(key, None)
                else:
                    key = f"{change.pset}.{change.property}"
                    entity.properties[key] = change.new_value

                entity.save(update_fields=["properties", "name"])

            except IFCEntity.DoesNotExist:
                logger.warning(
                    f"Could not sync entity {change.global_id} — not in DB"
                )

    def _propose_chain(self, intents: list, user_message: str, user=None, ifc_file=None, message_obj=None):
        """
        Handle a chain of Tier 1 operations from a single user request.

        Creates one proposal per sub-intent, linked by a shared chain_id.
        Returns the list of proposals.
        """
        import uuid
        chain_id = str(uuid.uuid4())[:8]
        proposals = []

        for i, intent in enumerate(intents):
            tier = intent.get("tier", 0)
            if tier != 1:
                raise ModificationError(
                    f"Chain element {i + 1} requires Tier {tier}. "
                    f"All chained operations must be Tier 1."
                )

            confidence = intent.get("confidence", 0)
            if confidence < 60:
                raise ModificationError(
                    f"Chain element {i + 1} has low confidence ({confidence}%). "
                    f"Please be more specific."
                )

            # Resolve filter
            filter_spec = intent.get("filter", {})
            try:
                matched_qs = self.filter_engine.resolve(filter_spec)
            except ValueError as e:
                raise ModificationError(
                    f"Chain element {i + 1} filter error: {e}"
                )
            matched_entities = list(matched_qs)

            # Validate
            validation = self.validator.validate(intent, matched_entities)

            if not validation.valid:
                # Try SET→ADD fallback
                if (
                        intent.get("operation") == "SET_PROPERTY"
                        and "not found on any" in validation.error
                ):
                    pset = intent.get("pset", "")
                    prop = intent.get("property", "")
                    if lookup_property(pset, prop) is not None:
                        intent["operation"] = "ADD_PROPERTY"
                        validation = self.validator.validate(intent, matched_entities)

                if not validation.valid:
                    raise ModificationError(
                        f"Chain element {i + 1} validation failed: {validation.error}"
                    )

            # Build diff preview
            diff_preview = self._build_diff_preview(intent, validation.entities)

            # Resolve IFC file from first match if not provided
            resolved_ifc_file = ifc_file or matched_entities[0].ifc_file

            # Create proposal (same field names as propose())
            proposal = ModificationProposal.objects.create(
                message=message_obj,
                ifc_file=resolved_ifc_file,
                created_by=user,
                request_text=f"[Chain {chain_id} ({i + 1}/{len(intents)})] {user_message}",
                explanation=intent.get("explanation", ""),
                changes=intent,
                diff_preview=json.dumps(diff_preview),
                affected_count=len(validation.entities),
                status=ModificationProposal.Status.PENDING,
                tier=tier,
                operation=intent.get("operation", ""),
                intent_json=intent,
                filter_spec=filter_spec,
                confidence=confidence,
            )
            proposals.append(proposal)

        logger.info(
            f"Chain {chain_id}: created {len(proposals)} proposals"
        )

        return proposals

