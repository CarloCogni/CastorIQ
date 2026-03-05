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

from .ifc_standard_psets import get_applicable_psets
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
4. Use the entity context provided to determine correct property set and property names.
5. The filter should be as specific as possible to match only the intended entities.
6. Be conservative with confidence — if you're unsure about pset/property names, lower it.
7. Always include "filter" with at least "ifc_type" for Tier 1.
8. Omit keys that are not relevant (e.g. omit "pset"/"property" for SET_ATTRIBUTE, omit "attribute" for SET_PROPERTY).
9. When the user says "rename" or "change the name", ALWAYS use SET_ATTRIBUTE with "attribute": "Name".
10. If the request contains multiple SIMPLE property/attribute changes connected by "and", "also", "plus", or comma-separated, return a JSON ARRAY of intents instead of a single object. Each element follows the same schema. Only do this for Tier 1 operations — if any sub-operation needs Tier 2/3, return the whole request as Tier 2.

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

    def __init__(self, user=None):
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

    def classify(
        self,
        user_message: str,
        entity_context: str,
    ) -> dict:
        """
        Parse a user's modification request into a structured intent.

        Args:
            user_message: The raw user input (e.g. "Set fire rating to EI120")
            entity_context: Summary of relevant entities from the DB

        Returns:
            Parsed intent dict with tier, operation, filter, etc.

        Raises:
            IntentParseError: If LLM output is not valid JSON or missing fields.
        """
        user_message = normalize_message(user_message)

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
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

        if not isinstance(tier, int) or tier not in (1, 2, 3):
            raise IntentParseError(f"Invalid tier: {tier}. Must be 1, 2, or 3.")

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

    def build_entity_context(self, entities) -> str:
        """
        Build a concise entity summary string for the LLM prompt.

        Groups entities by type and includes sample property sets
        so the LLM knows what's available.
        """
        if not entities:
            return "(no entities found)"

        # Group by ifc_type
        by_type: dict[str, list] = {}
        for e in entities[:50]:  # Cap at 50 to avoid token overflow
            by_type.setdefault(e.ifc_type, []).append(e)

        lines = []
        for ifc_type, group in by_type.items():
            names = [e.name or e.global_id[:8] for e in group[:5]]
            sample = group[0]

            # Extract pset names from properties JSON keys
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
                        from .ifc_standard_psets import lookup_property

                        info = lookup_property(pset_name, prop_name)
                        if info:
                            type_str, enum_values = info
                            if enum_values:
                                hint = f"  (enum: {', '.join(enum_values)})"
                            else:
                                hint = f"  ({type_str})"
                    sample_props.append(f"    {key} = {val}{hint}")

            # Show applicable standard psets not yet on this entity

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


class IntentParseError(Exception):
    """Raised when the LLM output cannot be parsed into a valid intent."""

    pass
