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

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.llm import get_llm

from .llm_boundary import BoundaryError, BoundaryValidationError, call_structured
from .schemas import (
    AttributeSlots,
    CreateSlots,
    PropertySlots,
    PsetSlots,
    RelationshipSlots,
)

logger = logging.getLogger(__name__)


# Attributes that Tier 1's SET_ATTRIBUTE may touch. Keep in sync with
# the writeback skill (.claude/skills/writeback-ops.md).
SAFE_ATTRIBUTES = frozenset({"Name", "Description", "ObjectType", "Tag", "LongName"})

#: What a PROPERTY segment can ask for. "ADD" is deliberately absent — on
#: standard psets SET_PROPERTY is an upsert, so a separate add would only
#: create a way for "add X" to fail when X already exists.
_PROPERTY_OPERATIONS = frozenset({"SET", "REMOVE"})


# ── PROPERTY ──────────────────────────────────────────────────────

PROPERTY_SYSTEM_PROMPT = """\
You extract the property slots from a single IFC modification segment.

You are given:
  - The original user request (for value grounding only).
  - The segment's free-text value_phrase describing the property change.

Output ONLY this JSON:

{
  "operation": "SET" | "REMOVE",
  "pset": "<Pset name if user named one, else null>",
  "property": "<property name as the user wrote it>",
  "value": "<new value, copied verbatim from the user's wording, or null>"
}

Rules:
  - "operation" is REMOVE when the user asks to remove / delete / clear /
    strip / get rid of a property. Otherwise SET (this covers "set",
    "change", "update" AND "add" — adding is an upsert).
  - "value" must appear as a substring of the user request, copied verbatim.
    No paraphrasing, no unit conversion, no synonym substitution.
  - For REMOVE there is no new value: return "value": null. NEVER invent one,
    and never copy the entity name or the target phrase into "value".
  - "pset" is optional — if the user didn't name a pset, return null.
    DO NOT guess a pset.
  - Booleans: extract as JSON true / false (not strings).
  - Numbers: extract as JSON numbers when the user typed a bare number
    ("0.18", "120"); otherwise keep as a string.

Examples:

User: "set fire rating to EI120 on all walls"
Segment value_phrase: "FireRating to EI120"
{"operation": "SET", "pset": null, "property": "FireRating", "value": "EI120"}

User: "set Pset_WallCommon.IsExternal to true on Wall-001"
Segment value_phrase: "Pset_WallCommon.IsExternal to true"
{"operation": "SET", "pset": "Pset_WallCommon", "property": "IsExternal", "value": true}

User: "add FireRating EI90 to all walls"
Segment value_phrase: "FireRating EI90"
{"operation": "SET", "pset": null, "property": "FireRating", "value": "EI90"}

User: "remove Reference from all walls"
Segment value_phrase: "Reference"
{"operation": "REMOVE", "pset": null, "property": "Reference", "value": null}

User: "remove ExtendToStructure from wall :285330"
Segment value_phrase: "ExtendToStructure"
{"operation": "REMOVE", "pset": null, "property": "ExtendToStructure", "value": null}
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
You extract pset-family slots from a single IFC modification segment.

The user is doing ONE of these:
  (1) ADD_PSET           — attach a property set, optionally with initial props
  (2) REMOVE_PSET        — detach a whole property set
  (3) SET_MATERIAL       — assign / change the material on entities
  (4) SET_CLASSIFICATION — classify entities under a system + reference

Output ONLY this JSON, with the operation-specific keys:

ADD_PSET:
{"operation": "ADD_PSET",
 "pset_name": "<exact Pset_* name>",
 "properties": {"<Name>": <value>, ...}}

REMOVE_PSET:
{"operation": "REMOVE_PSET",
 "pset_name": "<exact Pset_* name>"}

SET_MATERIAL:
{"operation": "SET_MATERIAL",
 "material_name": "<material name verbatim>"}

SET_CLASSIFICATION:
{"operation": "SET_CLASSIFICATION",
 "system_name": "<classification system, e.g. Uniclass>",
 "reference": "<reference code, e.g. EF_25_10>"}

Rules:
  - "pset_name" must follow the IFC convention `Pset_<Something>`. If the
    user typed a generic word ("properties", "the pset"), this is NOT a
    valid pset_name — return ``"pset_name": ""`` and the caller will
    reject the request.
  - For ADD_PSET, "properties" is a flat dict; empty {} is allowed when
    the user didn't name initial values.
  - All extracted strings are copied verbatim from the user's wording.
  - Pick the operation that matches the user's verb:
      "add Pset_X" / "attach"            → ADD_PSET
      "remove Pset_X" / "delete pset"    → REMOVE_PSET
      "assign material" / "set material" → SET_MATERIAL
      "classify" / "set classification"  → SET_CLASSIFICATION

Examples:

User: "add Pset_Maintenance to all walls with Inspector=TBD and LastInspection=2026-01-01"
{"operation": "ADD_PSET", "pset_name": "Pset_Maintenance",
 "properties": {"Inspector": "TBD", "LastInspection": "2026-01-01"}}

User: "remove Pset_Custom from Wall-001"
{"operation": "REMOVE_PSET", "pset_name": "Pset_Custom"}

User: "assign concrete material to all walls"
{"operation": "SET_MATERIAL", "material_name": "concrete"}

User: "set the material of wall :285395 to Reinforced Concrete C30/37"
{"operation": "SET_MATERIAL", "material_name": "Reinforced Concrete C30/37"}

User: "classify all walls under FireSafety with reference FS-2026/EW"
{"operation": "SET_CLASSIFICATION",
 "system_name": "FireSafety", "reference": "FS-2026/EW"}

User: "add properties to walls"   ← pset_name unknown / generic
{"operation": "ADD_PSET", "pset_name": "", "properties": {}}
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


# ── RELATIONSHIP ──────────────────────────────────────────────────

RELATIONSHIP_SYSTEM_PROMPT = """\
You extract the destination from a single IFC "move" segment.

The user wants to move an existing entity into a different spatial
container — normally a building storey ("move Wall-01 to Level 2").
The segment's target_phrase already names WHAT moves; your only job is
to extract WHERE it goes.

Output ONLY this JSON:

{
  "destination_phrase": "<free-text naming the destination, or empty>",
  "relation": "container"
}

Rules:
  - "destination_phrase": copy the user's wording verbatim — "Level 2",
    "the ground floor", "Storey 03". Do NOT invent a destination and do
    NOT repeat the thing being moved.
  - Leave it as an empty string when the request names no destination;
    the caller will ask the user for one.
  - "relation" is always "container" — only spatial container moves are
    supported here.

Examples:

User: "move Wall-01 to Level 2"
Segment target_phrase: "Wall-01"
{"destination_phrase": "Level 2", "relation": "container"}

User: "move all the doors on the ground floor up to the first floor"
Segment target_phrase: "all the doors on the ground floor"
{"destination_phrase": "the first floor", "relation": "container"}

User: "relocate the inner wall"   ← no destination given
{"destination_phrase": "", "relation": "container"}
"""


# ── DELETE has no slots — target_phrase is the whole spec.


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
    """Slot extraction returned unusable data.

    ``boundary_errors`` carries the structured ``{code, path, hint}`` rows
    when the failure originated at the schema-validated LLM boundary, so a
    later retry can feed them back into the prompt.
    """

    def __init__(self, *args, boundary_errors: list[dict] | None = None) -> None:
        super().__init__(*args)
        self.boundary_errors: list[dict] = boundary_errors or []


@dataclass
class SlotResult:
    """The slots extracted for a segment, plus warning markers.

    ``slots`` is a kind-specific dict (see prompts above). ``warnings``
    captures groundedness or shape repair notes that the caller may
    want to surface to the user.
    """

    slots: dict
    warnings: list[str]


def _clean_str(value) -> str:
    """Strip a value if it's a string, else return empty string."""
    return value.strip() if isinstance(value, str) else ""


# ── Normalizers (pure, pre-validation) ─────────────────────────────
# Each coerces the LLM's loose JSON into the shape its pydantic schema
# expects. They do NOT reject — presence and grounding are the
# finalizers' job. They keep dict/list drift intact so the schema (not
# the normalizer) surfaces it as a retryable structural error.


def _normalize_property(data: object) -> dict:
    payload = data if isinstance(data, dict) else {}
    # Default to SET: an omitted operation means the model saw an ordinary
    # edit, and SET is upsert on standard psets. Never infer REMOVE.
    operation = _clean_str(payload.get("operation")).upper() or "SET"
    return {
        "operation": operation,
        "pset": _clean_str(payload.get("pset")),
        "property": _clean_str(payload.get("property")),
        "value": payload.get("value"),
    }


def _normalize_attribute(data: object) -> dict:
    payload = data if isinstance(data, dict) else {}
    return {
        "attribute": _clean_str(payload.get("attribute")),
        "value": payload.get("value"),
    }


def _normalize_pset(data: object) -> dict:
    payload = data if isinstance(data, dict) else {}
    props = payload.get("properties")
    return {
        "operation": _clean_str(payload.get("operation")).upper(),
        "pset_name": _clean_str(payload.get("pset_name")),
        # Falsy (None / {} / []) → {}; a non-empty list stays a list so the
        # schema rejects it and the boundary retries.
        "properties": props if props else {},
        "material_name": _clean_str(payload.get("material_name")),
        "system_name": _clean_str(payload.get("system_name")),
        "reference": _clean_str(payload.get("reference")),
    }


def _normalize_relationship(data: object) -> dict:
    payload = data if isinstance(data, dict) else {}
    relation = _clean_str(payload.get("relation")).lower()
    return {
        "destination_phrase": _clean_str(payload.get("destination_phrase")),
        # Only container moves are typed; anything else the model invents
        # is normalised away rather than failing the whole parse.
        "relation": relation if relation == "container" else "container",
    }


def _normalize_create(data: object) -> dict:
    payload = data if isinstance(data, dict) else {}
    names = payload.get("names")
    return {
        "entity_class": _clean_str(payload.get("entity_class")),
        # None → []; a non-list (e.g. a bare string) stays put so the schema
        # rejects it and the boundary retries.
        "names": [] if names is None else names,
        "parent_phrase": _clean_str(payload.get("parent_phrase")),
    }


# ── Finalizers (semantic, post-validation) ─────────────────────────
# Run on the validated pydantic model. They own the presence/grounding
# checks and their precise user-facing messages, raising
# SlotExtractionError (no LLM retry — a missing value can't be conjured).


def _finalize_property(model: PropertySlots, segment: dict, user_message: str) -> SlotResult:
    warnings: list[str] = []
    prop = model.property
    if not prop:
        raise SlotExtractionError("PROPERTY segment missing 'property' slot.")

    operation = model.operation if model.operation in _PROPERTY_OPERATIONS else "SET"

    if operation == "REMOVE":
        # A removal has no new value. Requiring one is what used to make the
        # model invent a value from the target phrase — "remove Reference from
        # all walls" wrote Reference='all walls' on every wall.
        return SlotResult(
            slots={"operation": "REMOVE", "pset": model.pset, "property": prop, "value": None},
            warnings=warnings,
        )

    value = model.value
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SlotExtractionError("PROPERTY segment missing 'value' slot.")

    if not _value_appears_in_message(value, user_message):
        warnings.append(
            f"Value {value!r} not found in your request — "
            "did the model substitute it? Please confirm before approving."
        )

    return SlotResult(
        slots={"operation": "SET", "pset": model.pset, "property": prop, "value": value},
        warnings=warnings,
    )


def _finalize_attribute(model: AttributeSlots, segment: dict, user_message: str) -> SlotResult:
    warnings: list[str] = []
    attr = model.attribute
    if attr not in SAFE_ATTRIBUTES:
        raise SlotExtractionError(
            f"ATTRIBUTE segment 'attribute' must be one of {sorted(SAFE_ATTRIBUTES)}, got {attr!r}."
        )

    value = model.value
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SlotExtractionError("ATTRIBUTE segment missing 'value' slot.")

    # Vague-value guard: "rename the door" → triage emits ATTRIBUTE with
    # target_phrase="the door", and the slot LLM hallucinates value="the
    # door" because no real new value was supplied. Reject when the
    # extracted value is just the target reference echoed back, or when
    # it equals the attribute's own name.
    target_phrase = segment.get("target_phrase") or ""
    if isinstance(value, str):
        value_norm = value.strip().lower()
        target_norm = target_phrase.strip().lower()
        if target_norm and value_norm == target_norm:
            raise SlotExtractionError(
                f"ATTRIBUTE segment {attr!r}: no new value was provided. "
                f"Please rephrase, e.g. 'rename {target_phrase} to <new name>'."
            )
        if value_norm == attr.lower():
            raise SlotExtractionError(
                f"ATTRIBUTE segment {attr!r}: extracted value {value!r} is "
                "the attribute name itself — no new value provided."
            )

    if not _value_appears_in_message(value, user_message):
        warnings.append(
            f"Value {value!r} not found in your request — please confirm before approving."
        )

    return SlotResult(slots={"attribute": attr, "value": value}, warnings=warnings)


def _finalize_pset(model: PsetSlots, segment: dict, user_message: str) -> SlotResult:
    warnings: list[str] = []
    op = model.operation
    if op not in ("ADD_PSET", "REMOVE_PSET", "SET_MATERIAL", "SET_CLASSIFICATION"):
        raise SlotExtractionError(
            "PSET segment 'operation' must be one of "
            "ADD_PSET / REMOVE_PSET / SET_MATERIAL / SET_CLASSIFICATION, "
            f"got {op!r}."
        )

    if op == "SET_MATERIAL":
        material_name = model.material_name
        if not material_name:
            raise SlotExtractionError(
                "SET_MATERIAL segment missing 'material_name'. Please name the material to assign."
            )
        if not _value_appears_in_message(material_name, user_message):
            warnings.append(
                f"Material {material_name!r} not found in your request — "
                "please confirm before approving."
            )
        return SlotResult(
            slots={"operation": op, "material_name": material_name},
            warnings=warnings,
        )

    if op == "SET_CLASSIFICATION":
        system_name = model.system_name
        reference = model.reference
        if not system_name:
            raise SlotExtractionError(
                "SET_CLASSIFICATION segment missing 'system_name'. "
                "Please name the classification system."
            )
        if not reference:
            raise SlotExtractionError(
                "SET_CLASSIFICATION segment missing 'reference'. "
                "Please provide the classification reference code."
            )
        return SlotResult(
            slots={"operation": op, "system_name": system_name, "reference": reference},
            warnings=warnings,
        )

    # ADD_PSET / REMOVE_PSET share the pset_name shape guard.
    pset_name = model.pset_name
    if not pset_name:
        raise SlotExtractionError(
            "PSET segment missing 'pset_name'. Please name the property "
            "set, e.g. 'Pset_Maintenance'."
        )
    # Generic words like "properties", "the pset", "stuff" leak through
    # the LLM extractor when the user didn't actually name a Pset_*.
    # Reject before they reach the writers.
    if not pset_name.lower().startswith("pset_"):
        raise SlotExtractionError(
            f"PSET segment 'pset_name' {pset_name!r} is not a valid IFC pset name. "
            "Please name the property set explicitly, e.g. 'Pset_Maintenance'."
        )

    if op == "REMOVE_PSET":
        return SlotResult(
            slots={"operation": op, "pset_name": pset_name},
            warnings=warnings,
        )

    # ADD_PSET — properties dict optional but expected. The schema already
    # guaranteed a dict; groundedness on each value stays here.
    props = model.properties
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


def _finalize_create(model: CreateSlots, segment: dict, user_message: str) -> SlotResult:
    entity_class = model.entity_class
    if not entity_class.startswith("Ifc"):
        raise SlotExtractionError(
            f"CREATE 'entity_class' must be a valid IFC class (Ifc*), got {entity_class!r}."
        )

    names = [str(n).strip() for n in model.names if str(n).strip()]
    # Generic-word guard: "create three new IfcZone entities" → LLM
    # hallucinates names=["three new"] from the quantifier phrase.
    # Drop entries that are just quantifier / placeholder noise so the
    # caller surfaces a clean "no names provided" rejection.
    names = [n for n in names if not _is_generic_create_name(n, entity_class)]
    if not names:
        raise SlotExtractionError(
            "CREATE 'names' must contain at least one specific name. "
            "Please name the entities you want created — e.g. 'Fire Zone A', "
            "'Storage-01' — not just a count or generic noun."
        )

    return SlotResult(
        slots={
            "entity_class": entity_class,
            "names": names,
            "parent_phrase": model.parent_phrase,
        },
        warnings=[],
    )


def _finalize_relationship(model, segment: dict, user_message: str) -> SlotResult:
    destination = model.destination_phrase
    if not destination:
        raise SlotExtractionError(
            "Move requests need a destination. Please say where the entity "
            "should go — e.g. 'move Wall-01 to Level 2'."
        )

    # Guard against the model echoing the thing being moved back as the
    # destination, which would resolve to the entity itself.
    target_phrase = (segment.get("target_phrase") or "").strip().lower()
    if target_phrase and destination.strip().lower() == target_phrase:
        raise SlotExtractionError(
            f"Could not tell where {segment.get('target_phrase', 'the entity')!r} "
            "should move to. Please name the destination storey."
        )

    return SlotResult(
        slots={"destination_phrase": destination, "relation": model.relation},
        warnings=[],
    )


@dataclass(frozen=True)
class _KindConfig:
    """The boundary wiring for one triage kind."""

    system_prompt: str
    schema: type
    normalizer: Callable[[object], object]
    finalizer: Callable[[object, dict, str], SlotResult]


# Kinds absent from this map (DELETE / OUT_OF_SCOPE / UNCLEAR / unknown)
# have no slots and short-circuit to an empty result — their target_phrase
# is the whole specification.
_KIND_CONFIG: dict[str, _KindConfig] = {
    "PROPERTY": _KindConfig(
        PROPERTY_SYSTEM_PROMPT, PropertySlots, _normalize_property, _finalize_property
    ),
    "ATTRIBUTE": _KindConfig(
        ATTRIBUTE_SYSTEM_PROMPT, AttributeSlots, _normalize_attribute, _finalize_attribute
    ),
    "PSET": _KindConfig(PSET_SYSTEM_PROMPT, PsetSlots, _normalize_pset, _finalize_pset),
    "CREATE": _KindConfig(CREATE_SYSTEM_PROMPT, CreateSlots, _normalize_create, _finalize_create),
    "RELATIONSHIP": _KindConfig(
        RELATIONSHIP_SYSTEM_PROMPT,
        RelationshipSlots,
        _normalize_relationship,
        _finalize_relationship,
    ),
}


class SlotExtractor:
    """Stage 2 dispatcher.

    Routes each segment's kind through the schema-validated LLM boundary
    (:func:`call_structured`) and runs the kind's semantic finalizer on
    the validated model. Returns structured slots or raises
    :class:`SlotExtractionError`.
    """

    def __init__(self, user=None, llm=None) -> None:
        self._user = user
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self._user, temperature=0.0, format_json=True)
        return self._llm

    def extract(
        self,
        segment: dict,
        user_message: str,
        prior_errors: Sequence[BoundaryError] = (),
    ) -> SlotResult:
        """Extract slots for one segment.

        Kinds with no slots (DELETE / RELATIONSHIP / OUT_OF_SCOPE /
        UNCLEAR) return an empty result without an LLM call.

        Args:
            segment:      One triage segment dict.
            user_message: The raw request, for grounding.
            prior_errors: Structured errors from an earlier failed attempt
                          (retry-of-failure flow), injected into the prompt.
        """
        kind = segment.get("kind")
        config = _KIND_CONFIG.get(kind)
        if config is None:
            return SlotResult(slots={}, warnings=[])

        user_prompt = _USER_TEMPLATE.format(
            user_message=user_message,
            target_phrase=segment.get("target_phrase") or "",
            value_phrase=segment.get("value_phrase") or "",
        )
        try:
            model = call_structured(
                self.llm,
                stage=f"slots:{kind.lower()}",
                system_prompt=config.system_prompt,
                user_prompt=user_prompt,
                schema=config.schema,
                normalizers=(config.normalizer,),
                prior_errors=prior_errors,
            )
        except BoundaryValidationError as e:
            raise SlotExtractionError(str(e), boundary_errors=e.errors_as_dicts()) from e
        except Exception as e:
            logger.warning("Slot LLM call failed: %s", e)
            raise SlotExtractionError(f"Slot LLM call failed: {e}") from e

        return config.finalizer(model, segment, user_message)


# Words that the LLM frequently hallucinates as CREATE names when the
# user only supplied a quantifier or generic placeholder. Each is a
# whole-token comparison (case-insensitive); multi-word names containing
# any of these tokens (e.g. "Fire Zone A") still pass.
_GENERIC_CREATE_TOKENS = frozenset(
    {
        "new",
        "the",
        "a",
        "an",
        "entity",
        "entities",
        "instance",
        "instances",
        "object",
        "objects",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "several",
        "many",
        "some",
    }
)


def _is_generic_create_name(name: str, entity_class: str) -> bool:
    """Detect placeholder names like 'three new' / 'IfcZone entity' that
    the LLM emits when the user didn't actually name a specific entity.

    Returns True when *every* whitespace-separated token in ``name`` is
    either a pure number, a generic quantifier/placeholder, or the
    entity class itself. A name with at least one specific token (e.g.
    'Fire Zone A', 'Storage-01') is considered specific.
    """
    cleaned = name.strip()
    if not cleaned:
        return True
    cls_norm = (entity_class or "").strip().lower()
    cls_short = cls_norm.removeprefix("ifc")
    for token in cleaned.split():
        t = token.lower().strip(".,;:!?\"'()")
        if not t:
            continue
        if t.isdigit():
            continue
        if t in _GENERIC_CREATE_TOKENS:
            continue
        if t == cls_norm or t == cls_short:
            continue
        return False
    return True


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
