# writeback/services/message_normalizer.py
"""
Deterministic pre-LLM text normalizer for IFC modification requests.

Cleans up user input BEFORE it hits the LLM:
  - Maps BIM jargon → IFC property names
  - Normalizes entity type shorthand
  - Expands common abbreviations

No LLM calls. Pure dictionary + regex. Runs in microseconds.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Property Name Aliases ──────────────────────────────────
# Maps casual/shorthand → canonical IFC property name
_PROPERTY_ALIASES: dict[str, str] = {
    # Fire
    "fire rating": "FireRating",
    "fire rated": "FireRating",
    "fire resistance": "FireRating",
    "fire-rating": "FireRating",
    "fire-rated": "FireRating",
    # Thermal
    "u-value": "ThermalTransmittance",
    "u value": "ThermalTransmittance",
    "thermal transmittance": "ThermalTransmittance",
    "thermal-transmittance": "ThermalTransmittance",
    # Boolean props
    "is external": "IsExternal",
    "is loadbearing": "IsLoadBearing",
    "is load bearing": "IsLoadBearing",
    "load bearing": "IsLoadBearing",
    "loadbearing": "IsLoadBearing",
    "load-bearing": "IsLoadBearing",
    # Common props
    "acoustic rating": "AcousticRating",
    "acoustic-rating": "AcousticRating",
    "surface spread of flame": "SurfaceSpreadOfFlame",
    "handicap accessible": "HandicapAccessible",
    "glazing area fraction": "GlazingAreaFraction",
    "glazing area": "GlazingAreaFraction",
    # Attributes (not in psets)
    "object type": "ObjectType",
    "long name": "LongName",
}

# ── Entity Type Aliases ────────────────────────────────────
# Maps shorthand/plural → canonical IFC type string
_ENTITY_ALIASES: dict[str, str] = {
    "wall": "wall",
    "walls": "walls",
    "ext wall": "external wall",
    "ext walls": "external walls",
    "ext. wall": "external wall",
    "ext. walls": "external walls",
    "int wall": "internal wall",
    "int walls": "internal walls",
    "int. wall": "internal wall",
    "int. walls": "internal walls",
    "col": "column",
    "cols": "columns",
    "win": "window",
    "wins": "windows",
    "slab": "slab",
    "slabs": "slabs",
    "beam": "beam",
    "beams": "beams",
    "stair": "stair",
    "stairs": "stairs",
    "door": "door",
    "doors": "doors",
    "roof": "roof",
    "roofs": "roofs",
    "railing": "railing",
    "railings": "railings",
    "curtain wall": "curtain wall",
    "curtain walls": "curtain walls",
}

# ── Value Aliases ──────────────────────────────────────────
_VALUE_ALIASES: dict[str, str] = {
    "true": "true",
    "yes": "true",
    "false": "false",
    "no": "false",
    "none": "false",
}

# Pre-compile: sort by length descending so longer patterns match first
_PROPERTY_PATTERN = re.compile(
    "|".join(re.escape(k) for k in sorted(_PROPERTY_ALIASES, key=len, reverse=True)),
    re.IGNORECASE,
)
_ENTITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ENTITY_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def normalize(message: str) -> str:
    """
    Normalize a user message before LLM classification.

    Applies in order:
        1. Property name canonicalization
        2. Entity type expansion
        3. Whitespace cleanup

    Returns the cleaned message. Original is never modified.
    """
    original = message
    result = message

    # 1. Property aliases → canonical names
    result = _PROPERTY_PATTERN.sub(
        lambda m: _PROPERTY_ALIASES[m.group(0).lower()],
        result,
    )

    # 2. Entity type shorthand → expanded form
    result = _ENTITY_PATTERN.sub(
        lambda m: _ENTITY_ALIASES.get(m.group(0).lower(), m.group(0)),
        result,
    )

    # 3. Collapse multiple spaces
    result = re.sub(r"  +", " ", result).strip()

    if result != original:
        logger.debug(f"Normalized: '{original}' → '{result}'")

    return result