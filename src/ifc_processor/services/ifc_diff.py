# ifc_processor/services/ifc_diff.py
"""Round-trip integrity check for IFC files: snapshot, re-read, diff.

The writeback pipeline promises to change *only* what the journal says and to
leave the rest of the model untouched. ``verify_journal`` confirms the first
half (the requested change is present); this module confirms the second half
(nothing else moved). Together they turn "write, re-read, confirm nothing
degraded" into a measured column rather than a claim.

A snapshot captures three things that a lossy save would corrupt:

* **Population** — entity count per IFC class and the set of ``GlobalId``s.
  A dropped or duplicated entity shows up here.
* **Geometry** — one SHA-256 per product over its representation tree
  (cartesian points, placements, profile parameters). Any geometric drift,
  including a coordinate rounding change, flips the hash. Geometry is out of
  scope for writeback, so this must be identical before and after.
* **Properties** — the flattened property sets of every rooted entity, plus
  the safe attributes writeback may set. This is what *should* differ, and
  only where the journal says.

Typical use::

    before = IfcSnapshot.from_file(path)
    ... apply journal ...
    after = IfcSnapshot.from_file(path)
    diff = diff_snapshots(before, after)
    unexpected = diff.unexpected(allowed=journal.affected_global_ids)
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util

logger = logging.getLogger(__name__)

# Attributes writeback is allowed to set (mirrors Tier1Validator.SAFE_ATTRIBUTES).
TRACKED_ATTRIBUTES = ("Name", "Description", "ObjectType", "Tag", "LongName")


@dataclass(frozen=True)
class PropertyChange:
    """One (entity, pset, property) whose value differs between snapshots."""

    global_id: str
    pset: str
    prop: str
    before: object
    after: object

    def as_dict(self) -> dict:
        return {
            "global_id": self.global_id,
            "pset": self.pset,
            "prop": self.prop,
            "before": _jsonable(self.before),
            "after": _jsonable(self.after),
        }


@dataclass(frozen=True)
class IfcSnapshot:
    """Immutable structural fingerprint of one IFC file."""

    path: str
    schema: str
    type_counts: dict[str, int]
    global_ids: frozenset[str]
    geometry: dict[str, str]  # GlobalId -> representation hash
    properties: dict[str, dict[str, dict[str, object]]]  # GlobalId -> pset -> prop -> value
    attributes: dict[str, dict[str, object]]  # GlobalId -> attribute -> value

    @property
    def entity_total(self) -> int:
        return sum(self.type_counts.values())

    @classmethod
    def from_file(cls, path: str | Path) -> IfcSnapshot:
        """Open ``path`` read-only and capture population, geometry, properties."""
        model = ifcopenshell.open(str(path))
        try:
            return cls.from_model(model, path=str(path))
        finally:
            del model

    @classmethod
    def from_model(cls, model, *, path: str = "") -> IfcSnapshot:
        """Build a snapshot from an already-open model."""
        type_counts = Counter(entity.is_a() for entity in model)

        global_ids: set[str] = set()
        geometry: dict[str, str] = {}
        properties: dict[str, dict[str, dict[str, object]]] = {}
        attributes: dict[str, dict[str, object]] = {}

        for entity in model.by_type("IfcRoot"):
            gid = entity.GlobalId
            global_ids.add(gid)
            properties[gid] = _flatten_psets(entity)
            attributes[gid] = _tracked_attributes(entity)
            if entity.is_a("IfcProduct"):
                geometry[gid] = _geometry_hash(entity)

        return cls(
            path=path,
            schema=model.schema,
            type_counts=dict(type_counts),
            global_ids=frozenset(global_ids),
            geometry=geometry,
            properties=properties,
            attributes=attributes,
        )


@dataclass
class IfcDiff:
    """What changed between two snapshots. Empty means a lossless round trip."""

    schema_changed: bool = False
    type_count_delta: dict[str, int] = field(default_factory=dict)
    added_global_ids: frozenset[str] = frozenset()
    removed_global_ids: frozenset[str] = frozenset()
    geometry_changed: frozenset[str] = frozenset()
    property_changes: list[PropertyChange] = field(default_factory=list)
    attribute_changes: list[PropertyChange] = field(default_factory=list)

    @property
    def population_ok(self) -> bool:
        """No entity appeared or vanished, no per-class count moved."""
        return not (self.added_global_ids or self.removed_global_ids or self.type_count_delta)

    @property
    def geometry_ok(self) -> bool:
        return not self.geometry_changed

    @property
    def is_empty(self) -> bool:
        return (
            not self.schema_changed
            and self.population_ok
            and self.geometry_ok
            and not self.property_changes
            and not self.attribute_changes
        )

    def unexpected(
        self,
        *,
        allowed: frozenset[str] | set[str] = frozenset(),
        allow_population_change: bool = False,
    ) -> list[str]:
        """Human-readable list of changes outside what the caller permitted.

        ``allowed`` is the set of GlobalIds the journal declared it would touch.
        Property and attribute changes on those entities are fine; anything
        else — geometry anywhere, properties on other entities, population
        changes unless the journal creates/deletes entities — is reported.
        """
        problems: list[str] = []
        if self.schema_changed:
            problems.append("schema identifier changed")
        if self.geometry_changed:
            sample = ", ".join(sorted(self.geometry_changed)[:3])
            problems.append(
                f"geometry changed on {len(self.geometry_changed)} product(s): {sample}"
            )
        if not allow_population_change and not self.population_ok:
            problems.append(
                f"population changed: +{len(self.added_global_ids)} / "
                f"-{len(self.removed_global_ids)} entities, "
                f"class deltas {self.type_count_delta}"
            )
        for change in self.property_changes + self.attribute_changes:
            if change.global_id in allowed:
                continue
            problems.append(
                f"{change.global_id} {change.pset}.{change.prop}: "
                f"{change.before!r} -> {change.after!r} (not in journal)"
            )
        return problems

    def as_dict(self) -> dict:
        return {
            "schema_changed": self.schema_changed,
            "type_count_delta": self.type_count_delta,
            "added_global_ids": sorted(self.added_global_ids),
            "removed_global_ids": sorted(self.removed_global_ids),
            "geometry_changed": sorted(self.geometry_changed),
            "property_changes": [c.as_dict() for c in self.property_changes],
            "attribute_changes": [c.as_dict() for c in self.attribute_changes],
        }


def diff_snapshots(before: IfcSnapshot, after: IfcSnapshot) -> IfcDiff:
    """Compare two snapshots. Order matters: ``before`` is the reference."""
    delta = {
        ifc_type: after.type_counts.get(ifc_type, 0) - before.type_counts.get(ifc_type, 0)
        for ifc_type in set(before.type_counts) | set(after.type_counts)
    }
    delta = {k: v for k, v in delta.items() if v}

    common = before.global_ids & after.global_ids
    geometry_changed = frozenset(
        gid
        for gid in common
        if gid in before.geometry
        and gid in after.geometry
        and before.geometry[gid] != after.geometry[gid]
    )

    property_changes = [
        change
        for gid in sorted(common)
        for change in _diff_nested(
            gid, before.properties.get(gid, {}), after.properties.get(gid, {})
        )
    ]
    attribute_changes = [
        change
        for gid in sorted(common)
        for change in _diff_flat(
            gid, "", before.attributes.get(gid, {}), after.attributes.get(gid, {})
        )
    ]

    return IfcDiff(
        schema_changed=before.schema != after.schema,
        type_count_delta=delta,
        added_global_ids=after.global_ids - before.global_ids,
        removed_global_ids=before.global_ids - after.global_ids,
        geometry_changed=geometry_changed,
        property_changes=property_changes,
        attribute_changes=attribute_changes,
    )


def diff_files(before: str | Path, after: str | Path) -> IfcDiff:
    """Convenience: snapshot both paths and diff them."""
    return diff_snapshots(IfcSnapshot.from_file(before), IfcSnapshot.from_file(after))


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _flatten_psets(entity) -> dict[str, dict[str, object]]:
    """Property sets as plain nested dicts; ``id`` bookkeeping keys stripped."""
    try:
        psets = element_util.get_psets(entity)
    except Exception:  # noqa: BLE001 — a malformed pset must not abort the snapshot
        logger.debug("get_psets failed for %s", entity.GlobalId, exc_info=True)
        return {}
    return {
        name: {k: _normalise(v) for k, v in props.items() if k != "id"}
        for name, props in psets.items()
    }


def _tracked_attributes(entity) -> dict[str, object]:
    return {name: getattr(entity, name) for name in TRACKED_ATTRIBUTES if hasattr(entity, name)}


def _geometry_hash(product) -> str:
    """SHA-256 over the product's placement and representation subtree.

    Walks every entity reachable from ``ObjectPlacement`` and ``Representation``
    and feeds the tuple of its non-entity attributes into the digest. Entity
    references are followed rather than hashed by STEP id, so renumbering on
    save does not change the hash while any coordinate or parameter does.
    """
    digest = hashlib.sha256()
    seen: set[int] = set()
    stack = [
        getattr(product, "ObjectPlacement", None),
        getattr(product, "Representation", None),
    ]
    while stack:
        node = stack.pop()
        if node is None or not hasattr(node, "id"):
            continue
        if node.id() in seen:
            continue
        seen.add(node.id())
        digest.update(node.is_a().encode())
        for value in node:
            _feed(digest, value, stack)
    return digest.hexdigest()


def _feed(digest, value, stack: list) -> None:
    """Hash a scalar / collection or push a referenced entity for later."""
    if value is None:
        digest.update(b"\x00")
    elif hasattr(value, "id") and hasattr(value, "is_a"):
        stack.append(value)
    elif isinstance(value, (tuple, list)):
        digest.update(b"(")
        for item in value:
            _feed(digest, item, stack)
        digest.update(b")")
    else:
        digest.update(repr(value).encode())
        digest.update(b"|")


def _normalise(value):
    """Collapse IfcOpenShell wrapper types to plain JSON-ish values."""
    if hasattr(value, "wrappedValue"):
        return value.wrappedValue
    if isinstance(value, (list, tuple)):
        return tuple(_normalise(v) for v in value)
    return value


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


# ── Diff helpers ──────────────────────────────────────────────────────────────


def _diff_nested(gid: str, before: dict, after: dict) -> list[PropertyChange]:
    changes: list[PropertyChange] = []
    for pset in sorted(set(before) | set(after)):
        changes.extend(_diff_flat(gid, pset, before.get(pset, {}), after.get(pset, {})))
    return changes


def _diff_flat(gid: str, pset: str, before: dict, after: dict) -> list[PropertyChange]:
    return [
        PropertyChange(gid, pset, prop, before.get(prop), after.get(prop))
        for prop in sorted(set(before) | set(after))
        if before.get(prop) != after.get(prop)
    ]
