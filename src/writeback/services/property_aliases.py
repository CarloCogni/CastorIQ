# writeback/services/property_aliases.py
"""Canonical IFC property names for the labels documents and models use.

Specifications say "U-value", "fire resistance class" or "R'w"; the index
holds ``ThermalTransmittance``, ``FireRating`` and ``AcousticRating``. This
table is the one place that mapping lives: the conflict scanner uses it to
find the entities a requirement chunk is about and to look up the property a
finding claims, and the RAV benchmark uses it to match findings to its key.
Sharing the table is what keeps the benchmark from measuring a different
vocabulary than production runs.
"""

from __future__ import annotations

# Squashed label (lowercase, alphanumerics and apostrophes only) → canonical name.
PROPERTY_ALIASES: dict[str, str] = {
    "firerating": "FireRating",
    "fireresistance": "FireRating",
    "fireresistanceclass": "FireRating",
    "fireresistancerating": "FireRating",
    "fireclass": "FireRating",
    "thermaltransmittance": "ThermalTransmittance",
    "uvalue": "ThermalTransmittance",
    "u": "ThermalTransmittance",
    "acousticrating": "AcousticRating",
    "soundreductionindex": "AcousticRating",
    "soundinsulation": "AcousticRating",
    "rw": "AcousticRating",
    "r'w": "AcousticRating",
    "loadbearing": "LoadBearing",
    "isexternal": "IsExternal",
    "external": "IsExternal",
    "extendtostructure": "ExtendToStructure",
}


def squash(text: str) -> str:
    """Lowercase and drop everything but alphanumerics and apostrophes."""
    return "".join(ch for ch in text.casefold() if ch.isalnum() or ch == "'")


def canonical_property(name: str) -> str:
    """Normalise a property label to its canonical spelling.

    Strips spaces, hyphens, underscores and case, then looks the result up in
    ``PROPERTY_ALIASES``. Unknown names come back stripped but otherwise as-is,
    so a property the table never mentions is still a clean string.
    """
    return PROPERTY_ALIASES.get(squash(name), name.strip())
