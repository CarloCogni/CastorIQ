# writeback/services/slot_extractor.py
"""Stage 2 of the V2 writeback pipeline — per-kind slot extraction.

Each kind from the triage stage uses its own narrow prompt to fill a
small, fixed set of slots. The slots feed Stages 3 (resolver) and 3.5
(tier router) and ultimately reach the existing writers via the intent
assembler.

Design contract: each prompt asks the LLM to extract ONLY the slots
required for one kind. There is no polymorphic schema, no per-tier
conditional fields, no "decide what to do" instruction. Small models
stay on schema because the surface is tiny.

The groundedness guard (value must appear in the user message) lives
here so it can fire per-segment with a precise rejection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)


# Attributes that Tier 1's SET_ATTRIBUTE may touch. Keep in sync with
# the writeback skill (.claude/skills/writeback-ops.md).
SAFE_ATTRIBUTES = frozenset({"Name", "Description", "ObjectType", "Tag", "LongName"})


# ── PROPERTY ──────────────────────────────────────────────────────

PROPERTY_SYSTEM_PROMPT = """\
You extract the property slots from a single IFC modification segment.

You are given:
  - The original user request (for value grounding only).
  - The segment's free-text value_phrase describing the property change.

Output ONLY this JSON:

{
  "pset": "<Pset name if user named one, else null>",
  "property": "<property name as the user wrote it>",
  "value": "<new value, copied verbatim from the user's wording>"
}

Rules:
  - "value" must appear as a substring of the user request, copied verbatim.
    No paraphrasing, no unit conversion, no synonym substitution.
  - "pset" is optional — if the user didn't name a pset, return null.
    DO NOT guess a pset.
  - Booleans: extract as JSON true / false (not strings).
  - Numbers: extract as JSON numbers when the user typed a bare number
    ("0.18", "120"); otherwise keep as a string.

Examples:

User: "set fire rating to EI120 on all walls"
Segment value_phrase: "FireRating to EI120"
{"pset": null, "property": "FireRating", "value": "EI120"}

User: "set Pset_WallCommon.IsExternal to true on Wall-001"
Segment value_phrase: "Pset_WallCommon.IsExternal to true"
{"pset": "Pset_WallCommon", "property": "IsExternal", "value": true}

User: "set ThermalTransmittance to 0.18 on all external walls"
Segment value_phrase: "ThermalTransmittance to 0.18"
{"pset": null, "property": "ThermalTransmittance", "value": 0.18}
"""


# ── ATTRIBUTE ─────────────────────────────────────────────────────

ATTRIBUTE_SYSTEM_PROMPT = """\
You extract the attribute slots from a single IFC modification segment.

Top-level IFC attributes are limited to: Name, Description, ObjectType,
Tag, LongName. Anything else is a property and goes through the PROPERTY
extractor instead.

Output ONLY this JSON:

{
  "attribute": "<Name | Description | ObjectType | Tag | LongName>",
  "value": "<new value, copied verbatim from the user's wording>"
}

Rules:
  - "value" must appear in the user request verbatim.
  - "attribute" must be one of the five listed values, in PascalCase.

Examples:

User: "rename door D-007 to D-007-Updated"
{"attribute": "Name", "value": "D-007-Updated"}

User: "change description on Wall-001 to 'load-bearing exterior wall'"
{"attribute": "Description", "value": "load-bearing exterior wall"}
"""


# ── PSET ──────────────────────────────────────────────────────────

PSET_SYSTEM_PROMPT = """\
You extract pset-level slots from a single IFC modification segment.

The user is adding a property set, removing one, or copying properties.
Output ONLY this JSON:

{
  "operation": "ADD_PSET | REMOVE_PSET",
  "pset_name": "<exact pset name>",
  "properties": {"<PropertyName>": <value>, ...}
}

Rules:
  - "operation": ADD_PSET when the user wants to attach the pset with
    initial properties; REMOVE_PSET when the user wants to delete the
    whole pset.
  - "pset_name": the exact name the user typed.
  - "properties": for ADD_PSET only — a flat dict of property → value
    where each value is copied verbatim from the user's wording.
    Empty dict {} is allowed when the user didn't name initial values.
    Omit this key for REMOVE_PSET.

Examples:

User: "add Pset_Maintenance to all walls with Inspector=TBD and LastInspection=2026-01-01"
{"operation": "ADD_PSET", "pset_name": "Pset_Maintenance",
 "properties": {"Inspector": "TBD", "LastInspection": "2026-01-01"}}

User: "remove Pset_Custom from Wall-001"
{"operation": "REMOVE_PSET", "pset_name": "Pset_Custom"}
"""


# ── CREATE ────────────────────────────────────────────────────────

CREATE_SYSTEM_PROMPT = """\
You extract creation slots from a single IFC modification segment.

The user wants to create one or more new entities of a specific IFC
class — typically IfcZone, IfcSpace, or relationship objects.

Output ONLY this JSON:

{
  "entity_class": "<IfcZone | IfcSpace | IfcGroup | ...>",
  "names": ["<name 1>", "<name 2>", ...],
  "parent_phrase": "<free-text describing the spatial parent, or empty>"
}

Rules:
  - "entity_class" must be a valid IFC class (PascalCase, prefix Ifc).
  - "names": one entry per entity to create. Copy the user's wording
    verbatim. Do not invent names. If the user said "three new zones"
    without naming them, leave names empty and the caller will reject.
  - "parent_phrase": free-text describing where the new entities go —
    "on Level 1", "in the Conference Room", "under the ground floor".
    Empty string if not stated.

Examples:

User: "create three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C"
{"entity_class": "IfcZone",
 "names": ["Fire Zone A", "Fire Zone B", "Fire Zone C"],
 "parent_phrase": ""}

User: "create an IfcSpace called Server Room on Level 2"
{"entity_class": "IfcSpace", "names": ["Server Room"], "parent_phrase": "Level 2"}
"""


# ── DELETE / RELATIONSHIP have no slots — target_phrase is the whole spec.


_USER_TEMPLATE = """\
## Original User Request
{user_message}

## Segment
target_phrase: {target_phrase}
value_phrase: {value_phrase}

## Task
Extract the slots for this segment (JSON only).
"""


class SlotExtractionError(Exception):
    """Slot extraction returned unusable data."""


@dataclass
class SlotResult:
    """The slots extracted for a segment, plus warning markers.

    ``slots`` is a kind-specific dict (see prompts above). ``warnings``
    captures groundedness or shape repair notes that the caller may
    want to surface to the user.
    """

    slots: dict
    warnings: list[str]


class SlotExtractor:
    """Stage 2 dispatcher.

    Picks the prompt for the segment's kind and runs the LLM. Returns
    structured slots or raises :class:`SlotExtractionError`.
    """

    def __init__(self, user=None, llm=None) -> None:
        self._user = user
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self._user, temperature=0.0, format_json=True)
        return self._llm

    def extract(self, segment: dict, user_message: str) -> SlotResult:
        """Extract slots for one segment.

        DELETE and RELATIONSHIP segments have no slots; the function
        returns an empty result for them.
        """
        kind = segment.get("kind")
        if kind in ("DELETE", "RELATIONSHIP"):
            return SlotResult(slots={}, warnings=[])

        if kind == "PROPERTY":
            slots = self._llm_extract(PROPERTY_SYSTEM_PROMPT, segment, user_message)
            return self._post_process_property(slots, user_message)

        if kind == "ATTRIBUTE":
            slots = self._llm_extract(ATTRIBUTE_SYSTEM_PROMPT, segment, user_message)
            return self._post_process_attribute(slots, user_message)

        if kind == "PSET":
            slots = self._llm_extract(PSET_SYSTEM_PROMPT, segment, user_message)
            return self._post_process_pset(slots, user_message)

        if kind == "CREATE":
            slots = self._llm_extract(CREATE_SYSTEM_PROMPT, segment, user_message)
            return self._post_process_create(slots)

        # OUT_OF_SCOPE / UNCLEAR / unknown — no slots needed; the
        # router rejects before reaching execution.
        return SlotResult(slots={}, warnings=[])

    # ── LLM call ────────────────────────────────────────────────

    def _llm_extract(self, system_prompt: str, segment: dict, user_message: str) -> dict:
        user_prompt = _USER_TEMPLATE.format(
            user_message=user_message,
            target_phrase=segment.get("target_phrase") or "",
            value_phrase=segment.get("value_phrase") or "",
        )
        try:
            response = self.llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
        except Exception as e:
            logger.warning("Slot LLM call failed: %s", e)
            raise SlotExtractionError(f"Slot LLM call failed: {e}") from e

        raw = getattr(response, "content", "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Slot LLM returned non-JSON: %r", raw)
            raise SlotExtractionError("Slot LLM did not return valid JSON.")

        if not isinstance(data, dict):
            raise SlotExtractionError("Slot LLM did not return a JSON object.")
        return data

    # ── Per-kind post-processing ────────────────────────────────

    def _post_process_property(self, raw: dict, user_message: str) -> SlotResult:
        warnings: list[str] = []
        prop = (raw.get("property") or "").strip()
        if not prop:
            raise SlotExtractionError("PROPERTY segment missing 'property' slot.")

        value = raw.get("value")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SlotExtractionError("PROPERTY segment missing 'value' slot.")

        if not _value_appears_in_message(value, user_message):
            warnings.append(
                f"Value {value!r} not found in your request — "
                "did the model substitute it? Please confirm before approving."
            )

        pset_raw = raw.get("pset")
        pset = pset_raw.strip() if isinstance(pset_raw, str) else ""
        return SlotResult(
            slots={"pset": pset, "property": prop, "value": value},
            warnings=warnings,
        )

    def _post_process_attribute(self, raw: dict, user_message: str) -> SlotResult:
        warnings: list[str] = []
        attr = (raw.get("attribute") or "").strip()
        if attr not in SAFE_ATTRIBUTES:
            raise SlotExtractionError(
                f"ATTRIBUTE segment 'attribute' must be one of {sorted(SAFE_ATTRIBUTES)}, got {attr!r}."
            )

        value = raw.get("value")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SlotExtractionError("ATTRIBUTE segment missing 'value' slot.")

        if not _value_appears_in_message(value, user_message):
            warnings.append(
                f"Value {value!r} not found in your request — please confirm before approving."
            )

        return SlotResult(slots={"attribute": attr, "value": value}, warnings=warnings)

    def _post_process_pset(self, raw: dict, user_message: str) -> SlotResult:
        warnings: list[str] = []
        op = (raw.get("operation") or "").strip().upper()
        if op not in ("ADD_PSET", "REMOVE_PSET"):
            raise SlotExtractionError(
                f"PSET segment 'operation' must be ADD_PSET or REMOVE_PSET, got {op!r}."
            )

        pset_name = (raw.get("pset_name") or "").strip()
        if not pset_name:
            raise SlotExtractionError("PSET segment missing 'pset_name'.")

        if op == "REMOVE_PSET":
            return SlotResult(
                slots={"operation": op, "pset_name": pset_name},
                warnings=warnings,
            )

        # ADD_PSET — properties dict optional but expected.
        props = raw.get("properties") or {}
        if not isinstance(props, dict):
            raise SlotExtractionError("PSET ADD_PSET 'properties' must be a JSON object.")

        # Groundedness on each value: the user must have written each
        # value somewhere in the message.
        for prop_name, prop_value in props.items():
            if not _value_appears_in_message(prop_value, user_message):
                warnings.append(
                    f"Pset {pset_name}.{prop_name} value {prop_value!r} not found in "
                    "your request — please confirm."
                )

        return SlotResult(
            slots={"operation": op, "pset_name": pset_name, "properties": props},
            warnings=warnings,
        )

    def _post_process_create(self, raw: dict) -> SlotResult:
        entity_class = (raw.get("entity_class") or "").strip()
        if not entity_class.startswith("Ifc"):
            raise SlotExtractionError(
                f"CREATE 'entity_class' must be a valid IFC class (Ifc*), got {entity_class!r}."
            )

        names_raw = raw.get("names") or []
        if not isinstance(names_raw, list):
            raise SlotExtractionError("CREATE 'names' must be a JSON array.")
        names = [str(n).strip() for n in names_raw if str(n).strip()]
        if not names:
            raise SlotExtractionError(
                "CREATE 'names' must contain at least one name. "
                "Please name the entities you want created."
            )

        parent_phrase = (raw.get("parent_phrase") or "").strip()

        return SlotResult(
            slots={
                "entity_class": entity_class,
                "names": names,
                "parent_phrase": parent_phrase,
            },
            warnings=[],
        )


def _value_appears_in_message(value, user_message: str) -> bool:
    """Lossy groundedness check: the value's string form must appear in
    the user message. Booleans and ``None`` are exempted because they
    rarely appear verbatim.
    """
    if value is None or isinstance(value, bool):
        return True
    s = str(value).strip()
    if not s:
        return False
    return s.lower() in (user_message or "").lower()
