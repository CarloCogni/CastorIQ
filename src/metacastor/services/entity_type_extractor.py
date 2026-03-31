# metacastor/services/entity_type_extractor.py
"""
Lightweight keyword scanner that extracts IFC entity type hints from raw user
messages *before* classification (pre-LLM).

Used by the skill retriever as a hard-gate pre-filter to prevent cross-domain
poisoning in few-shot retrieval (e.g. "set fire rating on walls" should not
retrieve IfcSpace examples even if the embedding similarity is high).

Falls back to an empty list — safe, meaning "no filter, use all candidates."
"""

import re

logger = __import__("logging").getLogger(__name__)

# Lowercase keyword → canonical IFC class name
IFC_TYPE_KEYWORDS: dict[str, str] = {
    "wall": "IfcWall",
    "walls": "IfcWall",
    "door": "IfcDoor",
    "doors": "IfcDoor",
    "window": "IfcWindow",
    "windows": "IfcWindow",
    "slab": "IfcSlab",
    "slabs": "IfcSlab",
    "floor": "IfcSlab",
    "floors": "IfcSlab",
    "ceiling": "IfcSlab",
    "column": "IfcColumn",
    "columns": "IfcColumn",
    "pillar": "IfcColumn",
    "beam": "IfcBeam",
    "beams": "IfcBeam",
    "space": "IfcSpace",
    "spaces": "IfcSpace",
    "room": "IfcSpace",
    "rooms": "IfcSpace",
    "stair": "IfcStair",
    "stairs": "IfcStair",
    "staircase": "IfcStair",
    "roof": "IfcRoof",
    "roofs": "IfcRoof",
    "railing": "IfcRailing",
    "railings": "IfcRailing",
    "furniture": "IfcFurnishingElement",
    "pipe": "IfcPipeSegment",
    "pipes": "IfcPipeSegment",
    "duct": "IfcDuctSegment",
    "ducts": "IfcDuctSegment",
}

# Matches any already-qualified IFC class name the user typed directly
_IFC_CLASS_RE = re.compile(r"\bIfc[A-Z][a-zA-Z]+\b")


def extract_entity_types(user_message: str) -> list[str]:
    """
    Extract IFC entity type hints from a raw user message pre-classification.

    Two-pass approach:
      1. Keyword map: lowercased word tokens matched against IFC_TYPE_KEYWORDS.
      2. Regex pass: captures already-qualified "IfcXxx" class names.

    Returns:
        Sorted, deduplicated list of canonical IFC class strings.
        Empty list if nothing matched — retriever treats this as "no filter."
    """
    found: set[str] = set()

    lower = user_message.lower()
    # Tokenise on word boundaries for the keyword map
    tokens = re.findall(r"\b\w+\b", lower)
    for token in tokens:
        if ifc_type := IFC_TYPE_KEYWORDS.get(token):
            found.add(ifc_type)

    # Direct IfcXxx mentions (case-sensitive regex on original message)
    for match in _IFC_CLASS_RE.finditer(user_message):
        found.add(match.group())

    return sorted(found)
