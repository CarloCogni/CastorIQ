# writeback/services/t3_op_planner.py
"""
Stage 4 (RED) — turn a request into typed entity-lifecycle ops.

This is the pivot away from "the LLM writes IfcOpenShell code". The model
now picks from a closed set of pre-coded operations and fills in their
parameters; the code that actually creates or deletes entities lives in
``Tier3Writer``, written and tested once.

Two guards make a hallucinated target structurally impossible:

1. The pydantic schema allow-lists the creatable IFC classes.
2. :meth:`T3OpPlanner._ground` rejects any GlobalId the deterministic V2
   stages did not already resolve from the database — the planner may only
   re-emit identifiers it was shown.

``plan()`` never raises. When the ops can't express the request (or the
model can't produce a valid plan), it returns ``cannot_express=True`` and
the caller falls back to code generation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from core.llm import get_llm
from ifc_processor.services.tier3_writer import CREATABLE_CLASSES

from .llm_boundary import BoundaryError, BoundaryValidationError, call_structured
from .schemas import T3OpPlan

logger = logging.getLogger(__name__)

#: Hard bound on planning rounds: the initial attempt plus one repair round
#: when the model references entities the pipeline never resolved.
_MAX_GROUNDING_ATTEMPTS = 2


T3_OPS_SYSTEM_PROMPT = """\
You convert an IFC modification request into TYPED OPERATIONS.

You do NOT write code. You choose operations from a fixed set and fill in
their parameters. Pre-coded, tested handlers execute them.

Available operations:

CREATE_ENTITY — create ONE non-physical entity.
{
  "op": "CREATE_ENTITY",
  "ifc_class": "IfcZone | IfcSpace | IfcGroup | IfcMaterial | IfcClassification",
  "name": "<name, copied from the user's wording>",
  "long_name": "<optional IFC LongName>",
  "description": "<optional>",
  "parent_global_id": "<GlobalId from EXISTING_PARENTS, or empty>",
  "parent_relation": "aggregate | none",
  "member_global_ids": ["<GlobalId>", ...]
}

DELETE_ENTITY — delete ONE existing entity.
{"op": "DELETE_ENTITY", "global_id": "<GlobalId from DELETE_TARGETS>"}

ASSIGN_RELATIONSHIP — move ONE existing entity into a different storey.
{"op": "ASSIGN_RELATIONSHIP",
 "global_id": "<GlobalId from MOVE_TARGETS>",
 "destination_global_id": "<GlobalId from DESTINATION>",
 "relation": "container"}

Output schema (JSON only, no markdown):
{
  "ops": [ ... ],
  "cannot_express": false,
  "cannot_express_reason": "",
  "explanation": "<one sentence describing the whole change>",
  "confidence": <0-100>
}

HARD RULES:
  - Emit ONE op per entity. To create three zones, emit three CREATE_ENTITY ops;
    to move four doors, emit four ASSIGN_RELATIONSHIP ops.
  - Every GlobalId you emit MUST be copied verbatim from the context block
    below (EXISTING_PARENTS, DELETE_TARGETS, MOVE_TARGETS or DESTINATION).
    NEVER invent a GlobalId.
  - ASSIGN_RELATIONSHIP moves an element between storeys only. It cannot
    move spaces or storeys themselves — set cannot_express for those.
  - Only the five listed ifc_class values are creatable. Creating a physical
    element (IfcWall, IfcDoor, IfcWindow, IfcSlab, furniture, …) requires
    geometry and is NOT possible — set cannot_express.
  - "parent_relation": "aggregate" is only valid for IfcSpace under a spatial
    parent. Zones take members via member_global_ids instead.
  - Copy names from PROPOSED_NAMES when it is non-empty. Do not invent names.

Set "cannot_express": true (with an empty ops list and a one-sentence
reason) whenever the request needs anything these operations cannot do —
editing geometry, moving a space or storey, grouping entities into an
existing zone, conditional logic, touching entities that are not listed in
the context, or anything you are unsure how to express. Falling back is
safe and expected; guessing is not.

Examples:

Request: "create three new IfcZone entities for Fire Zone A, B, and C"
{"ops": [
  {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone A"},
  {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone B"},
  {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone C"}],
 "cannot_express": false, "cannot_express_reason": "",
 "explanation": "Create three fire zones.", "confidence": 95}

Request: "delete the chair" (DELETE_TARGETS lists one chair, 1abc…)
{"ops": [{"op": "DELETE_ENTITY", "global_id": "1abc..."}],
 "cannot_express": false, "cannot_express_reason": "",
 "explanation": "Delete the chair.", "confidence": 90}

Request: "move Wall-01 to Level 2"
(MOVE_TARGETS lists Wall-01 as 2abc…, DESTINATION lists Level 2 as 3def…)
{"ops": [{"op": "ASSIGN_RELATIONSHIP", "global_id": "2abc...",
          "destination_global_id": "3def...", "relation": "container"}],
 "cannot_express": false, "cannot_express_reason": "",
 "explanation": "Move Wall-01 to Level 2.", "confidence": 93}

Request: "make the walls 20cm thicker"
{"ops": [], "cannot_express": true,
 "cannot_express_reason": "Changing geometry is not one of the available operations.",
 "explanation": "", "confidence": 0}
"""

_USER_TEMPLATE = """\
## Request
{user_message}

## Context (the ONLY GlobalIds you may use)
{entity_context}

## Task
Emit typed operations for this request (JSON only).
"""


class T3OpPlanError(Exception):
    """The op planner could not be run at all."""


@dataclass(frozen=True)
class T3OpPlanResult:
    """Outcome of Stage 4 op planning."""

    ops: tuple[dict, ...] = ()
    cannot_express: bool = False
    reason: str = ""
    explanation: str = ""
    confidence: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_usable(self) -> bool:
        return bool(self.ops) and not self.cannot_express


class T3OpPlanner:
    """Emits typed Tier 3 ops through the schema-validated LLM boundary."""

    def __init__(self, user=None, llm=None) -> None:
        self._user = user
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self._user, temperature=0.0, format_json=True)
        return self._llm

    def plan(
        self,
        user_message: str,
        t3_inputs: dict,
        entity_context: str,
        *,
        prior_errors: Sequence[BoundaryError] = (),
    ) -> T3OpPlanResult:
        """Plan typed ops for a Tier 3 request.

        Never raises: any failure degrades to ``cannot_express``, which the
        caller reads as "fall back to code generation".

        At most two LLM calls: the initial attempt, plus one repair round if
        the first answer references entities the pipeline never resolved.
        The loop is hard-bounded — a model that keeps hallucinating must not
        be able to spin forever.
        """
        user_prompt = _USER_TEMPLATE.format(
            user_message=user_message, entity_context=entity_context
        )
        errors: Sequence[BoundaryError] = prior_errors

        for attempt in range(_MAX_GROUNDING_ATTEMPTS):
            outcome, grounding_errors = self._attempt(user_prompt, t3_inputs, errors)
            if outcome is not None:
                return outcome
            errors = grounding_errors
            logger.info(
                "T3 op planner emitted ungrounded ids (%s) — attempt %d/%d",
                [e.path for e in grounding_errors],
                attempt + 1,
                _MAX_GROUNDING_ATTEMPTS,
            )

        return T3OpPlanResult(
            cannot_express=True,
            reason="The planner referenced entities that are not in this model.",
        )

    # ── Internals ──────────────────────────────────────────

    def _attempt(
        self,
        user_prompt: str,
        t3_inputs: dict,
        prior_errors: Sequence[BoundaryError],
    ) -> tuple[T3OpPlanResult | None, list[BoundaryError]]:
        """One planning round.

        Returns ``(result, [])`` when the round settled the outcome, or
        ``(None, errors)`` when the ops were ungrounded and a repair round
        is worth trying.
        """
        try:
            plan = call_structured(
                self.llm,
                stage="t3_ops",
                system_prompt=T3_OPS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=T3OpPlan,
                prior_errors=prior_errors,
            )
        except BoundaryValidationError as e:
            logger.info("T3 op planner produced no valid plan — falling back to code: %s", e)
            return (
                T3OpPlanResult(
                    cannot_express=True, reason="The op planner did not return a valid plan."
                ),
                [],
            )
        except Exception as e:  # noqa: BLE001 — LLM/network failure must not break the tier
            logger.warning("T3 op planner call failed — falling back to code: %s", e)
            return (
                T3OpPlanResult(cannot_express=True, reason=f"The op planner was unavailable: {e}"),
                [],
            )

        if plan.cannot_express:
            logger.info("T3 op planner declined: %s", plan.cannot_express_reason)
            return (
                T3OpPlanResult(
                    cannot_express=True,
                    reason=plan.cannot_express_reason,
                    explanation=plan.explanation,
                    confidence=plan.confidence,
                ),
                [],
            )

        ops = [op.model_dump() for op in plan.ops]
        grounding_errors = self._ground(ops, t3_inputs)
        if grounding_errors:
            return None, grounding_errors

        warnings: list[str] = []
        if plan.confidence and plan.confidence < 70:
            warnings.append(
                f"The planner reported low confidence ({plan.confidence}%) — "
                f"check the preview carefully before approving."
            )

        return (
            T3OpPlanResult(
                ops=tuple(ops),
                explanation=plan.explanation,
                confidence=plan.confidence,
                warnings=tuple(warnings),
            ),
            [],
        )

    @staticmethod
    def _ground(ops: Sequence[dict], t3_inputs: dict) -> list[BoundaryError]:
        """Reject anything the deterministic stages did not already resolve.

        The planner may only re-emit GlobalIds and names it was shown. This
        is what makes a hallucinated target impossible rather than merely
        unlikely — the DB-backed builder re-checks again afterwards.
        """
        parent_ids = {
            p.get("global_id")
            for p in (t3_inputs.get("existing_parents") or [])
            if p.get("global_id")
        }
        delete_ids = {
            t.get("global_id")
            for t in (t3_inputs.get("targets_to_delete") or [])
            if t.get("global_id")
        }
        move_ids = {
            t.get("global_id") for t in (t3_inputs.get("move_targets") or []) if t.get("global_id")
        }
        destination_ids = {
            d.get("global_id") for d in (t3_inputs.get("destination") or []) if d.get("global_id")
        }
        known_ids = parent_ids | delete_ids | move_ids | destination_ids
        proposed_names = {n for n in (t3_inputs.get("proposed_names") or []) if n}

        errors: list[BoundaryError] = []
        for index, op in enumerate(ops):
            if op.get("op") == "ASSIGN_RELATIONSHIP":
                gid = op.get("global_id") or ""
                if gid not in move_ids:
                    errors.append(
                        BoundaryError(
                            code="UNGROUNDED",
                            path=f"ops[{index}].global_id",
                            hint=f"{gid!r} is not in MOVE_TARGETS.",
                        )
                    )
                destination_gid = op.get("destination_global_id") or ""
                if destination_gid not in destination_ids:
                    errors.append(
                        BoundaryError(
                            code="UNGROUNDED",
                            path=f"ops[{index}].destination_global_id",
                            hint=f"{destination_gid!r} is not in DESTINATION.",
                        )
                    )
                continue

            if op.get("op") == "DELETE_ENTITY":
                gid = op.get("global_id") or ""
                if gid not in delete_ids:
                    errors.append(
                        BoundaryError(
                            code="UNGROUNDED",
                            path=f"ops[{index}].global_id",
                            hint=(
                                f"{gid!r} is not in DELETE_TARGETS. Only use GlobalIds "
                                f"listed in the context block."
                            ),
                        )
                    )
                continue

            parent = op.get("parent_global_id") or ""
            if parent and parent not in parent_ids:
                errors.append(
                    BoundaryError(
                        code="UNGROUNDED",
                        path=f"ops[{index}].parent_global_id",
                        hint=f"{parent!r} is not in EXISTING_PARENTS.",
                    )
                )

            for member in op.get("member_global_ids") or []:
                if member not in known_ids:
                    errors.append(
                        BoundaryError(
                            code="UNGROUNDED",
                            path=f"ops[{index}].member_global_ids",
                            hint=f"{member!r} is not an entity listed in the context.",
                        )
                    )

            name = (op.get("name") or "").strip()
            if proposed_names and name not in proposed_names:
                errors.append(
                    BoundaryError(
                        code="UNGROUNDED",
                        path=f"ops[{index}].name",
                        hint=(
                            f"{name!r} is not one of the names the user asked for "
                            f"({sorted(proposed_names)})."
                        ),
                    )
                )

            ifc_class = op.get("ifc_class") or ""
            if ifc_class not in CREATABLE_CLASSES:
                errors.append(
                    BoundaryError(
                        code="UNGROUNDED",
                        path=f"ops[{index}].ifc_class",
                        hint=f"{ifc_class!r} is not creatable.",
                    )
                )

        return errors
