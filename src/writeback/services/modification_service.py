# writeback/services/modification_service.py
"""
Modification orchestrator for IFC write-back.

Coordinates the full lifecycle:
    classify → filter → validate → preview → execute → commit

Business logic lives here. Views stay thin.
"""

import json
import logging
import uuid

from django.conf import settings
from django.utils import timezone

from core.llm import resolve_model_name
from core.token_budget import compute_budget
from ifc_processor.models import IFCEntity
from ifc_processor.services.ifc_standard_psets import lookup_property
from ifc_processor.services.ifc_writer import EntityChange, IFCWriteError, Tier1Writer
from ifc_processor.services.processor import IFCProcessingService
from ifc_processor.services.tier2_writer import Tier2Writer
from writeback.models import GitCommit, ModificationProposal
from writeback.services.guardian_service import GuardianService

from .emitters import CancellationError, NullEmitter, PipelineEmitter
from .entity_resolver import (
    MODE_EXISTING_TARGET,
    MODE_NEW_TARGET,
    MODE_PARENT_TARGET,
    EntityNameResolver,
)
from .filter_engine import FilterEngine
from .git_service import GitService
from .intent_assembler import (
    assemble_tier1_intent,
    assemble_tier2_intent,
    derive_tier3_inputs,
)
from .intent_classifier import SYSTEM_PROMPT as CLASSIFY_SYSTEM_PROMPT
from .intent_classifier import IntentClassifier, IntentParseError
from .slot_extractor import SlotExtractionError, SlotExtractor
from .tier1_validator import Tier1Validator
from .tier2_planner import PlanGenerationError, Tier2Planner
from .tier2_validator import Tier2Validator
from .tier3_executor import Tier3ExecutionError, Tier3Executor
from .tier3_planner import CodeGenerationError, Tier3Planner
from .tier3_reviewer import Tier3Reviewer
from .tier_router import RoutingResult
from .tier_router import route as route_tier
from .triage_classifier import TriageClassifier, TriageError

logger = logging.getLogger(__name__)


class ModificationError(Exception):
    """User-facing error for modification failures."""

    def __init__(self, message: str, failure_record_id: str | None = None) -> None:
        super().__init__(message)
        self.failure_record_id = failure_record_id


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
        self.git = GitService(project)
        self.filter_engine = FilterEngine(project)
        self.entity_resolver = EntityNameResolver(
            project, user=user, filter_engine=self.filter_engine
        )
        self.classifier = IntentClassifier(user=user)
        self.t1_validator = Tier1Validator()
        self.t2_planner = Tier2Planner(user=user)
        self.t2_validator = Tier2Validator(project)
        self.t3_planner = Tier3Planner(user=user)
        self.t3_reviewer = Tier3Reviewer(user=user)
        # V2 pipeline services. Cheap to construct; LLM is not invoked
        # until ``classify`` / ``extract`` are called.
        self.triage_classifier = TriageClassifier(user=user)
        self.slot_extractor = SlotExtractor(user=user)

    # ── Phase 1: Propose ───────────────────────────────────

    def propose(
        self,
        user_message: str,
        user,
        ifc_file=None,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        failure_context: str | None = None,
    ) -> ModificationProposal:
        """
        Classify user intent, validate, and create a pending proposal.

        This does NOT modify the IFC file. It creates a proposal
        for the user to review and approve.

        Args:
            user_message:    Natural language modification request
            user:            The requesting user
            ifc_file:        Specific IFC file (auto-detected if None)
            message_obj:     Optional chat Message to link
            failure_context: Optional context string from a prior FailureRecord,
                             injected into the classifier's system prompt on retry.

        Returns:
            ModificationProposal with status=PENDING

        Raises:
            ModificationError on classification/validation failure.
        """
        emitter = emitter or NullEmitter()

        # V2 pipeline: behind a settings flag while the new prompts are
        # being validated against real Ollama models. Default off — flip
        # ``WRITEBACK_PIPELINE_V2 = True`` in settings to opt in.
        if getattr(settings, "WRITEBACK_PIPELINE_V2", False):
            return self._propose_v2(
                user_message,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                failure_context=failure_context,
            )

        # 0a. Feasibility pre-check — reject vague requests before any expensive work.
        from writeback.services.feasibility_checker import FeasibilityChecker

        emitter.emit("feasibility", "running", "Checking request feasibility…")
        feasibility = FeasibilityChecker(user=user).check(user_message)
        if not feasibility.feasible:
            emitter.emit("feasibility", "error", feasibility.reason)
            self._raise_with_failure_record(
                ValueError(f"Request too vague: {feasibility.reason}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=None,
            )
        emitter.emit("feasibility", "done", "Request looks specific enough")

        # 0b. Sanity check — do we have any entities?
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

        # 1. Retrieve skill examples FIRST so injection tokens are known before
        #    sizing entity context (injection shrinks the available budget).
        # Local import avoids a circular dependency at module load time
        # (metacastor imports writeback.models; writeback must not import
        # metacastor at module level).
        from metacastor.services.skill_retriever import retrieve as _retrieve_skills
        from writeback.services.intent_classifier import _format_skill_injection

        try:
            skill_examples = _retrieve_skills(user_message, project=self.project)
        except Exception:
            logger.warning("Skill retrieval failed — proceeding without few-shot injection.")
            skill_examples = []

        injection_text = _format_skill_injection(skill_examples) if skill_examples else ""

        n = len(skill_examples)
        self._skill_count = n  # consumed by consumer serialization
        emitter.emit(
            "skills",
            "done",
            f"{n} skill example{'s' if n != 1 else ''} retrieved"
            if n
            else "No skill examples found",
            {"skill_count": n},
        )

        # 1b. Pre-LLM entity resolution. The resolver is now AUTHORITATIVE for
        #     entity targeting — its output becomes the filter directly via
        #     build_filter_spec(). When the resolver returns nothing, we
        #     reject the request rather than falling through to the LLM,
        #     because the LLM no longer has a `filter` field in its schema.
        emitter.emit("resolve", "running", "Resolving entity references…")
        resolution = self.entity_resolver.resolve(user_message)
        if resolution.is_empty:
            emitter.emit("resolve", "done", "No specific entities resolved", {"resolved_count": 0})
            self._raise_with_failure_record(
                ValueError(
                    "I couldn't identify any specific entity, type, or "
                    "category in your request. Please name the entity "
                    '(e.g. "wall :285330"), the category (e.g. "all '
                    'walls"), or a property predicate (e.g. "all '
                    'external walls").'
                ),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=None,
            )

        entity_context_input = resolution.entities
        logger.info("Pre-LLM resolution: %s", resolution.diagnostic)
        emitter.emit(
            "resolve",
            "done",
            f"Resolved {len(resolution.entities)} candidate"
            f"{'s' if len(resolution.entities) != 1 else ''}",
            {"resolved_count": len(resolution.entities), "scope": resolution.scope},
        )

        pipeline_warnings: list[str] = []
        if resolution.misses:
            misses_str = ", ".join(resolution.misses)
            pipeline_warnings.append(
                f"Could not match: {misses_str}. Proceeding with the "
                f"{len(resolution.entities)} matched "
                f"entit{'y' if len(resolution.entities) == 1 else 'ies'}."
            )

        # Build entity context — token-aware, two-pass, injection-aware.
        model_name = resolve_model_name(user)
        prelim_budget = compute_budget(
            model_name, system=CLASSIFY_SYSTEM_PROMPT, injected=injection_text
        )
        entity_context = self.classifier.build_entity_context(
            entity_context_input, available_tokens=prelim_budget.remaining_for_injection
        )
        final_budget = compute_budget(
            model_name,
            system=CLASSIFY_SYSTEM_PROMPT,
            entity_context=entity_context,
            injected=injection_text,
        )
        logger.debug(
            "Modify token budget — model=%s util=%.0f%% total=%d/%d skill_examples=%d",
            model_name,
            final_budget.utilization_pct,
            final_budget.total_input,
            final_budget.max_usable,
            len(skill_examples),
        )
        emitter.emit(
            "context_budget",
            "info",
            f"Context: {final_budget.utilization_pct:.0f}%"
            f" ({final_budget.total_input:,} / {final_budget.model_context_window:,} tokens)",
            {
                "utilization_pct": final_budget.utilization_pct,
                "total_input": final_budget.total_input,
                "context_window": final_budget.model_context_window,
            },
        )

        # 2. Classify intent via LLM
        emitter.emit("classify", "running", "Classifying intent…")
        try:
            classified = self.classifier.classify(
                user_message,
                entity_context,
                skill_examples=skill_examples,
                failure_context=failure_context,
            )

            # Handle chained operations
            if isinstance(classified, list):
                emitter.emit(
                    "classify",
                    "done",
                    f"Chained operation — {len(classified)} steps",
                    {"chain": True, "steps": len(classified)},
                )
                return self._propose_chain(
                    classified,
                    user_message,
                    user=user,
                    ifc_file=ifc_file,
                    message_obj=message_obj,
                    entity_context=entity_context,
                    emitter=emitter,
                    skill_examples=skill_examples,
                    resolution=resolution,
                    pipeline_warnings=pipeline_warnings,
                )

            intent = classified
        except IntentParseError as e:
            emitter.emit("classify", "error", f"Could not understand the request: {e}")
            # Route through the failure-record path so the consumer surfaces
            # the help card (with retry button) instead of a raw error toast.
            self._raise_with_failure_record(
                ValueError(f"Could not understand the request: {e}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=None,
            )

        # 3. Check tier — route to appropriate handler
        tier = intent.get("tier", 0)
        emitter.emit(
            "classify",
            "done",
            f"Tier {tier} — {intent.get('operation', '?')}",
            {
                "tier": tier,
                "operation": intent.get("operation", ""),
                "confidence": intent.get("confidence", 0),
            },
        )

        if tier == 0:
            explanation = intent.get("explanation", "Request is too ambiguous to process.")
            # Wrap with a stable marker the failure classifier matches on
            # (REQUEST_AMBIGUOUS pattern), but the marker is stripped before
            # the message reaches the user — see _render_diagnosis().
            self._raise_with_failure_record(
                ValueError(f"Ambiguous request: {explanation}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=intent,
            )
        if tier == 2:
            return self._propose_tier2(
                user_message,
                entity_context,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                skill_examples=skill_examples,
            )
        if tier == 3:
            return self._propose_tier3(
                user_message,
                entity_context,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                skill_examples=skill_examples,
            )
        if tier != 1:
            raise ModificationError(
                f"Unexpected tier: {tier}. Reason: {intent.get('explanation', '?')}"
            )

        # 4. Build filter deterministically from the resolver's result.
        #    The LLM's `filter` field (if any) is ignored — entity targeting
        #    is the resolver's job, period. This is the boundary that the
        #    old "filter pinning AND-merge" code conflated.
        emitter.emit("validate", "running", "Matching entities…")
        from .filter_builder import build_filter_spec

        filter_spec = build_filter_spec(resolution)
        logger.info(
            "Filter built from resolver: %d entit%s (scope=%s)",
            len(resolution.entities),
            "y" if len(resolution.entities) == 1 else "ies",
            resolution.scope,
        )
        # Stamp the filter back onto the intent so proposal.intent_json /
        # filter_spec audit fields stay populated for downstream code.
        intent["filter"] = filter_spec

        try:
            matched_qs = self.filter_engine.resolve(filter_spec)
        except ValueError as e:
            raise ModificationError(str(e))

        matched_entities = list(matched_qs)

        # 5. Validate operation against real entity data. SET_PROPERTY now
        #    upserts on standard psets, so the historical SET→ADD auto-
        #    fallback path is folded into the validator itself.
        validation = self.t1_validator.validate(intent, matched_entities)

        if not validation.valid:
            # Type/enum errors are user errors — surface them directly
            if "not found on any" not in validation.error and "missing on" not in validation.error:
                emitter.emit("validate", "error", validation.error)
                self._raise_with_failure_record(
                    ValueError(validation.error),
                    phase="VALIDATION",
                    query_text=user_message,
                    intent_json=intent,
                )
            # Custom-pset / property-not-found cases → escalate to Tier 2
            logger.info(f"Tier 1 validation failed, escalating to Tier 2: {validation.error}")
            emitter.emit("validate", "done", "Escalating to Tier 2 plan…", {"escalated": True})
            return self._propose_tier2(
                user_message,
                entity_context,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                skill_examples=skill_examples,
                escalation_hint=validation.error,
            )

        emitter.emit(
            "validate",
            "done",
            f"{len(validation.entities)} entit{'y' if len(validation.entities) == 1 else 'ies'} matched",
            {"entities_count": len(validation.entities)},
        )

        # 5b. Combined confidence + groundedness gate (post-validation)
        confidence = intent.get("confidence", 0)
        groundedness = validation.groundedness_score
        effective = min(confidence, groundedness) if groundedness > 0 else confidence
        if effective < 60:
            msg = (
                f"Low confidence ({effective}% effective: LLM={confidence}%, "
                f"registry={groundedness}%). Please be more specific. "
                f"For example, use exact property names and entity types. "
                f"LLM interpretation: {intent.get('explanation', '?')}"
            )
            self._raise_with_failure_record(
                ValueError(msg),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=intent,
            )

        # 6. Build diff preview
        emitter.emit("diff", "running", "Building change preview…")
        diff_preview = self._build_diff_preview(intent, validation.entities)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        # 7. Resolve IFC file
        if ifc_file is None:
            ifc_file = matched_entities[0].ifc_file

        # Compose proposal explanation: stamp pipeline + validator warnings
        # at the top so the user sees them in the proposal card. The
        # groundedness guard already prefixes with "⚠ …" when it fires.
        all_warnings: list[str] = []
        all_warnings.extend(pipeline_warnings)
        all_warnings.extend(validation.warnings or [])
        explanation = intent.get("explanation", "")
        if all_warnings:
            warning_block = "\n".join(f"⚠ {w}" for w in all_warnings)
            explanation = f"{warning_block}\n\n{explanation}" if explanation else warning_block

        # 8. Create the proposal
        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=explanation,
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

        # ── Guardian: cross-reference against project documents ──
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
        except Exception as e:
            logger.warning(f"Guardian check failed (non-blocking): {e}")
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

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
                f"Proposal {proposal.id} is '{proposal.status}', expected 'pending' or 'approved'."
            )

        ifc_file = proposal.ifc_file

        # 1. Safety snapshot
        self.git.ensure_repo()
        parent_hash = self.git.snapshot(ifc_file) or self.git.get_parent_hash()

        # 2. Execute the write operation
        try:
            if proposal.tier == 3:
                changes = self._execute_tier3(proposal)
            elif proposal.tier == 2:
                changes = self._execute_tier2(proposal)
            else:
                changes = self._execute_tier1(proposal)
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

        # 7. Sync changed properties back to DB
        self._sync_entity_properties(changes, ifc_file)

        # 8. Harvest skill example — non-blocking, wrapped in harvester's own try/except
        from metacastor.services.skill_harvester import harvest_skill_example

        harvest_skill_example(proposal, commit_success=True)

        logger.info(
            f"Proposal {proposal.id} applied → commit {commit_hash[:8]} ({len(changes)} changes)"
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

    # ───ROLLBACK / RESTORE / TIME MACHINE ─────────────────────────────

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
                "restored_from_date": str(target_commit.created_at),
            },
            rolled_back=True,
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

    def _raise_with_failure_record(
        self,
        exc: Exception,
        phase: str,
        query_text: str,
        intent_json: dict | None,
        proposal=None,
    ) -> None:
        """
        Create a FailureRecord for exc, then raise ModificationError with its ID.

        Args:
            exc:        The original exception that caused the failure.
            phase:      Pipeline phase — "VALIDATION", "EXECUTION", or "SANDBOX".
            query_text: The user's original query string.
            intent_json: Parsed intent if available; None for very early failures.
            proposal:   Associated ModificationProposal, if one was created.

        Raises:
            ModificationError always.
        """
        from metacastor.services.failure_classifier import create_failure_record

        failure_rec = create_failure_record(
            exc,
            phase=phase,
            project=self.project,
            query_text=query_text,
            intent_json=intent_json,
            proposal=proposal,
        )
        failure_id = str(failure_rec.id) if failure_rec else None
        raise ModificationError(str(exc), failure_record_id=failure_id)

    def _execute_tier1(self, proposal: ModificationProposal) -> list[EntityChange]:
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

    def _execute_tier2(self, proposal: ModificationProposal) -> list[EntityChange]:
        """Execute a Tier 2 plan: iterate steps, using appropriate writer."""
        plan = proposal.intent_json
        ifc_path = proposal.ifc_file.file.path

        writer = Tier2Writer(ifc_path)
        all_changes = []

        for step in plan.get("plan", []):
            op = step["operation"]
            params = step["params"]
            filter_spec = step["filter"]

            # Resolve entities for this step
            entities = self.filter_engine.resolve(filter_spec)
            global_ids = list(entities.values_list("global_id", flat=True))

            step_changes = self._execute_tier2_step(
                writer,
                op,
                global_ids,
                params,
                entities,
            )
            all_changes.extend(step_changes)

        writer.save()
        return all_changes

    def _execute_tier2_step(
        self,
        writer: "Tier2Writer",
        operation: str,
        global_ids: list[str],
        params: dict,
        entities,
    ) -> list[EntityChange]:
        """Execute a single step from a Tier 2 plan."""
        # Tier 1 operations → delegate to the embedded Tier1Writer
        if operation == "SET_PROPERTY":
            return writer.t1.set_property(
                global_ids,
                params["pset"],
                params["property"],
                params["new_value"],
            )
        if operation == "ADD_PROPERTY":
            return writer.t1.add_property(
                global_ids,
                params["pset"],
                params["property"],
                params["new_value"],
            )
        if operation == "REMOVE_PROPERTY":
            return writer.t1.remove_property(
                global_ids,
                params["pset"],
                params["property"],
            )
        if operation == "SET_ATTRIBUTE":
            return writer.t1.set_attribute(
                global_ids,
                params["attribute"],
                params["new_value"],
            )
        # Tier 2 operations
        if operation == "ADD_PSET":
            return writer.add_pset(
                global_ids,
                params["pset_name"],
                params["properties"],
            )
        if operation == "REMOVE_PSET":
            return writer.remove_pset(global_ids, params["pset_name"])
        if operation == "SET_CLASSIFICATION":
            return writer.set_classification(
                global_ids,
                params["system_name"],
                params["reference"],
                params.get("name", ""),
            )
        if operation == "SET_MATERIAL":
            return writer.set_material(global_ids, params["material_name"])
        if operation == "COPY_PROPERTIES":
            source = IFCEntity.objects.filter(
                ifc_file__project=self.project,
                name__icontains=params["source_name"],
            ).first()
            if not source:
                raise IFCWriteError(f"Source entity '{params['source_name']}' not found")
            return writer.copy_properties(
                source.global_id,
                global_ids,
                params["pset_name"],
                params.get("property_names"),
            )

        raise IFCWriteError(f"Unknown Tier 2 operation: {operation}")

    def _build_diff_preview(self, intent: dict, entities: list[IFCEntity]) -> list[dict]:
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

            preview.append(
                {
                    "global_id": entity.global_id,
                    "name": entity.name or entity.global_id[:8],
                    "ifc_type": entity.ifc_type,
                    "field": f"{pset}.{prop}" if pset else attribute,
                    "old_value": str(old_value),
                    "new_value": str(new_value) if operation != "REMOVE_PROPERTY" else "(removed)",
                }
            )

        return preview

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

    def _propose_chain(
        self,
        intents: list,
        user_message: str,
        user=None,
        ifc_file=None,
        message_obj=None,
        entity_context: str = "",
        emitter: PipelineEmitter | None = None,
        skill_examples: list[dict] | None = None,
        resolution=None,
        pipeline_warnings: list[str] | None = None,
    ):
        """
        Handle a chain of Tier 1 operations from a single user request.

        Creates one proposal per sub-intent, linked by a shared chain_id.
        Returns the list of proposals.

        ``resolution`` is the same ResolutionResult used by the parent
        ``propose()`` call. Every step in the chain targets the same
        entity set (the LLM only varies operation/property/value across
        chained intents, never entity targeting).
        """
        from .filter_builder import build_filter_spec

        emitter = emitter or NullEmitter()
        chain_id = str(uuid.uuid4())[:8]
        proposals = []
        pipeline_warnings = list(pipeline_warnings or [])

        if resolution is None or resolution.is_empty:
            raise ModificationError(
                "Chain proposal requires a non-empty resolver result. "
                "This is a programming error — propose() should have "
                "rejected the request before reaching the chain branch."
            )

        chain_filter_spec = build_filter_spec(resolution)

        for i, intent in enumerate(intents):
            tier = intent.get("tier", 0)
            if tier != 1:
                raise ModificationError(
                    f"Chain element {i + 1} requires Tier {tier}. "
                    f"All chained operations must be Tier 1."
                )

            # Filter is the deterministic resolver-built spec, identical
            # for every step. Stamp it onto the intent for audit.
            intent["filter"] = chain_filter_spec
            try:
                matched_qs = self.filter_engine.resolve(chain_filter_spec)
            except ValueError as e:
                raise ModificationError(f"Chain element {i + 1} filter error: {e}")
            matched_entities = list(matched_qs)

            # Validate
            validation = self.t1_validator.validate(intent, matched_entities)

            if not validation.valid:
                # Type/enum errors are user errors — surface them directly
                if (
                    "not found on any" not in validation.error
                    and "missing on" not in validation.error
                ):
                    raise ModificationError(
                        f"Chain element {i + 1} validation failed: {validation.error}"
                    )
                # Custom-pset / property-not-found cases → escalate to Tier 2
                logger.info(
                    f"Chain element {i + 1} failed Tier 1, escalating to Tier 2: {validation.error}"
                )
                emitter.emit("validate", "done", "Escalating to Tier 2 plan…", {"escalated": True})
                return self._propose_tier2(
                    user_message,
                    entity_context,
                    user=user,
                    ifc_file=ifc_file,
                    message_obj=message_obj,
                    emitter=emitter,
                    skill_examples=skill_examples,
                    escalation_hint=validation.error,
                )

            # Combined confidence + groundedness gate (post-validation)
            confidence = intent.get("confidence", 0)
            groundedness = validation.groundedness_score
            effective = min(confidence, groundedness) if groundedness > 0 else confidence
            if effective < 60:
                raise ModificationError(
                    f"Chain element {i + 1} has low confidence ({effective}% effective: "
                    f"LLM={confidence}%, registry={groundedness}%). "
                    f"Please be more specific."
                )

            # Build diff preview
            diff_preview = self._build_diff_preview(intent, validation.entities)

            # Resolve IFC file from first match if not provided
            resolved_ifc_file = ifc_file or matched_entities[0].ifc_file

            # Compose explanation with warnings (only on first step to avoid
            # repeating the resolver-level warnings on every chained card).
            step_warnings: list[str] = list(validation.warnings or [])
            if i == 0:
                step_warnings = pipeline_warnings + step_warnings
            explanation = intent.get("explanation", "")
            if step_warnings:
                warning_block = "\n".join(f"⚠ {w}" for w in step_warnings)
                explanation = f"{warning_block}\n\n{explanation}" if explanation else warning_block

            # Create proposal (same field names as propose())
            proposal = ModificationProposal.objects.create(
                message=message_obj,
                ifc_file=resolved_ifc_file,
                created_by=user,
                request_text=f"[Chain {chain_id} ({i + 1}/{len(intents)})] {user_message}",
                explanation=explanation,
                changes=intent,
                diff_preview=json.dumps(diff_preview),
                affected_count=len(validation.entities),
                status=ModificationProposal.Status.PENDING,
                tier=tier,
                operation=intent.get("operation", ""),
                intent_json=intent,
                filter_spec=chain_filter_spec,
                confidence=confidence,
            )
            emitter.emit("guardian", "running", f"Checking documents for step {i + 1}…")
            try:
                GuardianService().check(proposal)
                emitter.emit(
                    "guardian",
                    "done",
                    "Document check complete",
                    {"verdict": proposal.verification_status},
                )
            except Exception as e:
                logger.warning(f"Guardian check failed (non-blocking): {e}")
                emitter.emit(
                    "guardian", "done", "Document check unavailable", {"verdict": "failed"}
                )

            proposals.append(proposal)

        logger.info(f"Chain {chain_id}: created {len(proposals)} proposals")

        return proposals

    # ── Tier 2: Plan-Based Proposals ───────────────────────

    def _propose_tier2(
        self,
        user_message: str,
        entity_context: str,
        user=None,
        ifc_file=None,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        skill_examples: list[dict] | None = None,
        escalation_hint: str | None = None,
    ) -> ModificationProposal:
        """
        Handle a Tier 2 request: generate plan, validate, create proposal.

        The plan is stored in intent_json. Execution iterates over steps.

        ``escalation_hint`` carries the Tier 1 validation error when this
        path was reached via T1 escalation. It is forwarded to the planner
        so the LLM can self-correct (wrong property name, SET vs ADD).
        """
        emitter = emitter or NullEmitter()

        # 1. Generate execution plan
        emitter.emit("plan", "running", "Generating execution plan…")
        try:
            plan = self.t2_planner.generate_plan(
                user_message,
                entity_context,
                escalation_hint=escalation_hint,
            )
        except PlanGenerationError as e:
            emitter.emit("plan", "error", f"Plan generation failed: {e}")
            raise ModificationError(f"Could not generate plan: {e}")

        # Escalation: planner decided this needs Tier 3
        if plan.get("tier") == 3:
            emitter.emit(
                "plan", "done", "Escalating to Tier 3 code generation…", {"escalated": True}
            )
            return self._propose_tier3(
                user_message,
                entity_context,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                skill_examples=skill_examples,
            )

        # 2. Confidence check
        confidence = plan.get("confidence", 0)
        if confidence < 60:
            raise ModificationError(
                f"Low confidence ({confidence}%). Please be more specific about what you need."
            )

        steps_count = len(plan.get("plan", []))
        emitter.emit(
            "plan",
            "done",
            f"Plan ready — {steps_count} step{'s' if steps_count != 1 else ''}",
            {"steps_count": steps_count, "confidence": confidence},
        )

        # 3. Validate the full plan
        emitter.emit("validate", "running", "Validating plan steps…")
        validation = self.t2_validator.validate_plan(plan)

        # Auto-fallback (parity with Tier 1): SET_PROPERTY → ADD_PROPERTY for
        # standard pset properties when the property is missing on entities.
        # validate_plan stops at the first failing step, so iterate up to
        # len(steps) times to cover plans where multiple steps need the swap.
        if not validation.valid:
            validation = self._t2_autofix_set_to_add(plan, validation)

        if not validation.valid:
            emitter.emit("validate", "error", validation.error)
            raise ModificationError(f"Plan validation failed: {validation.error}")

        emitter.emit(
            "validate",
            "done",
            f"{validation.total_affected} entit{'y' if validation.total_affected == 1 else 'ies'} affected",
            {"entities_count": validation.total_affected},
        )

        # 4. Build diff preview from all steps
        emitter.emit("diff", "running", "Building change preview…")
        diff_preview = self._build_tier2_diff_preview(plan, validation)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        # 5. Resolve IFC file from first step's entities
        if ifc_file is None:
            first_entities = validation.steps[0].entities
            if first_entities:
                ifc_file = first_entities[0].ifc_file
            else:
                raise ModificationError("Could not determine target IFC file.")

        # 6. Create proposal
        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=plan.get("explanation", ""),
            changes=plan,
            diff_preview=json.dumps(diff_preview),
            affected_count=validation.total_affected,
            status=ModificationProposal.Status.PENDING,
            tier=2,
            operation="PLAN",
            intent_json=plan,
            filter_spec={},  # plan has per-step filters
            confidence=confidence,
        )

        logger.info(
            f"Tier 2 proposal {proposal.id}: {len(plan['plan'])} steps, "
            f"{validation.total_affected} entities"
        )

        emitter.emit("guardian", "running", "Checking project documents…")
        try:
            GuardianService().check(proposal)
            emitter.emit(
                "guardian",
                "done",
                "Document check complete",
                {"verdict": proposal.verification_status},
            )
        except Exception as e:
            logger.warning(f"Guardian check failed (non-blocking): {e}")
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

        return proposal

    def _build_tier2_diff_preview(self, plan: dict, validation) -> list[dict]:
        """Build a combined diff preview for all steps in a Tier 2 plan."""
        preview = []

        for step, sv in zip(plan.get("plan", []), validation.steps):
            op = step.get("operation", "")
            params = step.get("params", {})
            step_num = step.get("step", sv.step_index + 1)

            for entity in sv.entities[:20]:  # Cap preview rows per step
                field_label = self._tier2_field_label(op, params)
                old_value = self._tier2_old_value(op, params, entity)
                new_value = self._tier2_new_value(op, params)

                preview.append(
                    {
                        "global_id": entity.global_id,
                        "name": entity.name or entity.global_id[:8],
                        "ifc_type": entity.ifc_type,
                        "field": f"[Step {step_num}] {field_label}",
                        "old_value": str(old_value),
                        "new_value": str(new_value),
                    }
                )

        return preview

    @staticmethod
    def _tier2_field_label(operation: str, params: dict) -> str:
        if operation in ("SET_PROPERTY", "ADD_PROPERTY", "REMOVE_PROPERTY"):
            return f"{params.get('pset', '')}.{params.get('property', '')}"
        if operation == "SET_ATTRIBUTE":
            return params.get("attribute", "")
        if operation in ("ADD_PSET", "REMOVE_PSET"):
            return params.get("pset_name", "")
        if operation == "SET_CLASSIFICATION":
            return f"{params.get('system_name', '')}: {params.get('reference', '')}"
        if operation == "SET_MATERIAL":
            return f"Material: {params.get('material_name', '')}"
        if operation == "COPY_PROPERTIES":
            return f"Copy from {params.get('source_name', '')} → {params.get('pset_name', '')}"
        return operation

    @staticmethod
    def _tier2_old_value(operation: str, params: dict, entity) -> str:
        if operation in ("SET_PROPERTY", "REMOVE_PROPERTY"):
            key = f"{params.get('pset', '')}.{params.get('property', '')}"
            return (entity.properties or {}).get(key, "(not set)")
        if operation == "SET_ATTRIBUTE":
            attr = params.get("attribute", "")
            if attr == "Name":
                return entity.name or "(none)"
            return "(attribute)"
        if operation in ("ADD_PROPERTY", "ADD_PSET", "SET_CLASSIFICATION", "SET_MATERIAL"):
            return "(none)"
        return "?"

    @staticmethod
    def _tier2_new_value(operation: str, params: dict) -> str:
        if operation in ("SET_PROPERTY", "ADD_PROPERTY", "SET_ATTRIBUTE"):
            return str(params.get("new_value", ""))
        if operation == "REMOVE_PROPERTY":
            return "(removed)"
        if operation == "REMOVE_PSET":
            return "(removed)"
        if operation == "ADD_PSET":
            props = params.get("properties", {})
            return f"{len(props)} properties"
        if operation == "SET_CLASSIFICATION":
            return params.get("reference", "")
        if operation == "SET_MATERIAL":
            return params.get("material_name", "")
        if operation == "COPY_PROPERTIES":
            return f"from {params.get('source_name', '')}"
        return "?"

    # ── Tier 3 ───────────────────────

    def _propose_tier3(
        self,
        user_message: str,
        entity_context: str,
        user=None,
        ifc_file=None,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        skill_examples: list[dict] | None = None,
    ) -> ModificationProposal:
        emitter = emitter or NullEmitter()

        emitter.emit("codegen", "running", "Generating IfcOpenShell code…")
        try:
            result = self.t3_planner.generate_code(
                user_message, entity_context, skill_examples=skill_examples
            )
        except CodeGenerationError as e:
            emitter.emit("codegen", "error", f"Code generation failed: {e}")
            raise ModificationError(f"Code generation failed: {e}")

        confidence = result.get("confidence", 0)
        emitter.emit(
            "codegen",
            "done",
            f"Code generated ({confidence}% confidence)",
            {"confidence": confidence, "code_len": len(result.get("code", ""))},
        )

        if confidence < 70:
            raise ModificationError(
                f"Low confidence ({confidence}%). "
                f"Tier 3 operations are complex — please be very specific "
                f"about entity types, names, and the exact operation needed. "
                f"LLM interpretation: {result.get('explanation', '?')}"
            )

        emitter.emit("review", "running", "Reviewing generated code…")
        try:
            review = self.t3_reviewer.review(
                user_message=user_message,
                code=result.get("code", ""),
                entity_context=entity_context,
            )
            result["review"] = review
            emitter.emit(
                "review",
                "done",
                f"Code review: {review.get('verdict', '?')}",
                {"verdict": review.get("verdict")},
            )
        except Exception as e:
            logger.warning(f"Tier3 code review failed (non-blocking): {e}")
            result["review"] = Tier3Reviewer._fallback()
            emitter.emit("review", "done", "Code review unavailable", {"verdict": "unknown"})

        # Resolve IFC file if not provided
        if ifc_file is None:
            from ifc_processor.models import IFCFile

            ifc_file = (
                IFCFile.objects.filter(project=self.project, status="completed")
                .order_by("-created_at")
                .first()
            )
            if ifc_file is None:
                raise ModificationError("No processed IFC file found in this project.")

        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=result.get("explanation", ""),
            changes=result,
            diff_preview=json.dumps([]),  # No entity-level preview for generated code
            affected_count=0,  # Unknown until execution
            status=ModificationProposal.Status.PENDING,
            tier=3,
            operation="CODE",
            intent_json=result,
            filter_spec={},
            confidence=confidence,
        )

        logger.info(
            f"Tier 3 proposal {proposal.id}: code length={len(result.get('code', ''))}, "
            f"confidence={confidence}%"
        )

        emitter.emit("guardian", "running", "Checking project documents…")
        try:
            GuardianService().check(proposal)
            emitter.emit(
                "guardian",
                "done",
                "Document check complete",
                {"verdict": proposal.verification_status},
            )
        except Exception as e:
            logger.warning(f"Guardian check failed (non-blocking): {e}")
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

        return proposal

    def _extract_guids_from_message(self, message: str) -> list[str]:
        """Extract IFC GlobalIds from a user message.

        Delegates to ``EntityNameResolver.extract_guids`` so the regex lives
        in one place. Kept as an instance method to preserve existing call
        sites (and to make patching easy in tests).
        """
        return EntityNameResolver.extract_guids(message)

    def _t2_autofix_set_to_add(self, plan: dict, validation):
        """
        Mirror Tier 1's SET_PROPERTY → ADD_PROPERTY fallback at Tier 2.

        ``validate_plan`` stops at the first invalid step. When that step is
        a SET_PROPERTY on a standard pset property that simply doesn't exist
        on the matched entities yet, swap it to ADD_PROPERTY in place and
        re-validate. Bounded loop because subsequent steps may need the same
        swap.
        """
        steps = plan.get("plan", []) or []
        if not steps:
            return validation

        for _ in range(len(steps)):
            if validation.valid:
                return validation
            if not validation.steps:
                return validation

            failed = validation.steps[-1]
            if failed.operation != "SET_PROPERTY":
                return validation
            if "not found on any" not in (failed.error or ""):
                return validation

            step = steps[failed.step_index]
            params = step.get("params", {}) or {}
            pset = params.get("pset", "")
            prop = params.get("property", "")
            if not pset or not prop:
                return validation
            if lookup_property(pset, prop) is None:
                return validation

            logger.info(
                "Auto-fallback (T2): SET_PROPERTY → ADD_PROPERTY "
                "for %s.%s (standard property, not yet on entities)",
                pset,
                prop,
            )
            step["operation"] = "ADD_PROPERTY"
            validation = self.t2_validator.validate_plan(plan)

        return validation

    def _execute_tier3(self, proposal: ModificationProposal) -> list[EntityChange]:
        code = proposal.intent_json.get("code", "")
        if not code:
            raise IFCWriteError("Proposal has no code to execute")

        ifc_path = proposal.ifc_file.file.path
        executor = Tier3Executor(ifc_path)

        try:
            changes = executor.execute(code)
        except Tier3ExecutionError as e:
            raise IFCWriteError(f"Tier 3 execution failed: {e}") from e

        # Update affected count now that we know
        proposal.affected_count = len(changes)
        proposal.save(update_fields=["affected_count"])

        return changes

    # ── V2 Pipeline (triage → slots → resolve → route) ─────────────

    def _propose_v2(
        self,
        user_message: str,
        user,
        ifc_file=None,
        message_obj=None,
        emitter: PipelineEmitter | None = None,
        failure_context: str | None = None,
    ) -> ModificationProposal:
        """V2 entry point: decomposed pipeline with deterministic tier choice.

        Stages:
          1. Triage     — segment the request (per-segment kind + phrases).
          2. Slots      — extract per-kind slot dicts.
          3. Resolve    — locate target entities, mode-aware per kind.
          3.5 Route     — deterministic tier selection (no LLM).
          4. Dispatch   — T1 single / T1 chain / T2 plan / T3 code.

        Replaces the FeasibilityChecker + IntentClassifier monolith.
        Behind a settings flag while the new prompts are being validated.
        """
        emitter = emitter or NullEmitter()

        # 0. Sanity check — same as V1.
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

        segments = self._v2_run_triage(user_message, emitter)
        self._v2_run_slot_extraction(segments, user_message, emitter)
        self._v2_run_target_resolution(segments, emitter)

        # 4. Route deterministically.
        emitter.emit("classify", "running", "Selecting execution tier…")
        routing = route_tier(segments)
        if routing.is_rejected:
            emitter.emit("classify", "error", routing.rejection_reason)
            self._raise_with_failure_record(
                ValueError(routing.rejection_reason),
                phase="VALIDATION",
                query_text=user_message,
                intent_json={"segments": segments},
            )
        emitter.emit(
            "classify",
            "done",
            f"Tier {routing.tier} — {routing.operation}",
            {"tier": routing.tier, "operation": routing.operation},
        )

        # 5. Dispatch.
        if routing.tier == 1:
            if routing.operation == "CHAIN":
                return self._v2_dispatch_t1_chain(
                    segments,
                    user_message,
                    user=user,
                    ifc_file=ifc_file,
                    message_obj=message_obj,
                    emitter=emitter,
                )
            return self._v2_dispatch_t1(
                segments[0],
                routing,
                user_message,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
            )

        if routing.tier == 2:
            return self._v2_dispatch_t2(
                segments,
                user_message,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
            )

        if routing.tier == 3:
            return self._v2_dispatch_t3(
                segments,
                user_message,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
            )

        raise ModificationError(
            f"V2 router returned unexpected tier: {routing.tier}"
        )

    # ── V2 Stages ─────────────────────────────────────────────────

    def _v2_run_triage(self, user_message: str, emitter: PipelineEmitter) -> list[dict]:
        """Stage 1 — invoke the triage classifier and return the segment list."""
        emitter.emit("triage", "running", "Understanding request…")
        try:
            triage_result = self.triage_classifier.classify(user_message)
        except TriageError as e:
            emitter.emit("triage", "error", str(e))
            self._raise_with_failure_record(
                ValueError(f"Could not understand request: {e}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=None,
            )
        segments = triage_result.segments
        suffix = "s" if len(segments) != 1 else ""
        emitter.emit(
            "triage",
            "done",
            f"{len(segments)} action segment{suffix} identified",
            {"segments": len(segments)},
        )
        return segments

    def _v2_run_slot_extraction(
        self,
        segments: list[dict],
        user_message: str,
        emitter: PipelineEmitter,
    ) -> None:
        """Stage 2 — extract per-kind slots, attach to each segment in place."""
        emitter.emit("extract", "running", "Extracting parameters…")
        for seg in segments:
            kind = seg.get("kind")
            if kind in ("OUT_OF_SCOPE", "UNCLEAR"):
                seg["slots"] = {}
                seg.setdefault("warnings", [])
                continue
            try:
                slot_result = self.slot_extractor.extract(seg, user_message)
            except SlotExtractionError as e:
                emitter.emit("extract", "error", str(e))
                self._raise_with_failure_record(
                    ValueError(f"Could not extract parameters: {e}"),
                    phase="VALIDATION",
                    query_text=user_message,
                    intent_json={"segments": segments},
                )
            seg["slots"] = slot_result.slots
            seg["warnings"] = slot_result.warnings
        emitter.emit("extract", "done", "Parameters extracted")

    def _v2_run_target_resolution(
        self,
        segments: list[dict],
        emitter: PipelineEmitter,
    ) -> None:
        """Stage 3 — resolve target entities per segment, mode-aware.

        EXISTING_TARGET for property/attribute/pset/delete/relationship.
        PARENT_TARGET for the parent_phrase of a CREATE segment, plus
        NEW_TARGET (an empty result by design) on the proposed names.
        OUT_OF_SCOPE / UNCLEAR are skipped — the router rejects them.
        """
        emitter.emit("resolve", "running", "Locating entities…")
        for seg in segments:
            kind = seg.get("kind")
            if kind in ("PROPERTY", "ATTRIBUTE", "PSET", "DELETE", "RELATIONSHIP"):
                target_phrase = (seg.get("target_phrase") or "").strip()
                seg["resolution"] = self.entity_resolver.resolve(
                    target_phrase, mode=MODE_EXISTING_TARGET
                )
            elif kind == "CREATE":
                slots = seg.get("slots") or {}
                parent_phrase = (slots.get("parent_phrase") or "").strip()
                if parent_phrase:
                    seg["parent_resolution"] = self.entity_resolver.resolve(
                        parent_phrase, mode=MODE_PARENT_TARGET
                    )
                else:
                    seg["parent_resolution"] = None
                seg["resolution"] = self.entity_resolver.resolve(
                    "", mode=MODE_NEW_TARGET
                )
            else:
                seg["resolution"] = None
        emitter.emit("resolve", "done", "Resolution complete")

    # ── V2 Dispatch — Tier 1 single ───────────────────────────────

    def _v2_dispatch_t1(
        self,
        segment: dict,
        routing: RoutingResult,
        user_message: str,
        user,
        ifc_file,
        message_obj,
        emitter: PipelineEmitter,
    ) -> ModificationProposal:
        """T1 single-segment path. Uses the deterministic intent assembler
        instead of running the legacy classifier prompt.
        """
        from .filter_builder import build_filter_spec

        intent = assemble_tier1_intent(segment, routing)
        resolution = segment.get("resolution")
        if resolution is None or resolution.is_empty:
            self._raise_with_failure_record(
                ValueError(
                    "Could not locate any entities matching the segment's "
                    f"target ({segment.get('target_phrase', '?')!r}). Try "
                    "naming the entity by GlobalId, full name, or an IFC "
                    "category like 'all walls'."
                ),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=intent,
            )

        emitter.emit("validate", "running", "Matching entities…")
        filter_spec = build_filter_spec(resolution)
        intent["filter"] = filter_spec
        try:
            matched_qs = self.filter_engine.resolve(filter_spec)
        except ValueError as e:
            raise ModificationError(str(e))
        matched_entities = list(matched_qs)

        validation = self.t1_validator.validate(intent, matched_entities)
        if not validation.valid:
            if "not found on any" not in validation.error and "missing on" not in validation.error:
                emitter.emit("validate", "error", validation.error)
                self._raise_with_failure_record(
                    ValueError(validation.error),
                    phase="VALIDATION",
                    query_text=user_message,
                    intent_json=intent,
                )
            # Custom-pset / property-not-found cases → escalate to T2.
            logger.info(
                "V2 Tier 1 validation failed, escalating to Tier 2: %s",
                validation.error,
            )
            emitter.emit("validate", "done", "Escalating to Tier 2 plan…", {"escalated": True})
            return self._v2_dispatch_t2(
                [segment],
                user_message,
                user=user,
                ifc_file=ifc_file,
                message_obj=message_obj,
                emitter=emitter,
                escalation_hint=validation.error,
            )

        emitter.emit(
            "validate",
            "done",
            f"{len(validation.entities)} entit{'y' if len(validation.entities) == 1 else 'ies'} matched",
            {"entities_count": len(validation.entities)},
        )

        emitter.emit("diff", "running", "Building change preview…")
        diff_preview = self._build_diff_preview(intent, validation.entities)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        if ifc_file is None:
            ifc_file = matched_entities[0].ifc_file

        all_warnings = list(segment.get("warnings") or []) + list(validation.warnings or [])
        explanation = intent.get("explanation", "")
        if all_warnings:
            warning_block = "\n".join(f"⚠ {w}" for w in all_warnings)
            explanation = f"{warning_block}\n\n{explanation}" if explanation else warning_block

        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=explanation,
            changes=intent,
            diff_preview=json.dumps(diff_preview),
            affected_count=len(validation.entities),
            status=ModificationProposal.Status.PENDING,
            tier=1,
            operation=intent.get("operation", ""),
            intent_json=intent,
            filter_spec=filter_spec,
            confidence=intent.get("confidence", 80),
        )

        logger.info(
            "V2 Tier 1 proposal %s: %s on %d entities",
            proposal.id,
            intent["operation"],
            len(validation.entities),
        )

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
        except Exception as e:
            logger.warning("Guardian check failed (non-blocking): %s", e)
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

        return proposal

    # ── V2 Dispatch — Tier 1 chain ────────────────────────────────

    def _v2_dispatch_t1_chain(
        self,
        segments: list[dict],
        user_message: str,
        user,
        ifc_file,
        message_obj,
        emitter: PipelineEmitter,
    ):
        """T1 chain: same target across segments, multiple operations.

        The shared entity set comes from segments[0]'s resolution. If
        targets differ across segments the router should have escalated
        to T2; we fall back to T2 here as a safety net.
        """
        if not segments:
            raise ModificationError("Empty chain — no segments to dispatch.")

        first_resolution = segments[0].get("resolution")
        for seg in segments[1:]:
            other = seg.get("resolution")
            if (
                other is None
                or first_resolution is None
                or {e.pk for e in (other.entities or [])}
                != {e.pk for e in (first_resolution.entities or [])}
            ):
                logger.info(
                    "V2 chain segments target different entity sets — escalating to T2."
                )
                return self._v2_dispatch_t2(
                    segments,
                    user_message,
                    user=user,
                    ifc_file=ifc_file,
                    message_obj=message_obj,
                    emitter=emitter,
                )

        # Build per-segment Tier 1 intents.
        intents = []
        for seg in segments:
            kind = seg.get("kind")
            op = "SET_ATTRIBUTE" if kind == "ATTRIBUTE" else "SET_PROPERTY"
            intents.append(
                assemble_tier1_intent(
                    seg, RoutingResult(tier=1, operation=op)
                )
            )

        chain_warnings: list[str] = []
        for seg in segments:
            chain_warnings.extend(seg.get("warnings") or [])

        return self._propose_chain(
            intents,
            user_message,
            user=user,
            ifc_file=ifc_file,
            message_obj=message_obj,
            entity_context="",
            emitter=emitter,
            skill_examples=[],
            resolution=first_resolution,
            pipeline_warnings=chain_warnings,
        )

    # ── V2 Dispatch — Tier 2 ──────────────────────────────────────

    def _v2_dispatch_t2(
        self,
        segments: list[dict],
        user_message: str,
        user,
        ifc_file,
        message_obj,
        emitter: PipelineEmitter,
        escalation_hint: str | None = None,
    ) -> ModificationProposal:
        """T2 path: assembles a plan directly from V2 segments — no
        Tier2Planner LLM call. Per-step ``filter`` is stamped from the
        segment's resolution; the rest mirrors ``_propose_tier2``.
        """
        from .filter_builder import build_filter_spec

        plan = assemble_tier2_intent(segments)
        plan_steps = plan.get("plan", [])
        if not plan_steps:
            self._raise_with_failure_record(
                ValueError(
                    "Could not build any executable steps from the request. "
                    "Please rephrase with concrete property names or pset names."
                ),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=plan,
            )

        # Stamp per-step filter_spec from the segment that contributed
        # the step. The assembler skips unsupported segment kinds, so we
        # iterate segments in order and pair them with the contiguous
        # step list.
        emitter.emit("plan", "running", "Building execution plan…")
        seg_iter = iter(seg for seg in segments if seg.get("kind") in ("PROPERTY", "ATTRIBUTE", "PSET"))
        for step in plan_steps:
            try:
                seg = next(seg_iter)
            except StopIteration:
                seg = None
            resolution = seg.get("resolution") if seg else None
            if resolution is None or resolution.is_empty:
                self._raise_with_failure_record(
                    ValueError(
                        f"Step {step.get('step', '?')}: could not locate any "
                        "entities matching the target. Please name the entity "
                        "or category."
                    ),
                    phase="VALIDATION",
                    query_text=user_message,
                    intent_json=plan,
                )
            step["filter"] = build_filter_spec(resolution)
        emitter.emit(
            "plan",
            "done",
            f"Plan ready — {len(plan_steps)} step{'s' if len(plan_steps) != 1 else ''}",
            {"steps_count": len(plan_steps)},
        )

        emitter.emit("validate", "running", "Validating plan steps…")
        validation = self.t2_validator.validate_plan(plan)
        if not validation.valid:
            validation = self._t2_autofix_set_to_add(plan, validation)
        if not validation.valid:
            emitter.emit("validate", "error", validation.error)
            self._raise_with_failure_record(
                ValueError(f"Plan validation failed: {validation.error}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=plan,
            )
        emitter.emit(
            "validate",
            "done",
            f"{validation.total_affected} entit"
            f"{'y' if validation.total_affected == 1 else 'ies'} affected",
            {"entities_count": validation.total_affected},
        )

        emitter.emit("diff", "running", "Building change preview…")
        diff_preview = self._build_tier2_diff_preview(plan, validation)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        if ifc_file is None:
            first_entities = validation.steps[0].entities if validation.steps else []
            if not first_entities:
                raise ModificationError("Could not determine target IFC file.")
            ifc_file = first_entities[0].ifc_file

        # Surface segment-level slot warnings on the proposal.
        warnings: list[str] = []
        for seg in segments:
            warnings.extend(seg.get("warnings") or [])
        explanation = plan.get("explanation", "")
        if escalation_hint:
            explanation = f"⚠ Escalated from Tier 1: {escalation_hint}\n\n{explanation}"
        if warnings:
            block = "\n".join(f"⚠ {w}" for w in warnings)
            explanation = f"{block}\n\n{explanation}" if explanation else block

        proposal = ModificationProposal.objects.create(
            message=message_obj,
            ifc_file=ifc_file,
            created_by=user,
            request_text=user_message,
            explanation=explanation,
            changes=plan,
            diff_preview=json.dumps(diff_preview),
            affected_count=validation.total_affected,
            status=ModificationProposal.Status.PENDING,
            tier=2,
            operation="PLAN",
            intent_json=plan,
            filter_spec={},
            confidence=plan.get("confidence", 80),
        )

        logger.info(
            "V2 Tier 2 proposal %s: %d steps, %d entities",
            proposal.id,
            len(plan_steps),
            validation.total_affected,
        )

        emitter.emit("guardian", "running", "Checking project documents…")
        try:
            GuardianService().check(proposal)
            emitter.emit(
                "guardian",
                "done",
                "Document check complete",
                {"verdict": proposal.verification_status},
            )
        except Exception as e:
            logger.warning("Guardian check failed (non-blocking): %s", e)
            emitter.emit("guardian", "done", "Document check unavailable", {"verdict": "failed"})

        return proposal

    # ── V2 Dispatch — Tier 3 ──────────────────────────────────────

    def _v2_dispatch_t3(
        self,
        segments: list[dict],
        user_message: str,
        user,
        ifc_file,
        message_obj,
        emitter: PipelineEmitter,
    ) -> ModificationProposal:
        """T3 path: feeds the existing Tier3Planner with V2-derived
        structured inputs (existing parents, proposed names, entity
        class, deletion targets). Code generation, review, sandbox,
        and proposal creation reuse the V1 helpers.
        """
        t3_inputs = derive_tier3_inputs(segments)
        entity_context = self._v2_build_t3_entity_context(t3_inputs)
        return self._propose_tier3(
            user_message,
            entity_context,
            user=user,
            ifc_file=ifc_file,
            message_obj=message_obj,
            emitter=emitter,
            skill_examples=[],
        )

    def _v2_build_t3_entity_context(self, t3_inputs: dict) -> str:
        """Render the structured T3 inputs as a compact text block the
        existing Tier3Planner prompt can consume.

        The Tier3Planner accepts ``entity_context`` as free-form text;
        it does not parse a fixed schema. By labelling the V2 sections
        explicitly (EXISTING_PARENTS / PROPOSED_NAMES / ENTITY_CLASS /
        DELETE_TARGETS) the LLM has a clear, deterministic anchor to
        target without relying on the resolver's brittle category
        descriptions.
        """
        lines: list[str] = []
        entity_class = t3_inputs.get("entity_class", "")
        if entity_class:
            lines.append(f"ENTITY_CLASS: {entity_class}")

        proposed_names = t3_inputs.get("proposed_names") or []
        if proposed_names:
            lines.append("PROPOSED_NAMES:")
            for name in proposed_names:
                lines.append(f"  - {name}")

        existing_parents = t3_inputs.get("existing_parents") or []
        if existing_parents:
            lines.append("EXISTING_PARENTS:")
            for p in existing_parents:
                lines.append(
                    f"  - {p.get('name', '')!r} (global_id={p.get('global_id', '')}, "
                    f"ifc_type={p.get('ifc_type', '')})"
                )

        targets_to_delete = t3_inputs.get("targets_to_delete") or []
        if targets_to_delete:
            lines.append("DELETE_TARGETS:")
            for t in targets_to_delete:
                lines.append(
                    f"  - {t.get('name', '')!r} (global_id={t.get('global_id', '')}, "
                    f"ifc_type={t.get('ifc_type', '')})"
                )

        if not lines:
            return "(no structured V2 context — proceed from user_message alone)"
        return "\n".join(lines)
