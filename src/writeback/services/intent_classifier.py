# writeback/services/intent_classifier.py
"""
LLM-powered intent classification for IFC modifications.

Parses natural language into structured modification intents.
This is the ONLY service that calls the LLM in the Tier 1 flow.
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm
from ifc_processor.services.ifc_standard_psets import get_applicable_psets

from .message_normalizer import normalize as normalize_message

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# NOTE: This is a plain string — NOT an f-string.
# Use single braces { } for the JSON example.
# The only template that uses .format() is USER_TEMPLATE below.
# ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are an IFC (Industry Foundation Classes) modification assistant.
Your job is to parse a user's modification request into a structured JSON intent.

## Your Output

Return ONLY valid JSON (no markdown, no explanation) with this schema:

{
  "tier": 1,
  "operation": "<one of: SET_PROPERTY, ADD_PROPERTY, REMOVE_PROPERTY, SET_ATTRIBUTE>",
  "filter": {
    "ifc_type": "<optional: IfcWall, IfcDoor, IfcWindow, etc.>",
    "storey": "<optional: floor/level name>",
    "property_match": {"<Pset.Property>": "<value>"},
    "name_pattern": "<optional: glob pattern like D-* or Wall-01>"
  },
  "pset": "<property set name, e.g. Pset_WallCommon>",
  "property": "<property name, e.g. FireRating>",
  "attribute": "<for SET_ATTRIBUTE only: Name, Description, etc.>",
  "new_value": "<the new value to set>",
  "confidence": <0.0 to 1.0>,
  "explanation": "<one-line summary of what this will do>"
}

## Choosing the Right Operation

CRITICAL — read carefully:

- SET_PROPERTY: Changes a value inside a PropertySet (e.g. Pset_WallCommon.FireRating).
  Use for: fire rating, thermal transmittance, IsExternal, LoadBearing, etc.
  Requires: "pset" and "property" fields.

- SET_ATTRIBUTE: Changes a DIRECT attribute on the IFC entity itself.
  Use for: renaming (Name), changing Description, ObjectType, Tag, LongName.
  Requires: "attribute" field. Do NOT set "pset" or "property".
  "Name" is an ATTRIBUTE, not a property — it lives on the entity, not in any Pset.

- ADD_PROPERTY: Adds a NEW property to a PropertySet. If the pset is a standard IFC pset (e.g. Pset_WallCommon) and doesn't exist yet, it will be auto-created. Works for any standard Pset_*Common property set.
- REMOVE_PROPERTY: Removes a property from a PropertySet.

## Rules

1. Only output Tier 1 if the request is a SINGLE property/attribute change on entities matching a simple filter.
2. If the request needs multiple different changes, set "tier": 2 and fill only "explanation" and "confidence".
3. If the request involves creating/deleting entities or spatial operations, set "tier": 3 and fill only "explanation" and "confidence".
4. If the request is too vague, ambiguous, or impossible to map to a specific IFC operation and target — even after your best interpretation — set "tier": 0 and fill "explanation" with a one-sentence reason the user can act on. Do NOT guess a tier if you cannot determine WHAT to change, on WHICH entities, and to WHAT value. Examples: "change everything", "update the model", "do something to the building", "improve fire safety" (no property or value specified).
5. Use the entity context provided to determine correct property set and property names.
6. The filter should be as specific as possible to match only the intended entities.
7. Be conservative with confidence — if you're unsure about pset/property names, lower it.
8. Always include "filter" with at least "ifc_type" for Tier 1.
9. Omit keys that are not relevant (e.g. omit "pset"/"property" for SET_ATTRIBUTE, omit "attribute" for SET_PROPERTY).
10. When the user says "rename" or "change the name", ALWAYS use SET_ATTRIBUTE with "attribute": "Name".
11. If the request contains multiple SIMPLE property/attribute changes connected by "and", "also", "plus", or comma-separated, return a JSON ARRAY of intents instead of a single object. Each element follows the same schema. Only do this for Tier 1 operations — if any sub-operation needs Tier 2/3, return the whole request as Tier 2.
12. When returning a JSON array, you MUST include ALL operations mentioned — never truncate the list. If the user listed 4 renames, the array must have exactly 4 elements.

## Examples

User: "Rename wall W-01 to W-01-Updated"
Answer:
{"tier": 1, "operation": "SET_ATTRIBUTE", "filter": {"ifc_type": "IfcWall", "name_pattern": "W-01"}, "attribute": "Name", "new_value": "W-01-Updated", "confidence": 0.95, "explanation": "Rename wall W-01 to W-01-Updated"}

User: "Set fire rating of all external walls to EI120"
Answer:
{"tier": 1, "operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall", "property_match": {"Pset_WallCommon.IsExternal": true}}, "pset": "Pset_WallCommon", "property": "FireRating", "new_value": "EI120", "confidence": 0.9, "explanation": "Set FireRating to EI120 on all external walls"}

User: "Set IsExternal to false for wall House Front"
Answer:
{"tier": 1, "operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall", "name_pattern": "*House Front*"}, "pset": "Pset_WallCommon", "property": "IsExternal", "new_value": false, "confidence": 0.9, "explanation": "Set IsExternal to false on wall House Front"}

User: "Set fire rating to EI120 and IsExternal to true for all walls"
Answer:
[{"tier": 1, "operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall"}, "pset": "Pset_WallCommon", "property": "FireRating", "new_value": "EI120", "confidence": 0.9, "explanation": "Set FireRating to EI120 on all walls"}, {"tier": 1, "operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall"}, "pset": "Pset_WallCommon", "property": "IsExternal", "new_value": true, "confidence": 0.9, "explanation": "Set IsExternal to true on all walls"}]

User: "Change wall name from Basic Wall:A to GH_A, Change wall name from Basic Wall:B to GH_B, Change wall name from Basic Wall:C to GH_C"
Answer:
[{"tier": 1, "operation": "SET_ATTRIBUTE", "filter": {"ifc_type": "IfcWall", "name_pattern": "*Basic Wall:A*"}, "attribute": "Name", "new_value": "GH_A", "confidence": 0.95, "explanation": "Rename Basic Wall:A to GH_A"}, {"tier": 1, "operation": "SET_ATTRIBUTE", "filter": {"ifc_type": "IfcWall", "name_pattern": "*Basic Wall:B*"}, "attribute": "Name", "new_value": "GH_B", "confidence": 0.95, "explanation": "Rename Basic Wall:B to GH_B"}, {"tier": 1, "operation": "SET_ATTRIBUTE", "filter": {"ifc_type": "IfcWall", "name_pattern": "*Basic Wall:C*"}, "attribute": "Name", "new_value": "GH_C", "confidence": 0.95, "explanation": "Rename Basic Wall:C to GH_C"}]

## Common Property Sets

- Pset_WallCommon: IsExternal, IsLoadBearing, FireRating, ThermalTransmittance, Reference, LoadBearing, Status
- Pset_DoorCommon: IsExternal, FireRating, Reference, HandicapAccessible
- Pset_WindowCommon: IsExternal, FireRating, ThermalTransmittance, GlazingAreaFraction
- Pset_SlabCommon: IsExternal, LoadBearing, FireRating, AcousticRating
- Pset_ColumnCommon: IsExternal, LoadBearing, FireRating, Reference
- Pset_BeamCommon: IsExternal, LoadBearing, FireRating, Span

NOTE: "Name", "Description", "ObjectType", "Tag" are ATTRIBUTES, not properties.
They are NOT inside any Pset. Use SET_ATTRIBUTE for these.

## Value Types

- FireRating values: "EI30", "EI60", "EI90", "EI120", "REI60", "REI120", etc.
- Boolean values: true / false (not strings)
- ThermalTransmittance (U-value): numeric, e.g. 0.25
- Strings: keep as-is
"""

# This one DOES use .format() — so user_message / entity_context
# placeholders use single braces, and any literal braces would
# need doubling.  There are none here, so it's straightforward.
USER_TEMPLATE = """\
## User Request
{user_message}

## Entity Context
The project contains these relevant entities:
{entity_context}

## Task
Parse the user's request into a structured modification intent (JSON only).
"""


def _format_skill_injection(examples: list[dict]) -> str:
    """
    Format retrieved skill examples as a labeled few-shot block.

    Appended to SYSTEM_PROMPT when skill_examples are available.
    Each example shows only the essential fields (operation, filter, tier,
    confidence) to minimise token cost.

    Args:
        examples: Truncated dicts from skill_retriever.retrieve().

    Returns:
        Formatted string ready to append to the system prompt.
    """
    if not examples:
        return ""

    lines = ["## Learned Examples (from similar past requests)", ""]
    for i, ex in enumerate(examples, start=1):
        lines.append(f"Example {i}:")
        lines.append(f'Query: "{ex.get("query_text", "")}"')
        intent_summary = (
            f'{{"operation": "{ex.get("operation", "")}", '
            f'"filter": {ex.get("filter", "{}")}, '
            f'"tier": {ex.get("tier", "")}, '
            f'"confidence": {ex.get("confidence", "")}}}'
        )
        lines.append(f"Intent: {intent_summary}")
        lines.append("")

    return "\n".join(lines)


class IntentClassifier:
    """
    Classifies user modification requests into structured intents.

    The LLM determines:
        - Which tier is needed (1, 2, or 3)
        - What operation to perform
        - Which entities to target (filter spec)
        - What values to set

    Usage:
        classifier = IntentClassifier()
        intent = classifier.classify(
            user_message="Set all external walls to fire-rated EI120",
            entity_context="IfcWall (12 entities): Wall-01..Wall-12, ..."
        )
    """

    def __init__(self, user=None, temperature: float = 0.1):
        self.llm = get_llm(user=user, temperature=temperature, format_json=True)

    def classify(
        self,
        user_message: str,
        entity_context: str,
        skill_examples: list[dict] | None = None,
        failure_context: str | None = None,
    ) -> dict:
        """
        Parse a user's modification request into a structured intent.

        Args:
            user_message:    The raw user input (e.g. "Set fire rating to EI120")
            entity_context:  Summary of relevant entities from the DB
            skill_examples:  Optional retrieved few-shot examples from the skill
                             bank. When provided, they are appended to the system
                             prompt as a labeled few-shot block. None → existing
                             behaviour, no change to call sites.
            failure_context: Optional short context string from a prior FailureRecord.
                             Injected as a ## Previous Attempt block so the model
                             can avoid repeating the same mistake on retry.

        Returns:
            Parsed intent dict with tier, operation, filter, etc.

        Raises:
            IntentParseError: If LLM output is not valid JSON or missing fields.
        """
        user_message = normalize_message(user_message)

        system_content = SYSTEM_PROMPT
        if skill_examples:
            system_content += "\n\n" + _format_skill_injection(skill_examples)
            logger.debug("Injecting %d skill examples into classifier.", len(skill_examples))
        if failure_context:
            system_content += f"\n\n## Previous Attempt\n{failure_context}\n"
            logger.debug("Injecting failure context into classifier.")

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(
                content=USER_TEMPLATE.format(
                    user_message=user_message,
                    entity_context=entity_context,
                )
            ),
        ]

        response = self.llm.invoke(messages)

        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.error(f"LLM returned invalid JSON: {response.content}")
            raise IntentParseError(f"Could not parse LLM response as JSON: {e}") from e

        # Handle chained operations (LLM returns array)
        if isinstance(parsed, list):
            intents = []
            for i, sub_intent in enumerate(parsed):
                if not isinstance(sub_intent, dict):
                    raise IntentParseError(f"Chain element {i} is not a valid intent object")
                self._validate_structure(sub_intent)
                # Normalize confidence on each
                raw_conf = sub_intent.get("confidence", 0.0)
                if isinstance(raw_conf, (int, float)) and raw_conf <= 1.0:
                    sub_intent["confidence"] = round(raw_conf * 100)
                else:
                    sub_intent["confidence"] = int(raw_conf)
                intents.append(sub_intent)

            logger.info(f"Classified chain of {len(intents)} intents")
            return intents

        intent = parsed

        # Validate required fields
        self._validate_structure(intent)

        # Normalize confidence to 0-100 for display
        raw_confidence = intent.get("confidence", 0.0)
        if isinstance(raw_confidence, (int, float)) and raw_confidence <= 1.0:
            intent["confidence"] = round(raw_confidence * 100)
        else:
            intent["confidence"] = int(raw_confidence)

        logger.info(
            f"Classified intent: tier={intent['tier']}, "
            f"op={intent.get('operation', 'N/A')}, "
            f"confidence={intent.get('confidence', '?')}%"
        )

        return intent

    def _validate_structure(self, intent: dict) -> None:
        """Check that the intent has the minimum required fields."""
        if "tier" not in intent:
            raise IntentParseError("Missing 'tier' in intent")

        tier = intent["tier"]

        if not isinstance(tier, int) or tier not in (0, 1, 2, 3):
            raise IntentParseError(f"Invalid tier: {tier}. Must be 0, 1, 2, or 3.")

        if tier == 0:
            # Tier 0 is a rejection — the request is too vague to classify.
            # The only required field is a user-facing explanation.
            if "explanation" not in intent:
                raise IntentParseError("Tier 0 intent missing 'explanation'")
            return

        if tier == 1:
            required = ["operation", "filter", "confidence", "explanation"]
            missing = [f for f in required if f not in intent]
            if missing:
                raise IntentParseError(f"Tier 1 intent missing fields: {missing}")

            valid_ops = {
                "SET_PROPERTY",
                "ADD_PROPERTY",
                "REMOVE_PROPERTY",
                "SET_ATTRIBUTE",
            }
            if intent["operation"] not in valid_ops:
                raise IntentParseError(f"Unknown Tier 1 operation: {intent['operation']}")

    def build_entity_context(self, entities, available_tokens: int | None = None) -> str:
        """
        Build a concise entity summary string for the LLM prompt.

        When ``available_tokens`` is provided, automatically selects the
        appropriate detail tier to stay within budget:

        - **Full** (default): types, sample psets, sample property values,
          available standard psets. Most informative.
        - **Trimmed**: types and pset names only — no sample values or
          available psets. Used when full exceeds 50% of available budget.
        - **Minimal**: type counts only (``"47 IfcWall, 12 IfcSlab, …"``).
          Last resort when trimmed still exceeds budget.

        Args:
            entities:         Iterable of IFCEntity instances.
            available_tokens: Budget ceiling in tokens. None = always return full.
        """
        if not entities:
            return "(no entities found)"

        if available_tokens is None:
            return self._build_entity_context_full(entities)

        # Two-pass tier selection: use at most 50% of available budget
        half_budget = max(1, available_tokens // 2)

        full = self._build_entity_context_full(entities)
        from core.token_utils import estimate_tokens

        if estimate_tokens(full) <= half_budget:
            return full

        trimmed = self._build_entity_context_trimmed(entities)
        if estimate_tokens(trimmed) <= half_budget:
            return trimmed

        return self._build_entity_context_minimal(entities)

    def _build_entity_context_full(self, entities) -> str:
        """Full entity context: types, psets, sample property values, available psets."""
        by_type: dict[str, list] = {}
        for e in entities[:50]:
            by_type.setdefault(e.ifc_type, []).append(e)

        lines = []
        for ifc_type, group in by_type.items():
            names = [e.name or e.global_id[:8] for e in group[:5]]
            sample = group[0]

            psets = set()
            if sample.properties:
                for key in sample.properties:
                    if "." in key:
                        psets.add(key.split(".")[0])

            sample_props = []
            if sample.properties:
                for key, val in list(sample.properties.items())[:6]:
                    hint = ""
                    if "." in key:
                        pset_name, prop_name = key.split(".", 1)
                        from ifc_processor.services.ifc_standard_psets import lookup_property

                        info = lookup_property(pset_name, prop_name)
                        if info:
                            type_str, enum_values = info
                            if enum_values:
                                hint = f"  (enum: {', '.join(enum_values)})"
                            else:
                                hint = f"  ({type_str})"
                    sample_props.append(f"    {key} = {val}{hint}")

            existing_psets = set()
            if sample.properties:
                for key in sample.properties:
                    if "." in key:
                        existing_psets.add(key.split(".")[0])
            applicable = get_applicable_psets(ifc_type or "")
            missing_psets = [p for p in applicable if p not in existing_psets]

            line = (
                f"- {ifc_type} ({len(group)} entities): "
                f"{', '.join(names)}{'...' if len(group) > 5 else ''}\n"
                f"  Property sets: {', '.join(sorted(psets)) or '(none)'}"
            )
            if sample_props:
                line += "\n  Sample properties:\n" + "\n".join(sample_props)
            if missing_psets:
                line += f"\n  [Available standard psets: {', '.join(missing_psets)}]"

            lines.append(line)

        return "\n".join(lines)

    def _build_entity_context_trimmed(self, entities) -> str:
        """Trimmed entity context: types, entity names, and pset names only."""
        by_type: dict[str, list] = {}
        for e in entities[:50]:
            by_type.setdefault(e.ifc_type, []).append(e)

        lines = []
        for ifc_type, group in by_type.items():
            names = [e.name or e.global_id[:8] for e in group[:5]]
            sample = group[0]

            psets = set()
            if sample.properties:
                for key in sample.properties:
                    if "." in key:
                        psets.add(key.split(".")[0])

            lines.append(
                f"- {ifc_type} ({len(group)} entities): "
                f"{', '.join(names)}{'...' if len(group) > 5 else ''}\n"
                f"  Property sets: {', '.join(sorted(psets)) or '(none)'}"
            )

        return "\n".join(lines)

    def _build_entity_context_minimal(self, entities) -> str:
        """Minimal entity context: type counts only."""
        by_type: dict[str, int] = {}
        for e in entities[:100]:
            by_type[e.ifc_type] = by_type.get(e.ifc_type, 0) + 1

        parts = [f"{count} {ifc_type}" for ifc_type, count in by_type.items()]
        return ", ".join(parts)


class IntentParseError(Exception):
    """Raised when the LLM output cannot be parsed into a valid intent."""

    pass
