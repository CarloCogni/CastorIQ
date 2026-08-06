# writeback/services/proposal_pipeline.py
"""
The writeback proposal pipeline — the single stage-walking loop.

Stages:
    1. Triage     — segment the request (per-segment kind + phrases).
    2. Slots      — extract per-kind slot dicts.
    3. Resolve    — locate target entities, mode-aware per kind.
    3.5 Route     — deterministic tier selection (no LLM).
    4. Dispatch   — T1 single / T2 plan / T3 code.

The pipeline decides *what* should change and returns a
:class:`PipelineOutcome`; it never writes to the database. Persisting the
outcome (and running Guardian) belongs to
:class:`~writeback.services.proposal_service.ProposalService`.

``route_request`` is shared by :meth:`ProposalPipeline.run` and the
``dry_run_v2_pipeline`` management command, so the stage walk exists in
exactly one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ifc_processor.models import IFCEntity

from .diff_renderer import render_diff
from .emitters import NullEmitter, PipelineEmitter
from .entity_resolver import (
    MODE_EXISTING_TARGET,
    MODE_NEW_TARGET,
    MODE_PARENT_TARGET,
    EntityNameResolver,
)
from .errors import ModificationError
from .filter_engine import FilterEngine
from .hint_generator import HintGenerator
from .intent_assembler import (
    assemble_tier1_intent,
    assemble_tier2_intent,
    derive_tier3_inputs,
)
from .journal_builder import JournalBuilder, JournalBuildError
from .llm_boundary import BoundaryError
from .slot_extractor import SlotExtractionError, SlotExtractor
from .t3_op_planner import T3OpPlanner
from .tier1_validator import Tier1Validator
from .tier2_validator import Tier2Validator
from .tier3_planner import CodeGenerationError, Tier3Planner
from .tier3_reviewer import Tier3Reviewer
from .tier_router import RequestRejectedError, RoutingResult
from .tier_router import route as route_tier
from .triage_classifier import TriageClassifier, TriageError

logger = logging.getLogger(__name__)


@dataclass
class PipelineOutcome:
    """What the pipeline decided, ready to be persisted as a proposal.

    ``changes`` is the payload stored on ``ModificationProposal.changes`` —
    a serialized ``MutationJournal`` when ``is_journal`` is True (the
    executor then routes to the journal path), otherwise the legacy intent
    or plan dict.
    """

    tier: int
    operation: str
    changes: dict
    intent_json: dict
    diff_preview: list[dict]
    filter_spec: dict
    affected_count: int
    explanation: str
    confidence: int
    ifc_file: object
    is_journal: bool = False
    warnings: list[str] = field(default_factory=list)


class ProposalPipeline:
    """Runs the LLM stages and produces a :class:`PipelineOutcome`."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.user = user
        self.filter_engine = FilterEngine(project)
        self.entity_resolver = EntityNameResolver(
            project, user=user, filter_engine=self.filter_engine
        )
        self.t1_validator = Tier1Validator()
        self.t2_validator = Tier2Validator(project)
        self.t3_planner = Tier3Planner(user=user)
        self.t3_reviewer = Tier3Reviewer(user=user)
        self.t3_op_planner = T3OpPlanner(user=user)
        # Why the last T3 request fell back to code generation, surfaced on
        # the proposal so a human can see why they're reading Python.
        self._last_t3_fallback_reason = ""
        # Cheap to construct; the LLM is not invoked until a stage runs.
        self.triage_classifier = TriageClassifier(user=user)
        self.slot_extractor = SlotExtractor(user=user)
        self.hint_generator = HintGenerator(project, user=user)
        self.journal_builder = JournalBuilder(project, user=user)

    # ── Entry point ────────────────────────────────────────

    def run(
        self,
        user_message: str,
        *,
        user,
        ifc_file=None,
        emitter: PipelineEmitter | None = None,
        retry_of=None,
    ) -> PipelineOutcome:
        """Walk the stages and return the outcome to persist.

        Multi-segment requests route to a T2 plan (the router decides), so
        there is no T1-chain branch — ``ModificationProposal.message`` is
        unique-per-row and chain proposals would IntegrityError.

        Raises:
            ModificationError on rejection or any stage failure (carrying a
            ``failure_record_id`` when a FailureRecord was written).
        """
        emitter = emitter or NullEmitter()
        segments, routing = self.route_request(user_message, emitter=emitter, retry_of=retry_of)

        if routing.tier == 1:
            return self._dispatch_t1(
                segments[0], routing, user_message, user=user, ifc_file=ifc_file, emitter=emitter
            )
        if routing.tier == 2:
            return self._dispatch_t2(
                segments, user_message, user=user, ifc_file=ifc_file, emitter=emitter
            )
        if routing.tier == 3:
            return self._dispatch_t3(
                segments, user_message, user=user, ifc_file=ifc_file, emitter=emitter
            )

        raise ModificationError(f"V2 router returned unexpected tier: {routing.tier}")

    def route_request(
        self,
        user_message: str,
        *,
        emitter: PipelineEmitter | None = None,
        retry_of=None,
    ) -> tuple[list[dict], RoutingResult]:
        """Run stages 1–3.5 and return the decorated segments + routing.

        Shared by :meth:`run` and the dry-run command so the stage walk is
        implemented exactly once.
        """
        emitter = emitter or NullEmitter()

        # 0. Sanity check — at least one processed IFC entity must exist.
        has_entities = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status="completed",
        ).exists()
        if not has_entities:
            raise ModificationError(
                "No processed IFC entities found in this project. "
                "Please upload and process an IFC file first."
            )

        retry_stage, prior_errors = self._retry_prior_errors(retry_of)
        triage_priors = prior_errors if retry_stage == "triage" else ()

        segments = self._run_triage(user_message, emitter, prior_errors=triage_priors)
        self._run_slot_extraction(
            segments, user_message, emitter, prior_errors=prior_errors, retry_stage=retry_stage
        )
        self._run_target_resolution(segments, emitter)

        emitter.emit("classify", "running", "Selecting execution tier…")
        routing = route_tier(segments)
        if routing.is_rejected:
            full_reason = self._compose_rejection_message(routing)
            emitter.emit("classify", "error", full_reason)
            self._raise_with_failure_record(
                RequestRejectedError(full_reason, category=routing.rejection_category),
                phase="VALIDATION",
                query_text=user_message,
                intent_json={"segments": self._segments_to_jsonable(segments)},
            )
        emitter.emit(
            "classify",
            "done",
            f"Tier {routing.tier} — {routing.operation}",
            {"tier": routing.tier, "operation": routing.operation},
        )
        return segments, routing

    # ── Stages ─────────────────────────────────────────────

    def _run_triage(
        self,
        user_message: str,
        emitter: PipelineEmitter,
        prior_errors: tuple[BoundaryError, ...] = (),
    ) -> list[dict]:
        """Stage 1 — invoke the triage classifier and return the segment list."""
        emitter.emit("triage", "running", "Understanding request…")
        try:
            triage_result = self.triage_classifier.classify(user_message, prior_errors=prior_errors)
        except TriageError as e:
            emitter.emit("triage", "error", str(e))
            self._raise_with_failure_record(
                ValueError(f"Could not understand request: {e}"),
                phase="VALIDATION",
                query_text=user_message,
                intent_json=None,
                boundary_errors=e.boundary_errors,
                stage="triage",
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

    def _run_slot_extraction(
        self,
        segments: list[dict],
        user_message: str,
        emitter: PipelineEmitter,
        prior_errors: tuple[BoundaryError, ...] = (),
        retry_stage: str | None = None,
    ) -> None:
        """Stage 2 — extract per-kind slots, attach to each segment in place.

        On a retry, ``prior_errors`` describe one failed stage (e.g.
        ``slots:pset``); they are fed back only to segments of that kind —
        another kind's extractor was never asked for those fields and would
        only be confused by them.
        """
        emitter.emit("extract", "running", "Extracting parameters…")
        for seg in segments:
            kind = seg.get("kind")
            if kind in ("OUT_OF_SCOPE", "UNCLEAR"):
                seg["slots"] = {}
                seg.setdefault("warnings", [])
                continue
            seg_stage = f"slots:{(kind or '').lower()}"
            seg_priors = prior_errors if seg_stage == retry_stage else ()
            try:
                slot_result = self.slot_extractor.extract(
                    seg, user_message, prior_errors=seg_priors
                )
            except SlotExtractionError as e:
                emitter.emit("extract", "error", str(e))
                self._raise_with_failure_record(
                    ValueError(f"Could not extract parameters: {e}"),
                    phase="VALIDATION",
                    query_text=user_message,
                    intent_json={"segments": self._segments_to_jsonable(segments)},
                    boundary_errors=e.boundary_errors,
                    stage=f"slots:{(kind or '').lower()}",
                )
            seg["slots"] = slot_result.slots
            seg["warnings"] = slot_result.warnings
        emitter.emit("extract", "done", "Parameters extracted")

    def _run_target_resolution(
        self,
        segments: list[dict],
        emitter: PipelineEmitter,
    ) -> None:
        """Stage 3 — resolve target entities per segment, mode-aware.

        EXISTING_TARGET for property/attribute/pset/delete/relationship.
        PARENT_TARGET for the parent_phrase of a CREATE segment and the
        destination_phrase of a RELATIONSHIP, plus NEW_TARGET (an empty
        result by design) on the proposed names.
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
                if kind == "RELATIONSHIP":
                    # Resolve where it moves TO, so an impossible destination
                    # is caught now rather than after the user approves.
                    destination_phrase = (
                        (seg.get("slots") or {}).get("destination_phrase") or ""
                    ).strip()
                    seg["destination_resolution"] = (
                        self.entity_resolver.resolve(destination_phrase, mode=MODE_PARENT_TARGET)
                        if destination_phrase
                        else None
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
                seg["resolution"] = self.entity_resolver.resolve("", mode=MODE_NEW_TARGET)
            else:
                seg["resolution"] = None
        emitter.emit("resolve", "done", "Resolution complete")

    # ── Dispatch — Tier 1 ──────────────────────────────────

    def _dispatch_t1(
        self,
        segment: dict,
        routing: RoutingResult,
        user_message: str,
        *,
        user,
        ifc_file,
        emitter: PipelineEmitter,
    ) -> PipelineOutcome:
        """T1 single-segment path, using the deterministic intent assembler."""
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
        if not matched_entities:
            # The resolver's snapshot can go stale (file re-processed or
            # deleted between resolution and dispatch) — refuse cleanly.
            raise ModificationError(
                f"No entities match the resolved filter {filter_spec}. "
                "The model may have changed since the request — please try again."
            )

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
            logger.info("V2 Tier 1 validation failed, escalating to Tier 2: %s", validation.error)
            emitter.emit("validate", "done", "Escalating to Tier 2 plan…", {"escalated": True})
            return self._dispatch_t2(
                [segment],
                user_message,
                user=user,
                ifc_file=ifc_file,
                emitter=emitter,
                escalation_hint=validation.error,
            )

        emitter.emit(
            "validate",
            "done",
            f"{len(validation.entities)} entit"
            f"{'y' if len(validation.entities) == 1 else 'ies'} matched",
            {"entities_count": len(validation.entities)},
        )

        if ifc_file is None:
            ifc_file = matched_entities[0].ifc_file

        emitter.emit("diff", "running", "Building change preview…")
        try:
            journal = self.journal_builder.build_t1(intent, validation.entities, ifc_file)
        except JournalBuildError as e:
            emitter.emit("diff", "error", str(e))
            self._raise_with_failure_record(
                e,
                phase="VALIDATION",
                query_text=user_message,
                intent_json=intent,
            )
        diff_preview = render_diff(journal)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        all_warnings = list(segment.get("warnings") or []) + list(validation.warnings or [])
        explanation = self._with_warnings(intent.get("explanation", ""), all_warnings)

        return PipelineOutcome(
            tier=1,
            operation=intent.get("operation", ""),
            changes=journal.to_json_dict(),
            intent_json=intent,
            diff_preview=diff_preview,
            filter_spec=filter_spec,
            affected_count=len(validation.entities),
            explanation=explanation,
            confidence=intent.get("confidence", 80),
            ifc_file=ifc_file,
            is_journal=True,
            warnings=all_warnings,
        )

    # ── Dispatch — Tier 2 ──────────────────────────────────

    def _dispatch_t2(
        self,
        segments: list[dict],
        user_message: str,
        *,
        user,
        ifc_file,
        emitter: PipelineEmitter,
        escalation_hint: str | None = None,
    ) -> PipelineOutcome:
        """T2 path: assembles a plan directly from V2 segments.

        No LLM authors the plan — each segment becomes one step and every
        step's ``filter`` is stamped from that segment's resolution.
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
        seg_iter = iter(
            seg for seg in segments if seg.get("kind") in ("PROPERTY", "ATTRIBUTE", "PSET")
        )
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

        if ifc_file is None:
            first_entities = validation.steps[0].entities if validation.steps else []
            if not first_entities:
                raise ModificationError("Could not determine target IFC file.")
            ifc_file = first_entities[0].ifc_file

        emitter.emit("diff", "running", "Building change preview…")
        try:
            journal = self.journal_builder.build_t2(plan, validation, ifc_file)
        except JournalBuildError as e:
            emitter.emit("diff", "error", str(e))
            self._raise_with_failure_record(
                e,
                phase="VALIDATION",
                query_text=user_message,
                intent_json=plan,
            )
        diff_preview = render_diff(journal)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        # Surface segment-level slot warnings on the proposal.
        warnings: list[str] = []
        for seg in segments:
            warnings.extend(seg.get("warnings") or [])
        explanation = plan.get("explanation", "")
        if escalation_hint:
            explanation = f"⚠ Escalated from Tier 1: {escalation_hint}\n\n{explanation}"
        explanation = self._with_warnings(explanation, warnings)

        return PipelineOutcome(
            tier=2,
            operation="PLAN",
            changes=journal.to_json_dict(),
            intent_json=plan,
            diff_preview=diff_preview,
            filter_spec={},
            affected_count=validation.total_affected,
            explanation=explanation,
            confidence=plan.get("confidence", 80),
            ifc_file=ifc_file,
            is_journal=True,
            warnings=warnings,
        )

    # ── Dispatch — Tier 3 ──────────────────────────────────

    def _dispatch_t3(
        self,
        segments: list[dict],
        user_message: str,
        *,
        user,
        ifc_file,
        emitter: PipelineEmitter,
    ) -> PipelineOutcome:
        """T3 path — typed ops first, generated code as the fallback.

        The LLM is asked to pick from a closed set of pre-coded operations.
        Only when it cannot express the request that way (or the ops are
        ungrounded) do we fall back to generating Python, which then runs
        behind the sandbox and the human code-review gate.
        """
        t3_inputs = derive_tier3_inputs(segments)
        entity_context = self._build_t3_entity_context(t3_inputs)

        if ifc_file is None:
            ifc_file = self._latest_completed_ifc_file()

        outcome = self._try_t3_ops(user_message, t3_inputs, entity_context, ifc_file, emitter)
        if outcome is not None:
            return outcome
        fallback_reason = self._last_t3_fallback_reason

        return self._dispatch_t3_code(
            user_message,
            entity_context,
            ifc_file=ifc_file,
            emitter=emitter,
            fallback_reason=fallback_reason,
        )

    def _try_t3_ops(
        self,
        user_message: str,
        t3_inputs: dict,
        entity_context: str,
        ifc_file,
        emitter: PipelineEmitter,
    ) -> PipelineOutcome | None:
        """Attempt the typed-op path. Returns None to fall back to code."""
        emitter.emit("plan", "running", "Planning entity operations…")
        plan = self.t3_op_planner.plan(user_message, t3_inputs, entity_context)

        if not plan.is_usable:
            self._last_t3_fallback_reason = plan.reason or "The typed operations did not fit."
            emitter.emit("plan", "done", "Falling back to generated code", {"typed_ops": False})
            return None

        try:
            journal = self.journal_builder.build_t3(list(plan.ops), ifc_file)
        except JournalBuildError as e:
            # The builder re-grounds every id against the DB; a failure here
            # means the plan referenced something real-looking but absent.
            logger.info("T3 typed ops rejected at build time — falling back to code: %s", e)
            self._last_t3_fallback_reason = str(e)
            emitter.emit("plan", "done", "Falling back to generated code", {"typed_ops": False})
            return None

        emitter.emit(
            "plan",
            "done",
            f"{len(journal.mutations)} operation{'s' if len(journal.mutations) != 1 else ''} planned",
            {"typed_ops": True, "ops": len(journal.mutations)},
        )

        emitter.emit("diff", "running", "Building change preview…")
        diff_preview = render_diff(journal)
        emitter.emit("diff", "done", "Preview ready", {"rows": len(diff_preview)})

        return PipelineOutcome(
            tier=3,
            operation="OPS",
            changes=journal.to_json_dict(),
            # No "code" key, ever — this is what keeps the ack gate off a
            # typed proposal while still catching anything that carries code.
            intent_json={
                "tier": 3,
                "operation": "OPS",
                "ops": list(plan.ops),
                "explanation": plan.explanation,
                "confidence": plan.confidence,
            },
            diff_preview=diff_preview,
            filter_spec={},
            affected_count=len(journal.mutations),
            explanation=self._with_warnings(plan.explanation, list(plan.warnings)),
            confidence=plan.confidence,
            ifc_file=ifc_file,
            is_journal=True,
            warnings=list(plan.warnings),
        )

    def _latest_completed_ifc_file(self):
        from ifc_processor.models import IFCFile

        ifc_file = (
            IFCFile.objects.filter(project=self.project, status="completed")
            .order_by("-created_at")
            .first()
        )
        if ifc_file is None:
            raise ModificationError("No processed IFC file found in this project.")
        return ifc_file

    def _dispatch_t3_code(
        self,
        user_message: str,
        entity_context: str,
        *,
        ifc_file,
        emitter: PipelineEmitter,
        fallback_reason: str = "",
    ) -> PipelineOutcome:
        """Generate IfcOpenShell code — the Tier 3 escape hatch."""
        emitter.emit("codegen", "running", "Generating IfcOpenShell code…")
        try:
            result = self.t3_planner.generate_code(user_message, entity_context, skill_examples=[])
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
        except Exception as e:  # noqa: BLE001 — review is advisory, never blocks
            logger.warning(f"Tier3 code review failed (non-blocking): {e}")
            result["review"] = Tier3Reviewer._fallback()
            emitter.emit("review", "done", "Code review unavailable", {"verdict": "unknown"})

        if ifc_file is None:
            ifc_file = self._latest_completed_ifc_file()

        warnings: list[str] = []
        if fallback_reason:
            warnings.append(
                f"Generated code was used because the typed operations could not "
                f"express this request: {fallback_reason}"
            )

        # Wrap the code in a journal so it inherits the fingerprint pin and
        # the atomic temp-copy/replace envelope. `changes` still carries the
        # code (via params) and `intent_json` keeps the shape the template,
        # the reviewer panel and the ack gate already read.
        #
        # A build failure used to degrade to a bare-code proposal executed by
        # the legacy Tier 3 path. That path is gone, so degrading would mint a
        # proposal nothing can execute — fail here instead.
        try:
            journal = self.journal_builder.build_t3_code(
                result.get("code", ""),
                ifc_file,
                explanation=result.get("explanation", ""),
            )
        except JournalBuildError as e:
            emitter.emit("diff", "error", str(e))
            self._raise_with_failure_record(
                e,
                phase="VALIDATION",
                query_text=user_message,
                intent_json=result,
            )
        changes: dict = journal.to_json_dict()

        return PipelineOutcome(
            tier=3,
            operation="CODE",
            changes=changes,
            intent_json=result,
            diff_preview=[],  # Effects are unknowable until the code runs
            filter_spec={},
            affected_count=0,  # Unknown until execution
            explanation=self._with_warnings(result.get("explanation", ""), warnings),
            confidence=confidence,
            ifc_file=ifc_file,
            is_journal=True,
            warnings=warnings,
        )

    def _build_t3_entity_context(self, t3_inputs: dict) -> str:
        """Render the structured T3 inputs as a compact text block the
        Tier3Planner prompt can consume.

        The planner accepts ``entity_context`` as free-form text; it does
        not parse a fixed schema. Labelling the V2 sections explicitly
        (EXISTING_PARENTS / PROPOSED_NAMES / ENTITY_CLASS / DELETE_TARGETS)
        gives the LLM a deterministic anchor instead of relying on the
        resolver's brittle category descriptions.
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

        move_targets = t3_inputs.get("move_targets") or []
        if move_targets:
            lines.append("MOVE_TARGETS:")
            for t in move_targets:
                lines.append(
                    f"  - {t.get('name', '')!r} (global_id={t.get('global_id', '')}, "
                    f"ifc_type={t.get('ifc_type', '')})"
                )

        destination = t3_inputs.get("destination") or []
        if destination:
            lines.append("DESTINATION:")
            for d in destination:
                lines.append(
                    f"  - {d.get('name', '')!r} (global_id={d.get('global_id', '')}, "
                    f"ifc_type={d.get('ifc_type', '')})"
                )

        if not lines:
            return "(no structured V2 context — proceed from user_message alone)"
        return "\n".join(lines)

    # ── Internals ──────────────────────────────────────────

    @staticmethod
    def _with_warnings(explanation: str, warnings: list[str]) -> str:
        """Prefix an explanation with a ⚠ block for each slot/validation warning."""
        if not warnings:
            return explanation
        block = "\n".join(f"⚠ {w}" for w in warnings)
        return f"{block}\n\n{explanation}" if explanation else block

    def _t2_autofix_set_to_add(self, plan: dict, validation):
        """
        Mirror Tier 1's SET_PROPERTY → ADD_PROPERTY fallback at Tier 2.

        ``validate_plan`` stops at the first invalid step. When that step is
        a SET_PROPERTY on a standard pset property that simply doesn't exist
        on the matched entities yet, swap it to ADD_PROPERTY in place and
        re-validate. Bounded loop because subsequent steps may need the same
        swap.
        """
        from ifc_processor.services.ifc_standard_psets import lookup_property

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

    def _compose_rejection_message(self, routing: RoutingResult) -> str:
        """Append a HintGenerator suggestion to the router's rejection reason.

        The hint is best-effort: any exception from the generator is
        swallowed so a hint failure can never block the user-facing
        rejection. Returns the plain rejection_reason if no hint comes back.
        """
        try:
            hint = self.hint_generator.suggest(
                reason_category=routing.rejection_category,
                payload=routing.rejection_payload,
            )
        except Exception as e:  # noqa: BLE001 — hint must never block rejection
            logger.warning("HintGenerator raised; using bare rejection: %s", e)
            return routing.rejection_reason
        if hint.is_empty:
            return routing.rejection_reason
        return f"{routing.rejection_reason} {hint.text}".strip()

    @staticmethod
    def _segments_to_jsonable(segments: list[dict]) -> list[dict]:
        """Strip non-serializable fields from V2 segments before storing
        them on a FailureRecord.

        The stages decorate each segment with rich runtime objects:
        ``ResolutionResult`` dataclasses (carrying live ``IFCEntity`` model
        instances) under ``resolution`` / ``parent_resolution``. Django's
        JSONField cannot persist any of those. This preserves only the keys
        whose values are guaranteed JSON-serialisable — the slots dict
        carries the user-facing intent, which is what failure analysis needs.
        """
        allowed = ("kind", "target_phrase", "slots", "warnings")
        clean: list[dict] = []
        for seg in segments or ():
            if not isinstance(seg, dict):
                continue
            clean.append({k: seg.get(k) for k in allowed if k in seg})
        return clean

    @staticmethod
    def _retry_prior_errors(retry_of) -> tuple[str | None, tuple]:
        """Decode a retry FailureRecord into (failed_stage, prior_errors).

        Reads the structured boundary errors stored on the record's
        ``intent_json`` by a prior ``_raise_with_failure_record`` call.
        Returns ``(None, ())`` when there is nothing to replay.
        """
        if retry_of is None:
            return None, ()
        payload = getattr(retry_of, "intent_json", None) or {}
        stage = payload.get("stage")
        rows = payload.get("boundary_errors") or []
        errors = tuple(
            BoundaryError(
                code=str(row.get("code", "")),
                path=str(row.get("path", "")),
                hint=str(row.get("hint", "")),
            )
            for row in rows
            if isinstance(row, dict)
        )
        return stage, errors

    def _raise_with_failure_record(
        self,
        exc: Exception,
        phase: str,
        query_text: str,
        intent_json: dict | None,
        proposal=None,
        boundary_errors: list[dict] | None = None,
        stage: str | None = None,
    ) -> None:
        """
        Create a FailureRecord for exc, then raise ModificationError with its ID.

        Args:
            exc:        The original exception that caused the failure.
            phase:      Pipeline phase — "VALIDATION", "EXECUTION", or "SANDBOX".
            query_text: The user's original query string.
            intent_json: Parsed intent if available; None for very early failures.
                        Caller is responsible for ensuring serializability —
                        for V2 segments use ``_segments_to_jsonable``.
            proposal:   Associated ModificationProposal, if one was created.
            boundary_errors: Structured ``{code, path, hint}`` rows from the
                        LLM boundary, stored so a retry can feed them back.
            stage:      The pipeline stage that failed (e.g. "triage",
                        "slots:property"), stored alongside boundary_errors so
                        the retry knows which stage to inject them into.

        Raises:
            ModificationError always.
        """
        from metacastor.services.failure_classifier import create_failure_record

        if boundary_errors:
            intent_json = dict(intent_json or {})
            intent_json["boundary_errors"] = boundary_errors
            intent_json["stage"] = stage

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
