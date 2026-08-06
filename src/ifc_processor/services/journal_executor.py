# ifc_processor/services/journal_executor.py
"""
Unified journal executor — replays a MutationJournal with temp-copy atomicity.

The original IFC file is NEVER written in place. The executor:
    1. verifies the file fingerprint against the journal's pin,
    2. copies the file to a temp path in the same directory,
    3. applies every mutation to the temp copy via per-op handlers,
    4. re-serializes the temp copy, then atomically ``os.replace``s it
       over the original (atomic on the same volume, Windows + POSIX).

Any failure at any step deletes the temp copy and leaves the original
byte-identical — corruption prevention by construction, not recovery
(deferred-bake pattern from ifc-lite's export layer).

PoC scope: Tier 1 handlers (SET_PROPERTY / ADD_PROPERTY / REMOVE_PROPERTY /
SET_ATTRIBUTE) delegating to :class:`Tier1Writer`. Tier 2/3 handlers land in
later migration phases.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .code_sandbox import DEFAULT_TIMEOUT_SECONDS, CodeSandboxError, run_code_subprocess
from .ifc_writer import EntityChange
from .journal import (
    AppliedJournal,
    AppliedMutation,
    Mutation,
    MutationJournal,
    MutationOp,
    compute_fingerprint,
)
from .tier2_writer import Tier2Writer
from .tier3_writer import Tier3Writer

logger = logging.getLogger(__name__)


class JournalExecutionError(Exception):
    """A mutation could not be applied. Carries the failed mutation id."""

    def __init__(self, message: str, mutation_id: str = "") -> None:
        super().__init__(message)
        self.mutation_id = mutation_id


class JournalStaleError(JournalExecutionError):
    """The IFC file changed since the journal was captured — re-propose."""


def _normalize_snapshot(value: object) -> str:
    """Render a journal old-value snapshot the way Tier1Writer renders it."""
    if value is None:
        return "(none)"
    return str(value)


def _applied_from_change(mutation: Mutation, change: EntityChange) -> AppliedMutation:
    """Build an AppliedMutation from one EntityChange a handler returned.

    Staleness only means "the DB snapshot the journal captured no longer
    matches the model". It is only meaningful when a snapshot exists — ops
    that add fresh data (ADD_*, SET_MATERIAL/SET_CLASSIFICATION whose old
    value the DB index doesn't carry) snapshot ``None`` and must never be
    flagged stale.
    """
    actual_old = change.old_value
    expected_old = _normalize_snapshot(mutation.old_value)
    stale = mutation.old_value is not None and actual_old != expected_old
    return AppliedMutation(mutation=mutation, actual_old_value=actual_old, stale=stale)


@dataclass
class _RunContext:
    """Per-run scratch state shared across handlers within one apply().

    REMOVE_PSET is a whole-pset writer op that returns one EntityChange per
    property, but the journal carries one mutation per (entity, property).
    This memoizes the single ``remove_pset`` call per (global_id, pset) and
    matches its per-property changes back to the individual mutations.
    """

    removed_psets: dict[tuple[str, str], dict[str, EntityChange]] = field(default_factory=dict)


# ── Tier 1 handlers ────────────────────────────────────────────────
# Every handler returns a list of AppliedMutation (0..N) so the executor
# loop can `extend`. T1 ops are always 1:1, so they return a 1-element list.


def _handle_set_property(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    changes = writer.t1.set_property([m.global_id], m.pset, m.prop, m.new_value)
    return [_applied_from_change(m, changes[0])]


def _handle_add_property(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    changes = writer.t1.add_property([m.global_id], m.pset, m.prop, m.new_value)
    return [_applied_from_change(m, changes[0])]


def _handle_remove_property(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    changes = writer.t1.remove_property([m.global_id], m.pset, m.prop)
    return [_applied_from_change(m, changes[0])]


def _handle_set_attribute(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    changes = writer.t1.set_attribute([m.global_id], m.attribute, m.new_value)
    return [_applied_from_change(m, changes[0])]


# ── Tier 2 handlers ────────────────────────────────────────────────


def _handle_add_pset(writer: Tier2Writer, m: Mutation, ctx: _RunContext) -> list[AppliedMutation]:
    # One property per mutation. add_pset skips a property already present,
    # returning [] — a no-op mutation contributes no change row.
    changes = writer.add_pset([m.global_id], m.pset, {m.prop: m.new_value})
    return [_applied_from_change(m, c) for c in changes]


def _handle_remove_pset(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    # remove_pset removes the whole pset at once; call it once per
    # (entity, pset) and hand each property mutation its own change row.
    key = (m.global_id, m.pset)
    if key not in ctx.removed_psets:
        changes = writer.remove_pset([m.global_id], m.pset)
        ctx.removed_psets[key] = {c.property: c for c in changes}
    change = ctx.removed_psets[key].get(m.prop)
    return [] if change is None else [_applied_from_change(m, change)]


def _handle_set_material(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    changes = writer.set_material([m.global_id], m.new_value)
    return [_applied_from_change(m, changes[0])]


def _handle_set_classification(
    writer: Tier2Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    # m.prop carries the classification system name; m.params["name"] the
    # optional human label (matches Tier2Writer.set_classification args).
    changes = writer.set_classification(
        [m.global_id], m.prop, m.new_value, m.params.get("name", "")
    )
    return [_applied_from_change(m, changes[0])]


# ── Tier 3 handlers ────────────────────────────────────────────────
# Entity lifecycle. These are the ops that used to require the LLM to
# author IfcOpenShell code; the calls now live in Tier3Writer, written and
# tested once.


def _handle_create_entity(
    writer: Tier3Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    params = m.params or {}
    change = writer.create_entity(
        m.ifc_type,
        m.entity_name,
        long_name=params.get("long_name", ""),
        description=params.get("description", ""),
        parent_global_id=params.get("parent_global_id", ""),
        parent_relation=params.get("parent_relation", "none"),
        member_global_ids=params.get("member_global_ids", ()),
    )
    # The GlobalId only exists now — hand it to the caller via `result`,
    # which is what reaches the git diff and the DB sync.
    return [
        AppliedMutation(
            mutation=m,
            actual_old_value=change.old_value,
            stale=False,
            result={
                "global_id": change.global_id,
                "ifc_type": change.ifc_type,
                "name": change.entity_name,
                "created": True,
            },
        )
    ]


def _handle_delete_entity(
    writer: Tier3Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    change = writer.delete_entity(m.global_id)
    return [
        AppliedMutation(
            mutation=m,
            actual_old_value=change.old_value,
            stale=False,
            result={
                "global_id": change.global_id,
                "ifc_type": change.ifc_type,
                "name": change.entity_name,
                "deleted": True,
            },
        )
    ]


def _handle_assign_relationship(
    writer: Tier3Writer, m: Mutation, ctx: _RunContext
) -> list[AppliedMutation]:
    destination_global_id = (m.params or {}).get("destination_global_id") or ""
    change = writer.assign_container(m.global_id, destination_global_id)
    return [
        AppliedMutation(
            mutation=m,
            actual_old_value=change.old_value,
            stale=False,
            result={
                "global_id": change.global_id,
                "destination_global_id": destination_global_id,
                "moved": True,
            },
        )
    ]


_Handler = Callable[[Tier3Writer, Mutation, _RunContext], list[AppliedMutation]]

# RUN_CODE is deliberately absent: generated code runs in a subprocess that
# owns the file, so it cannot share the in-memory writer these handlers use.
# _run dispatches it separately (see _apply_code).
_HANDLERS: dict[MutationOp, _Handler] = {
    MutationOp.SET_PROPERTY: _handle_set_property,
    MutationOp.ADD_PROPERTY: _handle_add_property,
    MutationOp.REMOVE_PROPERTY: _handle_remove_property,
    MutationOp.SET_ATTRIBUTE: _handle_set_attribute,
    MutationOp.ADD_PSET: _handle_add_pset,
    MutationOp.REMOVE_PSET: _handle_remove_pset,
    MutationOp.SET_MATERIAL: _handle_set_material,
    MutationOp.SET_CLASSIFICATION: _handle_set_classification,
    MutationOp.CREATE_ENTITY: _handle_create_entity,
    MutationOp.DELETE_ENTITY: _handle_delete_entity,
    MutationOp.ASSIGN_RELATIONSHIP: _handle_assign_relationship,
}


class JournalExecutor:
    """Replays a MutationJournal against one IFC file.

    Usage:
        executor = JournalExecutor(ifc_file.file.path)
        applied = executor.apply(journal)          # bakes + atomic swap
        preview = executor.preview(journal)        # applies to a copy, no swap
    """

    def __init__(
        self,
        ifc_path: str | Path,
        code_timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.ifc_path = Path(ifc_path)
        if not self.ifc_path.exists():
            raise JournalExecutionError(f"IFC file not found: {self.ifc_path}")
        # Wall-clock budget for a RUN_CODE mutation's sandbox subprocess.
        self.code_timeout = code_timeout

    def apply(
        self,
        journal: MutationJournal,
        *,
        stale_policy: Literal["abort", "warn"] = "abort",
    ) -> AppliedJournal:
        """Apply the journal and atomically replace the original file."""
        return self._run(journal, persist=True, stale_policy=stale_policy)

    def preview(
        self,
        journal: MutationJournal,
        *,
        stale_policy: Literal["abort", "warn"] = "abort",
    ) -> AppliedJournal:
        """Apply the journal to a throwaway copy — the original is untouched."""
        return self._run(journal, persist=False, stale_policy=stale_policy)

    # ── Internals ──────────────────────────────────────────

    def _run(
        self,
        journal: MutationJournal,
        *,
        persist: bool,
        stale_policy: Literal["abort", "warn"],
    ) -> AppliedJournal:
        if not journal.mutations:
            raise JournalExecutionError("Journal contains no mutations.")

        code_mutations = [m for m in journal.mutations if m.op == MutationOp.RUN_CODE]
        if code_mutations and len(journal.mutations) != 1:
            # Generated code runs in a subprocess that opens and writes the
            # file itself, while typed ops go through an in-memory writer.
            # Both in one journal means two writers racing the same path —
            # last save wins, silently discarding the other's work.
            raise JournalExecutionError(
                "A journal containing RUN_CODE must hold exactly one mutation; "
                f"got {len(journal.mutations)}.",
                mutation_id=code_mutations[0].id,
            )

        self._check_fingerprint(journal, stale_policy)

        temp_path = self.ifc_path.with_name(
            f".{self.ifc_path.stem}.journal-{uuid.uuid4().hex[:8]}{self.ifc_path.suffix}"
        )
        shutil.copy2(self.ifc_path, temp_path)

        try:
            if code_mutations:
                applied = self._apply_code(code_mutations[0], temp_path)
            else:
                applied = self._apply_handlers(journal, temp_path)

            if persist:
                os.replace(temp_path, self.ifc_path)
                logger.info(
                    "Journal %s applied: %d mutation(s) baked into %s",
                    journal.journal_id,
                    len(applied),
                    self.ifc_path.name,
                )
        finally:
            temp_path.unlink(missing_ok=True)

        result = AppliedJournal(journal=journal, applied=tuple(applied))
        if result.stale_count:
            logger.warning(
                "Journal %s: %d mutation(s) had drifted old values (DB vs file).",
                journal.journal_id,
                result.stale_count,
            )
        return result

    def _apply_handlers(self, journal: MutationJournal, temp_path: Path) -> list[AppliedMutation]:
        """Apply typed ops through the in-memory writer, then save the copy."""
        writer = Tier3Writer(temp_path)
        ctx = _RunContext()
        applied: list[AppliedMutation] = []
        for mutation in journal.mutations:
            handler = _HANDLERS.get(mutation.op)
            if handler is None:
                raise JournalExecutionError(
                    f"No handler registered for op {mutation.op.value} (mutation {mutation.id}).",
                    mutation_id=mutation.id,
                )
            applied.extend(handler(writer, mutation, ctx))
        writer.save()
        return applied

    def _apply_code(self, mutation: Mutation, temp_path: Path) -> list[AppliedMutation]:
        """Run generated code in the sandbox subprocess.

        No writer is constructed here — the child process opens the temp
        copy and writes it back itself, so there is exactly one writer for
        the file. Each change the code self-reports becomes one
        AppliedMutation sharing this single RUN_CODE mutation.
        """
        code = (mutation.params or {}).get("code") or ""
        if not code:
            raise JournalExecutionError(
                "RUN_CODE mutation carries no code.", mutation_id=mutation.id
            )

        try:
            result = run_code_subprocess(temp_path, code, timeout=self.code_timeout)
        except CodeSandboxError as e:
            raise JournalExecutionError(str(e), mutation_id=mutation.id) from e

        logger.info("RUN_CODE %s: %s", mutation.id, result.get("summary", "(no summary)"))
        return [
            AppliedMutation(
                mutation=mutation,
                actual_old_value=change.get("old_value"),
                stale=False,
                result=change,
            )
            for change in result.get("changes", [])
        ]

    def _check_fingerprint(
        self,
        journal: MutationJournal,
        stale_policy: Literal["abort", "warn"],
    ) -> None:
        """Compare the on-disk fingerprint with the journal's pin."""
        if not journal.base_fingerprint:
            return
        current = compute_fingerprint(self.ifc_path)
        if current == journal.base_fingerprint:
            return
        message = (
            "The IFC file changed after this proposal was created "
            f"(fingerprint {current[:12]}… != {journal.base_fingerprint[:12]}…). "
            "Please re-propose the modification."
        )
        if stale_policy == "abort":
            raise JournalStaleError(message)
        logger.warning("Stale journal applied with warn policy: %s", message)
