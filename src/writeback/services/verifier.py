# writeback/services/verifier.py
"""Verify, deterministically, what the code did to the scratch copy (spec V-1..V-3).

Everything here is a pure function over ``IfcDiff.as_dict()`` and the request
text, so the same rules serve the pipeline, the card, the approve check and
the benchmark.

- :func:`scope_error` — anything changed outside the selection, or any geometry
  or schema change, is one error string for the repair loop.
- :func:`aggregate_rows` — rows grouped by (pset, property, before → after) with
  counts, plus one row per added or removed entity.
- :func:`flag_rows` — **the flag rule**: a row is flagged when something
  about it is not in the request. Its new value, when that is not a non-empty
  case-insensitive substring of the request (booleans and None, a removal, are
  exempt); or the name of the property it changed, when no squashed form of the
  name, no camel word of :data:`_MIN_NAME_WORD` letters or more and no
  :data:`PROPERTY_SYNONYMS` entry appears in the request; or its **prior value**,
  when the same property is overwritten from more than one distinct before-value
  across the proposal (a scalar removal is exempt, same reasoning as the value
  check: nothing new is written over the mix, it is just gone). Every added or
  removed entity is a flagged row. ``DiffRow.flag_reason`` says which; a flag is
  never a repair (a false positive on an unlisted phrasing costs one tick).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

_SAMPLE = 5

#: Words a request uses for a property or relationship whose name it never
#: spells out. Keyed by property or relationship attribute name; matched squashed
#: (lowercase alphanumerics) against the squashed request.
PROPERTY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "FireRating": ("fire resistance", "fire class", "fireproof", "fire proof"),
    "ThermalTransmittance": ("u-value", "u value", "thermal"),
    "AcousticRating": ("sound", "acoustic", "noise"),
    "IsExternal": ("external", "exterior", "internal", "interior", "outside", "inside"),
    "LoadBearing": ("load-bearing", "load bearing", "structural", "bearing"),
    "Container": ("move", "storey", "story", "floor", "level", "relocate"),
    "Parent": ("move", "storey", "story", "floor", "level", "under", "nest", "aggregate"),
    "Materials": ("material",),
    "Classifications": ("classif", "uniclass", "omniclass"),
    "Groups": ("group", "zone"),
    "TypeObject": ("type",),
    "Name": ("rename", "call"),
    "LongName": ("rename", "call", "name"),
    "Description": ("describe",),
}
#: Badge text per ``DiffRow.flag_reason``.
FLAG_LABELS: dict[str, str] = {
    "value": "value not in request",
    "property": "property not in request",
    "added": "entity added",
    "removed": "entity removed",
    "heterogeneous": "overwrites different existing values",
}
#: A camel word shorter than this never names a property on its own ("is", "has").
_MIN_NAME_WORD = 4
_WORD_RE = re.compile(r"[a-z0-9]+")
_SQUASH_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class DiffRow:
    """One aggregated line of the card."""

    key: str
    kind: str  # property | attribute | added | removed
    pset: str
    prop: str
    before: object
    after: object
    count: int
    global_ids: list[str] = field(default_factory=list)
    flagged: bool = False
    flag_reason: str = ""  # value | property | added | removed | ""

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        if self.kind == "added":
            return f"{self.prop or 'entity'} added"
        if self.kind == "removed":
            return f"{self.prop or 'entity'} removed"
        return _change_label(self.pset, self.prop)


def scope_error(diff: dict, targets: Iterable[str]) -> str | None:
    """None when every change sits on a target, else one error string.

    Mirrors ``IfcDiff.unexpected(allowed=targets, allow_population_change=True)``
    on the serialised diff: population changes are flagged rows, never
    violations; geometry and schema changes always are; a new object attached
    to a pre-existing entity outside the selection is one.
    """
    allowed = set(targets)
    problems: list[str] = []
    if diff.get("schema_changed"):
        problems.append("the schema identifier changed")
    geometry = diff.get("geometry_changed") or []
    if geometry:
        problems.append(f"geometry changed on {len(geometry)} product(s)")
    strays = [
        f"{row['global_id']} {_change_label(row['pset'], row['prop'])}"
        for row in _rows(diff)
        if row["global_id"] not in allowed
    ]
    if strays:
        extra = f" (+{len(strays) - _SAMPLE} more)" if len(strays) > _SAMPLE else ""
        problems.append(
            f"{len(strays)} change(s) on entities that select() did not return: {', '.join(strays[:_SAMPLE])}{extra}"
        )
    added = set(diff.get("added_global_ids") or [])
    classes = diff.get("added_objects") or {}
    for gid, parent in sorted((diff.get("added_attachments") or {}).items()):
        if parent in allowed or parent in added:
            continue
        problems.append(
            f"new {classes.get(gid, 'entity')} {gid} was attached to {parent}, which select() "
            "did not return (select() must return the storey, building or project a new "
            "entity belongs to)"
        )
    if not problems:
        return None
    return "modify() changed entities outside the selection: " + "; ".join(problems) + "."


def is_empty(diff: dict) -> bool:
    """True when the run changed nothing the card could show.

    Population is judged on objects (``added_objects`` / ``removed_objects``),
    not on every rooted entity: a property set or relationship that comes and
    goes is visible through its property or attribute rows, never on its own.
    """
    return not (
        diff.get("schema_changed")
        or diff.get("geometry_changed")
        or diff.get("property_changes")
        or diff.get("attribute_changes")
        or _population(diff, "added")
        or _population(diff, "removed")
    )


def population_ids(diff: dict, kind: str) -> list[str]:
    """Sorted GlobalIds of the objects added or removed (``kind`` is ``added`` | ``removed``)."""
    return sorted(_population(diff, kind))


def aggregate_rows(diff: dict) -> list[DiffRow]:
    """Group the diff by (kind, pset, property, before, after); one row per added/removed entity."""
    grouped: dict[tuple, DiffRow] = {}
    for kind, rows in (
        ("property", diff.get("property_changes") or []),
        ("attribute", diff.get("attribute_changes") or []),
    ):
        for row in rows:
            ident = (kind, row["pset"], row["prop"], _norm(row["before"]), _norm(row["after"]))
            entry = grouped.get(ident)
            if entry is None:
                entry = grouped[ident] = DiffRow(
                    _key(ident), kind, row["pset"], row["prop"], row["before"], row["after"], 0
                )
            entry.count += 1
            entry.global_ids.append(row["global_id"])
    out = list(grouped.values())
    for gid, ifc_type in _population(diff, "added").items():
        out.append(DiffRow(_key(("added", gid)), "added", "", ifc_type, None, gid, 1, [gid]))
    for gid, ifc_type in _population(diff, "removed").items():
        out.append(DiffRow(_key(("removed", gid)), "removed", "", ifc_type, gid, None, 1, [gid]))
    return out


def flag_rows(rows: list[DiffRow], request: str) -> list[DiffRow]:
    """Apply the flag rule in place and return the rows, flagged ones first."""
    mixed = _mixed_prior_value_keys(rows)
    for row in rows:
        row.flag_reason = _flag_reason(row, request, mixed)
        row.flagged = bool(row.flag_reason)
    return sorted(rows, key=lambda r: not r.flagged)


def camel_to_words(name: str) -> str:
    """Convert CamelCase to space-separated words. 'FireRating' → 'fire rating'."""
    return re.sub(r"([A-Z])", r" \1", name).strip().lower()


def flagged_keys(diff: dict, request: str) -> set[str]:
    """The keys the approve request must carry for this diff and request."""
    return {row.key for row in flag_rows(aggregate_rows(diff), request) if row.flagged}


# ── Internals ──────────────────────────────────────────────────────


def _flag_reason(row: DiffRow, request: str, mixed: set[tuple[str, str, str]]) -> str:
    """Why the row needs a tick: population, property name, value, or a mixed prior value; "" if none."""
    if row.kind in ("added", "removed"):
        return row.kind
    if not _names_property(row, request):
        return "property"
    if _value_absent(row, request.casefold()):
        return "value"
    return "heterogeneous" if (row.kind, row.pset, row.prop) in mixed else ""


def _mixed_prior_value_keys(rows: list[DiffRow]) -> set[tuple[str, str, str]]:
    """(kind, pset, prop) groups the proposal overwrites from more than one before-value.

    A mass write over a heterogeneous prior state is where the user cannot know what
    they are overwriting: nine walls at EI 90 and 271 with no rating at all both become
    EI60, and "EI60" being the literal value typed makes the value check blind to it.
    Counts distinct before-values, never compares them against each other, so it needs
    no domain knowledge of what outranks what. A scalar removal (``after is None``) is
    excluded from the count: deleting a property erases whatever was there, so a mixed
    prior state is not a specific new value silently overwriting it, the reasoning the
    value check already applies to removals.
    """
    by_key: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        if row.kind not in ("property", "attribute") or row.after is None:
            continue
        by_key.setdefault((row.kind, row.pset, row.prop), set()).add(_norm(row.before))
    return {key for key, befores in by_key.items() if len(befores) > 1}


def _names_property(row: DiffRow, request: str) -> bool:
    """True when the request names what the row changed (property, attribute or whole pset).

    Three tests, any one suffices: the squashed name is a substring of the squashed request
    ("firerating" in "changefireratingon…"); a camel word of :data:`_MIN_NAME_WORD` letters
    or more, plural stripped, is a substring of a request word ("material" in "materials");
    a :data:`PROPERTY_SYNONYMS` entry appears in the request ("u-value" for ThermalTransmittance).
    """
    name = row.prop or row.pset
    if not name:
        return True
    lowered = request.casefold()
    squashed = _SQUASH_RE.sub("", lowered)
    if _SQUASH_RE.sub("", name.casefold()) in squashed:
        return True
    words = _WORD_RE.findall(lowered)
    for word in camel_to_words(name).split():
        stem = word[:-1] if word.endswith("s") and len(word) > _MIN_NAME_WORD else word
        if len(stem) >= _MIN_NAME_WORD and any(stem in w for w in words):
            return True
    return any(_SQUASH_RE.sub("", s) in squashed for s in PROPERTY_SYNONYMS.get(name, ()))


def _value_absent(row: DiffRow, haystack: str) -> bool:
    """The value half of the rule: the new value is not in the request (booleans and None exempt)."""
    if isinstance(row.after, bool) or row.after is None:
        return False
    if isinstance(row.after, (list, tuple)):
        # A relationship value (materials, groups…): every new name must be in the request.
        before = set(row.before) if isinstance(row.before, (list, tuple)) else set()
        new_names = [_value_text(v).casefold() for v in row.after if v not in before]
        return not new_names or any(not n or n not in haystack for n in new_names)
    text = _value_text(row.after).casefold()
    return not text or text not in haystack


def _value_text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return ", ".join(_value_text(v) for v in value)
    return str(value).strip()


def _population(diff: dict, kind: str) -> dict[str, str]:
    """``{gid: class}`` of the objects added or removed; older diffs carry ids only."""
    typed = diff.get(f"{kind}_objects")
    if isinstance(typed, dict):
        return typed
    return dict.fromkeys(diff.get(f"{kind}_global_ids") or [], "")


def _rows(diff: dict) -> list[dict]:
    return list(diff.get("property_changes") or []) + list(diff.get("attribute_changes") or [])


def _change_label(pset: str, prop: str) -> str:
    """``Pset.Prop`` for a property, ``Prop`` for an attribute, ``Pset`` for a whole pset."""
    if pset and prop:
        return f"{pset}.{prop}"
    return pset or prop


def _norm(value):
    return repr(value)


def _key(ident: tuple) -> str:
    return hashlib.sha1(repr(ident).encode()).hexdigest()[:12]
