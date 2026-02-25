# writeback/services/tier2_planner.py
"""
LLM-powered operation planner for Tier 2 modifications.

Takes a user request that's too complex for Tier 1 and produces
a structured execution plan: an ordered sequence of operations.

Each step in the plan reuses Tier 1 or Tier 2 operations.
"""

import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage
from core.llm import get_llm

logger = logging.getLogger(__name__)

# All operations available to the planner
TIER2_OPERATIONS = {
    # Inherited from Tier 1
    "SET_PROPERTY", "ADD_PROPERTY", "REMOVE_PROPERTY", "SET_ATTRIBUTE",
    # New in Tier 2
    "ADD_PSET", "REMOVE_PSET", "SET_CLASSIFICATION", "SET_MATERIAL",
    "COPY_PROPERTIES",
}

TIER1_OPERATIONS = {"SET_PROPERTY", "ADD_PROPERTY", "REMOVE_PROPERTY", "SET_ATTRIBUTE"}

PLANNER_SYSTEM_PROMPT = """\
You are an IFC (Industry Foundation Classes) modification planner.
The user's request requires MULTIPLE coordinated changes that can't be done
in a single operation. Your job is to decompose it into an ordered plan.

## Output

Return ONLY valid JSON (no markdown, no explanation):

{
  "tier": 2,
  "plan": [
    {
      "step": 1,
      "operation": "<operation name>",
      "filter": {
        "ifc_type": "<e.g. IfcWall>",
        "storey": "<optional>",
        "property_match": {"<Pset.Property>": "<value>"},
        "name_pattern": "<optional glob>"
      },
      "params": { <operation-specific parameters> },
      "explanation": "<what this step does>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<overall plan summary>"
}

## Available Operations

### Property Operations (from Tier 1)
- SET_PROPERTY: params: {"pset", "property", "new_value"}
- ADD_PROPERTY: params: {"pset", "property", "new_value"}
- REMOVE_PROPERTY: params: {"pset", "property"}
- SET_ATTRIBUTE: params: {"attribute", "new_value"}

### Structural Operations (Tier 2)
- ADD_PSET: Create a new property set with initial properties.
  params: {"pset_name": "...", "properties": {"key": "value", ...}}
- REMOVE_PSET: Remove an entire property set.
  params: {"pset_name": "..."}
- SET_CLASSIFICATION: Assign a classification reference.
  params: {"system_name": "...", "reference": "...", "name": "(optional)"}
- SET_MATERIAL: Assign a material to elements.
  params: {"material_name": "..."}
- COPY_PROPERTIES: Copy properties from a source entity.
  params: {"source_name": "...", "pset_name": "...", "property_names": ["...", ...] or null for all}

## Rules

1. Each step MUST have: operation, filter, params, explanation.
2. Steps execute in order. Later steps can depend on earlier ones
   (e.g. ADD_PSET first, then SET_PROPERTY on that pset).
3. Use the most specific filter possible for each step.
4. If ALL steps are simple property/attribute changes, this should be Tier 1
   (chained). Only use Tier 2 if at least one step needs a Tier 2 operation,
   OR if the decomposition requires LLM reasoning (implicit multi-step).
5. If the request involves creating/deleting entities or geometry, set tier: 3
   with only "explanation" and "confidence".
6. Maximum 10 steps per plan.
7. Be conservative with confidence.

## Common Property Sets

- Pset_WallCommon: IsExternal, IsLoadBearing, FireRating, ThermalTransmittance, Reference, Status
- Pset_DoorCommon: IsExternal, FireRating, Reference, HandicapAccessible
- Pset_WindowCommon: IsExternal, FireRating, ThermalTransmittance, GlazingAreaFraction
- Pset_SlabCommon: IsExternal, LoadBearing, FireRating, AcousticRating
- Pset_ColumnCommon: IsExternal, LoadBearing, FireRating, Reference
- Pset_BeamCommon: IsExternal, LoadBearing, FireRating, Span

NOTE: "Name", "Description", "ObjectType", "Tag" are ATTRIBUTES (SET_ATTRIBUTE), not properties.

## Examples

User: "Add a fire compliance property set to all external walls with standard FS-2026 and classify them"
Plan:
{
  "tier": 2,
  "plan": [
    {
      "step": 1,
      "operation": "ADD_PSET",
      "filter": {"ifc_type": "IfcWall", "property_match": {"Pset_WallCommon.IsExternal": true}},
      "params": {"pset_name": "Pset_FireCompliance", "properties": {"Standard": "FS-2026", "ComplianceDate": "2026-01-15", "Status": "Pending"}},
      "explanation": "Create fire compliance property set on all external walls"
    },
    {
      "step": 2,
      "operation": "SET_CLASSIFICATION",
      "filter": {"ifc_type": "IfcWall", "property_match": {"Pset_WallCommon.IsExternal": true}},
      "params": {"system_name": "FireSafety", "reference": "FS-2026/EW", "name": "External Wall Fire Compliance"},
      "explanation": "Classify external walls under fire safety standard"
    }
  ],
  "confidence": 0.85,
  "explanation": "Add fire compliance tracking and classification to all external walls"
}

User: "Assign concrete material to all columns and set them as load bearing"
Plan:
{
  "tier": 2,
  "plan": [
    {
      "step": 1,
      "operation": "SET_MATERIAL",
      "filter": {"ifc_type": "IfcColumn"},
      "params": {"material_name": "Concrete"},
      "explanation": "Assign concrete material to all columns"
    },
    {
      "step": 2,
      "operation": "SET_PROPERTY",
      "filter": {"ifc_type": "IfcColumn"},
      "params": {"pset": "Pset_ColumnCommon", "property": "LoadBearing", "new_value": true},
      "explanation": "Set all columns as load bearing"
    }
  ],
  "confidence": 0.9,
  "explanation": "Assign concrete material and mark all columns as load bearing"
}
"""

PLANNER_USER_TEMPLATE = """\
## User Request
{user_message}

## Entity Context
{entity_context}

## Task
Decompose this request into an ordered execution plan (JSON only).
"""


class Tier2Planner:
    """
    Generates structured execution plans for Tier 2 modifications.

    The planner is ONLY invoked when IntentClassifier returns tier=2.
    It takes the same user message + entity context and produces
    a multi-step plan.

    Usage:
        planner = Tier2Planner()
        plan = planner.generate_plan(user_message, entity_context)
        # plan = {"tier": 2, "plan": [...], "confidence": 85, ...}
    """

    def __init__(self, user=None):
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

    def generate_plan(self, user_message: str, entity_context: str) -> dict:
        """
        Generate a Tier 2 execution plan from a user request.

        Returns:
            Dict with "tier", "plan" (list of steps), "confidence", "explanation".

        Raises:
            PlanGenerationError on invalid LLM output.
        """
        messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=PLANNER_USER_TEMPLATE.format(
                user_message=user_message,
                entity_context=entity_context,
            )),
        ]

        response = self.llm.invoke(messages)

        try:
            plan = json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.error(f"Tier2 planner returned invalid JSON: {response.content}")
            raise PlanGenerationError(f"Could not parse plan as JSON: {e}") from e

        self._validate_plan(plan)

        # Normalize confidence to 0-100
        raw_conf = plan.get("confidence", 0.0)
        if isinstance(raw_conf, (int, float)) and raw_conf <= 1.0:
            plan["confidence"] = round(raw_conf * 100)
        else:
            plan["confidence"] = int(raw_conf)

        logger.info(
            f"Tier2 plan: {len(plan.get('plan', []))} steps, "
            f"confidence={plan['confidence']}%"
        )

        return plan

    def _validate_plan(self, plan: dict) -> None:
        """Validate plan structure and each step."""
        if not isinstance(plan, dict):
            raise PlanGenerationError("Plan must be a JSON object")

        tier = plan.get("tier")
        if tier == 3:
            # LLM decided this needs Tier 3 — that's a valid escalation
            return

        if tier != 2:
            raise PlanGenerationError(f"Expected tier 2, got {tier}")

        steps = plan.get("plan")
        if not isinstance(steps, list) or not steps:
            raise PlanGenerationError("Plan must have a non-empty 'plan' array")

        if len(steps) > 10:
            raise PlanGenerationError(f"Plan has {len(steps)} steps (max 10)")

        for i, step in enumerate(steps):
            self._validate_step(step, i)

    def _validate_step(self, step: dict, index: int) -> None:
        """Validate a single plan step."""
        label = f"Step {index + 1}"

        required = ["operation", "filter", "params", "explanation"]
        missing = [f for f in required if f not in step]
        if missing:
            raise PlanGenerationError(f"{label} missing fields: {missing}")

        op = step["operation"]
        if op not in TIER2_OPERATIONS:
            raise PlanGenerationError(
                f"{label}: unknown operation '{op}'. "
                f"Valid: {', '.join(sorted(TIER2_OPERATIONS))}"
            )

        # Validate params per operation
        params = step["params"]
        self._validate_step_params(op, params, label)

    def _validate_step_params(self, operation: str, params: dict, label: str) -> None:
        """Check that operation-specific params are present."""
        required_params = {
            "SET_PROPERTY": ["pset", "property", "new_value"],
            "ADD_PROPERTY": ["pset", "property", "new_value"],
            "REMOVE_PROPERTY": ["pset", "property"],
            "SET_ATTRIBUTE": ["attribute", "new_value"],
            "ADD_PSET": ["pset_name", "properties"],
            "REMOVE_PSET": ["pset_name"],
            "SET_CLASSIFICATION": ["system_name", "reference"],
            "SET_MATERIAL": ["material_name"],
            "COPY_PROPERTIES": ["source_name", "pset_name"],
        }

        needed = required_params.get(operation, [])
        missing = [p for p in needed if p not in params]
        if missing:
            raise PlanGenerationError(
                f"{label} ({operation}): missing params: {missing}"
            )


class PlanGenerationError(Exception):
    """Raised when the Tier 2 planner produces invalid output."""
    pass