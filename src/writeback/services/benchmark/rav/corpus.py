# writeback/services/benchmark/rav/corpus.py
"""Load and validate the RAV ground-truth key.

The key is a JSON file (``fixtures/benchmark/rav/key.json``) with two parts:
named entity groups (GlobalId lists) and cases that reference those groups.
Keeping the groups named means a case reads as "external_walls" rather than
three opaque GlobalIds, and adding a wall to the model means editing one list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXPECTED_VALUES = frozenset({"conflict", "no_conflict"})
SEVERITY_VALUES = frozenset({"clear", "marginal", "missing", "none"})

# Property names the scanner may use for the same thing. Findings are matched
# to key cases through this table, so "U-value" counts as ThermalTransmittance.
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


class RavCorpusError(ValueError):
    """The key file is malformed."""


def canonical_property(name: str) -> str:
    """Normalise a property label to the key's canonical spelling.

    Strips spaces, hyphens, underscores and case, then looks the result up in
    ``PROPERTY_ALIASES``. Unknown names come back stripped but otherwise as-is,
    so a finding on a property the key never mentions is still a clean string.
    """
    squashed = "".join(ch for ch in name.casefold() if ch.isalnum() or ch == "'")
    return PROPERTY_ALIASES.get(squashed, name.strip())


@dataclass(frozen=True)
class KeyCase:
    """One labelled requirement from the key."""

    id: str
    document: str
    section: str
    group: str
    global_ids: tuple[str, ...]
    ifc_type: str
    pset: str
    property: str
    ifc_value: object
    document_value: str
    expected: str
    severity: str

    @property
    def is_conflict(self) -> bool:
        return self.expected == "conflict"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "document": self.document,
            "section": self.section,
            "group": self.group,
            "global_ids": list(self.global_ids),
            "ifc_type": self.ifc_type,
            "property": self.property,
            "ifc_value": self.ifc_value,
            "document_value": self.document_value,
            "expected": self.expected,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class RavCorpus:
    """The parsed key: entity groups plus cases."""

    ifc: str
    groups: dict[str, tuple[str, ...]]
    cases: tuple[KeyCase, ...]

    @property
    def conflict_cases(self) -> list[KeyCase]:
        return [c for c in self.cases if c.is_conflict]

    @property
    def negative_cases(self) -> list[KeyCase]:
        return [c for c in self.cases if not c.is_conflict]

    @property
    def documents(self) -> list[str]:
        return sorted({c.document for c in self.cases})

    def triples(self) -> int:
        """Number of (entity, property) opportunities the key scores."""
        return sum(len(c.global_ids) for c in self.cases)


def load_key(path: str | Path) -> RavCorpus:
    """Parse and validate ``key.json``. Raises :class:`RavCorpusError`."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RavCorpusError(f"cannot read key {path}: {e}") from e

    groups = {name: tuple(ids) for name, ids in (data.get("entities") or {}).items() if ids}
    if not groups:
        raise RavCorpusError("key has no entity groups")

    cases = tuple(_parse_case(raw, groups) for raw in data.get("cases") or [])
    if not cases:
        raise RavCorpusError("key has no cases")

    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RavCorpusError(f"duplicate case ids: {', '.join(duplicates)}")

    return RavCorpus(ifc=str(data.get("ifc", "")), groups=groups, cases=cases)


def _parse_case(raw: dict, groups: dict[str, tuple[str, ...]]) -> KeyCase:
    case_id = str(raw.get("id") or "?")
    missing = [k for k in ("document", "entities", "property", "expected") if not raw.get(k)]
    if missing:
        raise RavCorpusError(f"case {case_id}: missing {', '.join(missing)}")

    group = raw["entities"]
    if group not in groups:
        raise RavCorpusError(f"case {case_id}: unknown entity group {group!r}")

    expected = raw["expected"]
    if expected not in EXPECTED_VALUES:
        raise RavCorpusError(f"case {case_id}: expected must be one of {sorted(EXPECTED_VALUES)}")

    severity = raw.get("severity", "none")
    if severity not in SEVERITY_VALUES:
        raise RavCorpusError(f"case {case_id}: severity must be one of {sorted(SEVERITY_VALUES)}")
    if (expected == "conflict") == (severity == "none"):
        raise RavCorpusError(
            f"case {case_id}: conflicts need a real severity, no_conflict needs 'none'"
        )

    return KeyCase(
        id=case_id,
        document=str(raw["document"]),
        section=str(raw.get("section", "")),
        group=group,
        global_ids=groups[group],
        ifc_type=str(raw.get("ifc_type", "")),
        pset=str(raw.get("pset", "")),
        property=canonical_property(str(raw["property"])),
        ifc_value=raw.get("ifc_value"),
        document_value=str(raw.get("document_value", "")),
        expected=expected,
        severity=severity,
    )
