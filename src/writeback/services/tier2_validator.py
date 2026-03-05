# writeback/services/tier2_validator.py
"""
Validation layer for Tier 2 operation plans.

Validates each step individually AND checks cross-step consistency.
Reuses Tier1Validator for property operations, adds plan-level checks.
"""

import logging
from dataclasses import dataclass, field

from ifc_processor.models import IFCEntity

from .filter_engine import FilterEngine
from .tier1_validator import Tier1Validator
from .tier2_planner import TIER1_OPERATIONS

logger = logging.getLogger(__name__)

TIER2_ONLY_OPERATIONS = {
    "ADD_PSET",
    "REMOVE_PSET",
    "SET_CLASSIFICATION",
    "SET_MATERIAL",
    "COPY_PROPERTIES",
}


@dataclass
class StepValidation:
    """Validation result for a single plan step."""

    step_index: int
    operation: str
    valid: bool
    entities: list = field(default_factory=list)
    error: str = ""


@dataclass
class PlanValidation:
    """Validation result for the entire plan."""

    valid: bool
    steps: list[StepValidation] = field(default_factory=list)
    error: str = ""

    @property
    def total_affected(self) -> int:
        """Total entity count across all valid steps."""
        return sum(len(s.entities) for s in self.steps if s.valid)


class Tier2Validator:
    """
    Validates a Tier 2 plan against actual project data.

    Two-phase validation:
        1. Per-step: filter resolution + operation-specific checks
        2. Cross-step: consistency checks (no contradictions, valid ordering)

    Usage:
        validator = Tier2Validator(project)
        result = validator.validate_plan(plan_dict)
        if result.valid:
            # proceed with execution
    """

    def __init__(self, project):
        self.project = project
        self.filter_engine = FilterEngine(project)
        self.t1_validator = Tier1Validator()

    def validate_plan(self, plan: dict) -> PlanValidation:
        """
        Validate all steps in a Tier 2 plan.

        Returns PlanValidation with per-step results.
        Stops at first invalid step.
        """
        steps = plan.get("plan", [])
        step_validations = []

        for i, step in enumerate(steps):
            sv = self._validate_step(step, i)
            step_validations.append(sv)

            if not sv.valid:
                return PlanValidation(
                    valid=False,
                    steps=step_validations,
                    error=f"Step {i + 1} failed: {sv.error}",
                )

        # Cross-step consistency
        consistency_error = self._check_consistency(steps, step_validations)
        if consistency_error:
            return PlanValidation(
                valid=False,
                steps=step_validations,
                error=consistency_error,
            )

        logger.info(
            f"Tier2 plan validated: {len(steps)} steps, "
            f"{sum(len(s.entities) for s in step_validations)} total entities"
        )

        return PlanValidation(valid=True, steps=step_validations)

    def _validate_step(self, step: dict, index: int) -> StepValidation:
        """Validate a single plan step: resolve filter + check operation."""
        operation = step.get("operation", "")
        filter_spec = step.get("filter", {})
        params = step.get("params", {})

        # Resolve entities
        try:
            matched_qs = self.filter_engine.resolve(filter_spec)
            entities = list(matched_qs)
        except ValueError as e:
            return StepValidation(
                step_index=index,
                operation=operation,
                valid=False,
                error=str(e),
            )

        # Delegate Tier 1 ops to existing validator
        if operation in TIER1_OPERATIONS:
            # Build a Tier 1-style intent from plan step params
            intent = self._step_to_intent(operation, params)
            t1_result = self.t1_validator.validate(intent, entities)
            return StepValidation(
                step_index=index,
                operation=operation,
                valid=t1_result.valid,
                entities=t1_result.entities or [],
                error=t1_result.error,
            )

        # Tier 2-specific validations
        if operation == "ADD_PSET":
            return self._validate_add_pset(index, params, entities)
        if operation == "REMOVE_PSET":
            return self._validate_remove_pset(index, params, entities)
        if operation in ("SET_CLASSIFICATION", "SET_MATERIAL"):
            # These always succeed if entities matched — minimal pre-validation
            return StepValidation(
                step_index=index,
                operation=operation,
                valid=True,
                entities=entities,
            )
        if operation == "COPY_PROPERTIES":
            return self._validate_copy_properties(index, params, entities)

        return StepValidation(
            step_index=index,
            operation=operation,
            valid=False,
            error=f"Unknown operation: {operation}",
        )

    def _validate_add_pset(
        self,
        index: int,
        params: dict,
        entities: list[IFCEntity],
    ) -> StepValidation:
        """ADD_PSET: warn if pset already exists on all entities."""
        pset_name = params.get("pset_name", "")
        properties = params.get("properties", {})

        if not pset_name:
            return StepValidation(
                step_index=index,
                operation="ADD_PSET",
                valid=False,
                error="ADD_PSET requires 'pset_name'",
            )
        if not properties:
            return StepValidation(
                step_index=index,
                operation="ADD_PSET",
                valid=False,
                error="ADD_PSET requires at least one property",
            )

        # Check how many already have this pset with all the properties
        fully_populated = 0
        for e in entities:
            if all(f"{pset_name}.{k}" in (e.properties or {}) for k in properties):
                fully_populated += 1

        if fully_populated == len(entities):
            return StepValidation(
                step_index=index,
                operation="ADD_PSET",
                valid=False,
                error=f"All {len(entities)} entities already have '{pset_name}' "
                f"with all specified properties.",
            )

        return StepValidation(
            step_index=index,
            operation="ADD_PSET",
            valid=True,
            entities=entities,
        )

    def _validate_remove_pset(
        self,
        index: int,
        params: dict,
        entities: list[IFCEntity],
    ) -> StepValidation:
        pset_name = params.get("pset_name", "")
        if not pset_name:
            return StepValidation(
                step_index=index,
                operation="REMOVE_PSET",
                valid=False,
                error="REMOVE_PSET requires 'pset_name'",
            )

        # At least one entity must have this pset
        has_pset = [
            e for e in entities if any(k.startswith(f"{pset_name}.") for k in (e.properties or {}))
        ]
        if not has_pset:
            return StepValidation(
                step_index=index,
                operation="REMOVE_PSET",
                valid=False,
                error=f"No matched entities have property set '{pset_name}'.",
            )

        return StepValidation(
            step_index=index,
            operation="REMOVE_PSET",
            valid=True,
            entities=has_pset,
        )

    def _validate_copy_properties(
        self,
        index: int,
        params: dict,
        entities: list[IFCEntity],
    ) -> StepValidation:
        source_name = params.get("source_name", "")
        pset_name = params.get("pset_name", "")

        if not source_name or not pset_name:
            return StepValidation(
                step_index=index,
                operation="COPY_PROPERTIES",
                valid=False,
                error="COPY_PROPERTIES requires 'source_name' and 'pset_name'",
            )

        # Verify source entity exists in the project
        source_exists = IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status="completed",
            name__icontains=source_name,
        ).exists()

        if not source_exists:
            return StepValidation(
                step_index=index,
                operation="COPY_PROPERTIES",
                valid=False,
                error=f"Source entity '{source_name}' not found in project.",
            )

        return StepValidation(
            step_index=index,
            operation="COPY_PROPERTIES",
            valid=True,
            entities=entities,
        )

    def _check_consistency(
        self,
        steps: list[dict],
        validations: list[StepValidation],
    ) -> str:
        """
        Cross-step consistency checks.

        Catches:
            - ADD_PSET then REMOVE_PSET on same pset (contradictory)
            - SET_PROPERTY on a pset that's REMOVE_PSET'd later
        """
        added_psets: set[str] = set()
        removed_psets: set[str] = set()

        for step in steps:
            op = step.get("operation", "")
            params = step.get("params", {})
            pset = params.get("pset_name", "") or params.get("pset", "")

            if op == "ADD_PSET" and pset:
                if pset in removed_psets:
                    return f"Contradiction: plan adds '{pset}' after removing it."
                added_psets.add(pset)

            elif op == "REMOVE_PSET" and pset:
                if pset in added_psets:
                    return f"Contradiction: plan removes '{pset}' after adding it."
                removed_psets.add(pset)

            elif op in ("SET_PROPERTY", "ADD_PROPERTY") and pset:
                if pset in removed_psets:
                    return (
                        f"Contradiction: plan modifies '{pset}'but it's removed in an earlier step."
                    )
        return ""

    @staticmethod
    def _step_to_intent(operation: str, params: dict) -> dict:
        """Convert a plan step's params into a Tier 1-style intent dict."""
        intent = {"operation": operation, "tier": 1}
        # Map plan params → intent fields
        for key in ("pset", "property", "new_value", "attribute"):
            if key in params:
                intent[key] = params[key]
        return intent
