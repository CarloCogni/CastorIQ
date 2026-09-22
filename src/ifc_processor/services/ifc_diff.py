# ifc_processor/services/ifc_diff.py
"""Round-trip integrity check for IFC files: snapshot, re-read, diff.

The writeback pipeline promises to change *only* the entities the generated
``select`` returned and to leave the rest of the model untouched. This module
measures that: a snapshot before the code runs, a snapshot after, and a diff
whose ``unexpected`` rows are scope violations. The same diff feeds the card,
the commit, the index refresh and the benchmark.

A snapshot captures three things that a lossy save would corrupt:

* **Population** — entity count per IFC class and the set of ``GlobalId``s.
  A dropped or duplicated entity shows up here.
* **Geometry** — one SHA-256 per product over its representation tree
  (cartesian points, placements, profile parameters). Any geometric drift,
  including a coordinate rounding change, flips the hash. Geometry is out of
  scope for writeback, so this must be identical before and after.
* **Properties** — the flattened property sets of every rooted entity, plus
  the tracked attributes and, for objects and type objects (never the
  project context), the relationship-derived values (container, materials,
  classifications, groups, type object) that a modification changes without
  touching a property. This is what *should* differ, and only on the selected entities.
  A snapshot holds each entity's **own** values only: property sets and
  materials inherited from the type object belong to the type's row, so
  editing a type pset or assigning a material to a type is one change on
  the type, not one on every occurrence. A property set that appears or
  vanishes with no properties in it is one row with an empty property name.

Typical use::

    before = IfcSnapshot.from_model(model)
    ... targets = select(model); modify(model, targets) ...
    after = IfcSnapshot.from_model(model)
    diff = diff_snapshots(before, after)
    unexpected = diff.unexpected(allowed=target_global_ids, allow_population_change=True)
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util
import ifcopenshell.util.placement as placement_util
import numpy as np

logger = logging.getLogger(__name__)

# Attributes the diff tracks on every rooted entity.
TRACKED_ATTRIBUTES = ("Name", "Description", "ObjectType", "Tag", "LongName")
# Relationship-derived values tracked on every IfcObject and IfcTypeObject,
# so a container move, a re-parenting (a space moved to another storey), a
# material or classification assignment or a group membership shows up in
# the diff as an attribute change on the entity itself. The project context
# is not tracked: registering a classification system associates it with the
# project, which is bookkeeping, not a change a user asked for. Container,
# Parent and TypeObject only exist on occurrences; a type object reports None.
RELATIONSHIP_ATTRIBUTES = (
    "Container",
    "Parent",
    "Materials",
    "Classifications",
    "Groups",
    "TypeObject",
)


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
    types: dict[str, str] = field(default_factory=dict)  # GlobalId -> IFC class
    # GlobalIds that are IfcObject (products, groups…). Type objects are not
    # listed: a created type is never "an entity added" on the card.
    objects: frozenset[str] = frozenset()
    # IfcObject GlobalId -> GlobalId of what it is attached to: its direct
    # decomposition parent, else its direct spatial container. What a new
    # object hangs from must be inside the selection (``IfcDiff.unexpected``).
    parents: dict[str, str] = field(default_factory=dict)

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
        types: dict[str, str] = {}
        objects: set[str] = set()
        parents: dict[str, str] = {}

        for entity in model.by_type("IfcRoot"):
            gid = entity.GlobalId
            global_ids.add(gid)
            types[gid] = entity.is_a()
            properties[gid] = _flatten_psets(entity)
            attributes[gid] = _tracked_attributes(entity)
            if entity.is_a("IfcObject") or entity.is_a("IfcTypeObject"):
                attributes[gid].update(_relationship_attributes(entity))
            if entity.is_a("IfcObject"):
                objects.add(gid)
                attached_to = _attachment(entity)
                if attached_to is not None:
                    parents[gid] = attached_to.GlobalId
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
            types=types,
            objects=frozenset(objects),
            parents=parents,
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
    # The IfcObject subset of the population change, with classes: what a
    # user means by "an entity was created / deleted" (relationships and
    # property sets that come and go with a change are not listed here).
    added_objects: dict[str, str] = field(default_factory=dict)
    removed_objects: dict[str, str] = field(default_factory=dict)
    # Added object GlobalId -> GlobalId of the pre-existing object it was
    # attached to (decomposition parent or spatial container), so a creation
    # hung from something outside the selection is a scope violation.
    added_attachments: dict[str, str] = field(default_factory=dict)

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

        ``allowed`` is the set of GlobalIds ``select`` returned. Property and
        attribute changes on those entities are fine; anything else — geometry
        anywhere, properties on other entities, population changes unless the
        caller allows them — is reported.
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
                f"{change.before!r} -> {change.after!r} (not in selection)"
            )
        for gid, parent in sorted(self.added_attachments.items()):
            if parent in allowed or parent in self.added_global_ids:
                continue
            problems.append(
                f"new {self.added_objects.get(gid, 'entity')} {gid} attached to {parent}, "
                "which is outside the selection"
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
            "added_objects": dict(sorted(self.added_objects.items())),
            "removed_objects": dict(sorted(self.removed_objects.items())),
            "added_attachments": dict(sorted(self.added_attachments.items())),
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

    added = after.global_ids - before.global_ids
    removed = before.global_ids - after.global_ids
    added_objects = {gid: after.types[gid] for gid in added if gid in after.objects}
    return IfcDiff(
        schema_changed=before.schema != after.schema,
        type_count_delta=delta,
        added_global_ids=added,
        removed_global_ids=removed,
        geometry_changed=geometry_changed,
        property_changes=property_changes,
        attribute_changes=attribute_changes,
        added_objects=added_objects,
        removed_objects={gid: before.types[gid] for gid in removed if gid in before.objects},
        added_attachments={
            gid: after.parents[gid] for gid in sorted(added_objects) if gid in after.parents
        },
    )


def diff_files(before: str | Path, after: str | Path) -> IfcDiff:
    """Convenience: snapshot both paths and diff them."""
    return diff_snapshots(IfcSnapshot.from_file(before), IfcSnapshot.from_file(after))


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _flatten_psets(entity) -> dict[str, dict[str, object]]:
    """The entity's own property sets as nested dicts; ``id`` keys stripped.

    ``should_inherit=False``: a type's psets are the type's, so an edit on the
    type object is a change on the type row only, never on every occurrence.
    """
    try:
        psets = element_util.get_psets(entity, should_inherit=False)
    except Exception:  # noqa: BLE001 — a malformed pset must not abort the snapshot
        logger.debug("get_psets failed for %s", entity.GlobalId, exc_info=True)
        return {}
    return {
        name: {k: _normalise(v) for k, v in props.items() if k != "id"}
        for name, props in psets.items()
    }


def _tracked_attributes(entity) -> dict[str, object]:
    return {name: getattr(entity, name) for name in TRACKED_ATTRIBUTES if hasattr(entity, name)}


def _relationship_attributes(entity) -> dict[str, object]:
    """Container, parent, materials, classifications, groups and type object of one entity.

    Each value is a name or a sorted tuple of names, so a diff row reads
    ``Materials: () -> ('Concrete',)`` rather than a STEP id. Materials are
    the entity's **own** (``should_inherit=False``): a material assigned to
    a type is one row on the type, never one on each occurrence. Container,
    parent (the object this one decomposes, e.g. a space's storey) and type
    object exist on occurrences only; a type object reports None.
    """
    try:
        is_occurrence = entity.is_a("IfcObject")
        # Direct containment only: an element hosted in a moved wall keeps its
        # own storey relation, so the move is one row on the wall, not a
        # violation on every window in it.
        container = (
            element_util.get_container(entity, should_get_direct=True) if is_occurrence else None
        )
        type_object = element_util.get_type(entity) if is_occurrence else None
        parent = _decomposition_parent(entity) if is_occurrence else None
        return {
            "Container": getattr(container, "Name", None) if container is not None else None,
            "Parent": getattr(parent, "Name", None) if parent is not None else None,
            "Materials": tuple(sorted(_material_names(entity))),
            "Classifications": tuple(sorted(_classification_refs(entity))),
            "Groups": tuple(sorted(_group_names(entity))),
            "TypeObject": getattr(type_object, "Name", None) if type_object is not None else None,
        }
    except Exception:  # noqa: BLE001 — a malformed relationship must not abort the snapshot
        logger.debug("relationship attributes failed for %s", entity.GlobalId, exc_info=True)
        return {}


def _decomposition_parent(entity):
    """The object ``entity`` directly decomposes (IfcRelAggregates / IfcRelNests), or None."""
    for rel in getattr(entity, "Decomposes", None) or ():
        return rel.RelatingObject
    return None


def _attachment(entity):
    """What a new object hangs from: its decomposition parent, else its direct container."""
    try:
        parent = _decomposition_parent(entity)
        if parent is not None:
            return parent
        return element_util.get_container(entity, should_get_direct=True)
    except Exception:  # noqa: BLE001 — a malformed relationship must not abort the snapshot
        logger.debug("attachment failed for %s", entity.GlobalId, exc_info=True)
        return None


def _material_names(entity) -> list[str]:
    try:
        return [m.Name or "" for m in element_util.get_materials(entity, should_inherit=False)]
    except Exception:  # noqa: BLE001
        return []


def _classification_refs(entity) -> list[str]:
    refs = []
    for rel in getattr(entity, "HasAssociations", None) or ():
        if not rel.is_a("IfcRelAssociatesClassification"):
            continue
        ref = rel.RelatingClassification
        ident = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None)
        refs.append(str(ident or getattr(ref, "Name", None) or ""))
    return refs


def _group_names(entity) -> list[str]:
    return [
        rel.RelatingGroup.Name or ""
        for rel in getattr(entity, "HasAssignments", None) or ()
        if rel.is_a("IfcRelAssignsToGroup")
    ]


def _geometry_hash(product) -> str:
    """SHA-256 over the product's world placement and representation subtree.

    The placement is hashed as the resolved 4×4 world matrix (rounded), so
    re-parenting an element to another storey while it stays where it is —
    what ``spatial.assign_container`` does — is not a geometry change, while
    any move is. The representation is walked entity by entity and every
    non-entity attribute is fed into the digest; references are followed
    rather than hashed by STEP id, so renumbering on save does not change the
    hash while any coordinate or parameter does.
    """
    digest = hashlib.sha256()
    seen: set[int] = set()
    placement = getattr(product, "ObjectPlacement", None)
    stack = [getattr(product, "Representation", None)]
    if placement is not None:
        try:
            matrix = placement_util.get_local_placement(placement)
            digest.update(np.round(np.asarray(matrix, dtype=float), 6).tobytes())
        except Exception:  # noqa: BLE001 — fall back to walking the placement tree
            stack.append(placement)
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
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return repr(value)


# ── Diff helpers ──────────────────────────────────────────────────────────────


def _diff_nested(gid: str, before: dict, after: dict) -> list[PropertyChange]:
    """Property rows per pset, plus one presence row for a pset added or removed empty."""
    changes: list[PropertyChange] = []
    for pset in sorted(set(before) | set(after)):
        rows = _diff_flat(gid, pset, before.get(pset, {}), after.get(pset, {}))
        if not rows and (pset in before) != (pset in after):
            rows = [
                PropertyChange(
                    gid, pset, "", pset if pset in before else None, pset if pset in after else None
                )
            ]
        changes.extend(rows)
    return changes


def _diff_flat(gid: str, pset: str, before: dict, after: dict) -> list[PropertyChange]:
    return [
        PropertyChange(gid, pset, prop, before.get(prop), after.get(prop))
        for prop in sorted(set(before) | set(after))
        if before.get(prop) != after.get(prop)
    ]
