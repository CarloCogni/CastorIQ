# ifc_processor/services/journal.py
"""
Mutation-journal IR — the shared artifact every writeback tier produces.

A :class:`MutationJournal` is an append-only, per-entity list of typed
:class:`Mutation` records with old/new values captured at build time.
One executor (``journal_executor.JournalExecutor``) replays any journal;
diff previews and DB sync derive from the same records, so there is a
single source of truth for "what will change / what changed".

Pure library code — no LLM, no Django. Adapted from the overlay-journal
pattern in ifc-lite (``packages/mutations``): the source file is never
touched until the journal is baked onto a copy and atomically swapped in.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from .ifc_writer import EntityChange

JOURNAL_SCHEMA_VERSION = 1

_FINGERPRINT_CHUNK = 1024 * 1024

# Sentinel pset labels for ops whose "pset" is not a real IFC property set.
# They ride in the EntityChange so the DB sync and git diff can tell an
# entity-lifecycle row from a property row.
_ENTITY_PSET = "(entity)"
_CODE_PSET = "(code)"


class JournalDecodeError(Exception):
    """A persisted journal dict could not be decoded into a MutationJournal."""


class MutationOp(str, Enum):
    """Closed set of typed operations a journal may contain."""

    # Tier 1 — single property / attribute ops
    SET_PROPERTY = "SET_PROPERTY"
    ADD_PROPERTY = "ADD_PROPERTY"
    REMOVE_PROPERTY = "REMOVE_PROPERTY"
    SET_ATTRIBUTE = "SET_ATTRIBUTE"
    # Tier 2 — pset / classification / material ops
    ADD_PSET = "ADD_PSET"
    REMOVE_PSET = "REMOVE_PSET"
    SET_CLASSIFICATION = "SET_CLASSIFICATION"
    SET_MATERIAL = "SET_MATERIAL"
    # Tier 3 — entity lifecycle / relationship ops
    CREATE_ENTITY = "CREATE_ENTITY"
    DELETE_ENTITY = "DELETE_ENTITY"
    ASSIGN_RELATIONSHIP = "ASSIGN_RELATIONSHIP"
    RUN_CODE = "RUN_CODE"


@dataclass(frozen=True)
class Mutation:
    """One typed change to one entity. The atom of the writeback IR.

    ``old_value`` is a JSON scalar snapshot captured when the journal is
    built (from the DB index). The executor re-reads the authoritative
    old value from the IFC model at apply time and reports drift via
    :class:`AppliedMutation.stale`.
    """

    id: str
    op: MutationOp
    global_id: str
    entity_name: str = ""
    ifc_type: str = ""
    pset: str = ""
    prop: str = ""
    attribute: str = ""
    old_value: object = None
    new_value: object = None
    value_type: str = ""
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MutationJournal:
    """Immutable, serializable journal of mutations against one IFC file."""

    journal_id: str
    ifc_file_id: str
    source_tier: int
    base_fingerprint: str
    captured_at: str
    mutations: tuple[Mutation, ...]
    schema_version: int = JOURNAL_SCHEMA_VERSION

    @property
    def affected_global_ids(self) -> frozenset[str]:
        """Distinct GlobalIds touched by this journal (empty ids excluded)."""
        return frozenset(m.global_id for m in self.mutations if m.global_id)

    def to_json_dict(self) -> dict:
        """Serialize for a Django JSONField (``ModificationProposal.changes``)."""
        payload = asdict(self)
        payload["mutations"] = [{**asdict(m), "op": m.op.value} for m in self.mutations]
        return payload

    @classmethod
    def from_json_dict(cls, data: dict) -> MutationJournal:
        """Decode a persisted journal dict. Raises :class:`JournalDecodeError`."""
        if not isinstance(data, dict):
            raise JournalDecodeError(f"Journal payload is not a dict: {type(data).__name__}")

        version = data.get("schema_version")
        if version != JOURNAL_SCHEMA_VERSION:
            raise JournalDecodeError(f"Unsupported journal schema_version: {version!r}")

        raw_mutations = data.get("mutations")
        if not isinstance(raw_mutations, list) or not raw_mutations:
            raise JournalDecodeError("Journal has no mutations.")

        mutations: list[Mutation] = []
        for index, raw in enumerate(raw_mutations):
            if not isinstance(raw, dict):
                raise JournalDecodeError(f"Mutation #{index} is not a dict.")
            try:
                op = MutationOp(raw.get("op"))
            except ValueError as e:
                raise JournalDecodeError(
                    f"Mutation #{index} has unknown op: {raw.get('op')!r}"
                ) from e
            mutations.append(
                Mutation(
                    id=str(raw.get("id") or ""),
                    op=op,
                    global_id=str(raw.get("global_id") or ""),
                    entity_name=str(raw.get("entity_name") or ""),
                    ifc_type=str(raw.get("ifc_type") or ""),
                    pset=str(raw.get("pset") or ""),
                    prop=str(raw.get("prop") or ""),
                    attribute=str(raw.get("attribute") or ""),
                    old_value=raw.get("old_value"),
                    new_value=raw.get("new_value"),
                    value_type=str(raw.get("value_type") or ""),
                    params=raw.get("params") or {},
                )
            )

        return cls(
            journal_id=str(data.get("journal_id") or ""),
            ifc_file_id=str(data.get("ifc_file_id") or ""),
            source_tier=int(data.get("source_tier") or 0),
            base_fingerprint=str(data.get("base_fingerprint") or ""),
            captured_at=str(data.get("captured_at") or ""),
            mutations=tuple(mutations),
        )


@dataclass(frozen=True)
class AppliedMutation:
    """One mutation after execution, with the authoritative old value.

    ``result`` carries facts that only exist *after* the write ran — most
    importantly the GlobalId IfcOpenShell mints for a created entity, which
    the proposed mutation cannot know. Everything downstream (the git diff
    and the DB sync) reads identity from here first, so execution-time
    reality wins over the proposal's guess.
    """

    mutation: Mutation
    actual_old_value: object
    stale: bool
    result: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AppliedJournal:
    """Result of replaying a full journal against the IFC file."""

    journal: MutationJournal
    applied: tuple[AppliedMutation, ...]

    @property
    def stale_count(self) -> int:
        return sum(1 for a in self.applied if a.stale)


def new_mutation_id() -> str:
    """Generate a short unique id for a Mutation record."""
    return f"mut_{uuid.uuid4().hex[:12]}"


def new_journal_id() -> str:
    """Generate a short unique id for a MutationJournal."""
    return f"jrn_{uuid.uuid4().hex[:12]}"


def compute_fingerprint(ifc_path: str | Path) -> str:
    """SHA-256 of the IFC file on disk — pins the model state a journal targets."""
    digest = hashlib.sha256()
    with open(ifc_path, "rb") as fh:
        while chunk := fh.read(_FINGERPRINT_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def applied_to_entity_changes(applied: AppliedJournal) -> list[EntityChange]:
    """Transition adapter: journal results → legacy ``EntityChange`` rows.

    Keeps ``GitCommit.diff_data`` and ``_sync_entity_properties`` working
    unchanged while the strangler migration is in flight. The ``"(removed)"``
    new-value sentinel is produced here; the Tier 2 pset sentinels
    ``"(material)"`` / ``"(classification)"`` ride in ``Mutation.pset`` so the
    default branch reproduces the legacy ``EntityChange`` shape verbatim.
    """
    changes: list[EntityChange] = []
    for item in applied.applied:
        m = item.mutation
        result = item.result or {}

        # Execution-time identity wins: a CREATE has no GlobalId until the
        # writer mints one, and RUN_CODE reports whatever it touched.
        global_id = result.get("global_id") or m.global_id
        entity_name = result.get("name") or m.entity_name
        ifc_type = result.get("ifc_type") or m.ifc_type

        old = item.actual_old_value
        old_label = "(none)" if old is None else str(old)
        new_label = str(m.new_value)

        if m.op == MutationOp.SET_ATTRIBUTE:
            pset_label, prop_label = "(attribute)", m.attribute
        elif m.op == MutationOp.CREATE_ENTITY:
            pset_label, prop_label = _ENTITY_PSET, "CREATE"
            old_label = "(does not exist)"
            new_label = f"{ifc_type}: {entity_name}".strip(": ")
        elif m.op == MutationOp.DELETE_ENTITY:
            pset_label, prop_label = _ENTITY_PSET, "DELETE"
            new_label = "(deleted)"
        elif m.op == MutationOp.ASSIGN_RELATIONSHIP:
            pset_label, prop_label = _ENTITY_PSET, "CONTAINER"
            old_label = str(old) if old else "(uncontained)"
        elif m.op == MutationOp.RUN_CODE:
            # Generated code self-reports each change; mirror the legacy
            # Tier3Executor row shape so git history stays comparable.
            pset_label = _CODE_PSET
            prop_label = str(result.get("description", ""))
            old_label = str(result.get("old_value", ""))
            new_label = str(result.get("new_value", ""))
        else:
            pset_label, prop_label = m.pset, m.prop

        if m.op in (MutationOp.REMOVE_PROPERTY, MutationOp.REMOVE_PSET):
            new_label = "(removed)"

        changes.append(
            EntityChange(
                global_id=global_id,
                entity_name=entity_name,
                ifc_type=ifc_type,
                pset=pset_label,
                property=prop_label,
                old_value=old_label,
                new_value=new_label,
            )
        )
    return changes
