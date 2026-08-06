# ifc_processor/schema_data/lookup.py
"""High-level queries over the generated IFC schema metadata.

Three consumers in mind:
- retrieval / deterministic answers: ``expand_type("IfcWall")`` so a count of
  walls includes IfcWallStandardCase;
- property-term grounding: ``resolve_property_term("fire rating", "IfcDoor")``
  → ``("Pset_DoorCommon", "FireRating")``;
- validation: ``is_standard_pset_property`` before trusting an LLM-proposed
  property name.

All functions are pure and cached per schema.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ifc_processor.schema_data import entities, normalize_schema, psets

_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_term(term: str) -> str:
    """Lowercase alphanumeric squash: 'Fire Rating' / 'fire_rating' → 'firerating'."""
    return _ALNUM_RE.sub("", (term or "").lower())


@lru_cache(maxsize=4)
def _children_map(schema: str) -> dict[str, tuple[str, ...]]:
    """parent → direct children, derived once from the entity table."""
    children: dict[str, list[str]] = {}
    for name, record in entities(schema).items():
        parent = record["parent"]
        if parent:
            children.setdefault(parent, []).append(name)
    return {parent: tuple(kids) for parent, kids in children.items()}


@lru_cache(maxsize=1024)
def expand_type(ifc_type: str, schema: str = "IFC4") -> frozenset[str]:
    """The subtype closure of a type, itself included.

    Unknown types return a singleton set — callers can still query with the
    literal name and fail downstream with a clear "no rows" instead of here.
    """
    schema = normalize_schema(schema)
    result: set[str] = set()
    stack = [ifc_type]
    children = _children_map(schema)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, ()))
    return frozenset(result)


def ancestors(ifc_type: str, schema: str = "IFC4") -> tuple[str, ...]:
    """The parent chain from the type itself up to the root, in order."""
    schema = normalize_schema(schema)
    table = entities(schema)
    chain: list[str] = []
    current: str | None = ifc_type
    while current and current in table and current not in chain:
        chain.append(current)
        current = table[current]["parent"]
    return tuple(chain)


def is_subtype_of(child: str, ancestor: str, schema: str = "IFC4") -> bool:
    """True when `child` is `ancestor` or one of its descendants."""
    return ancestor in ancestors(child, schema)


@lru_cache(maxsize=1024)
def psets_for(ifc_type: str, schema: str = "IFC4") -> tuple[str, ...]:
    """Standard psets applicable to a type, honouring inheritance.

    Pset_WallCommon lists IfcWall as applicable; IfcWallStandardCase inherits
    it through the ancestor chain.
    """
    schema = normalize_schema(schema)
    lineage = set(ancestors(ifc_type, schema)) or {ifc_type}
    return tuple(
        name for name, record in psets(schema).items() if lineage & set(record["applicable"])
    )


def properties_of(pset_name: str, schema: str = "IFC4") -> dict[str, dict]:
    """The property table of one standard pset ({} when unknown)."""
    return psets(normalize_schema(schema)).get(pset_name, {}).get("properties", {})


def is_standard_pset_property(pset_name: str, prop_name: str, schema: str = "IFC4") -> bool:
    """True when the (pset, property) pair exists in the standard catalogue."""
    return prop_name in properties_of(pset_name, schema)


def resolve_property_term(
    term: str, entity_type: str = "", schema: str = "IFC4"
) -> tuple[str, str] | None:
    """Ground a natural-language property term in the standard catalogue.

    Searches psets applicable to `entity_type` (all psets when omitted) for a
    property whose normalized name matches the normalized term — exactly
    first, then by containment either way. Returns (pset_name, property_name)
    or None.
    """
    needle = _normalize_term(term)
    if not needle:
        return None

    schema = normalize_schema(schema)
    candidate_psets = psets_for(entity_type, schema) if entity_type else tuple(psets(schema))

    containment_hit: tuple[str, str] | None = None
    for pset_name in candidate_psets:
        for prop_name in properties_of(pset_name, schema):
            normalized = _normalize_term(prop_name)
            if normalized == needle:
                return (pset_name, prop_name)
            if containment_hit is None and (needle in normalized or normalized in needle):
                containment_hit = (pset_name, prop_name)
    return containment_hit
