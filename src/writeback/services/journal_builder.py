# writeback/services/journal_builder.py
"""
Builds MutationJournals from validated pipeline output.

The builder is the single point where tier artifacts normalize into the
journal IR: it expands a validated intent over the resolved entities,
snapshots old values from the DB index (IFCEntity.properties), stamps the
registry value type, and pins the on-disk file fingerprint so the executor
can detect staleness at apply time.

PoC scope: ``build_t1`` (SET_PROPERTY / ADD_PROPERTY / REMOVE_PROPERTY /
SET_ATTRIBUTE). ``build_t2`` / ``build_t3`` land in later migration phases.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from django.conf import settings
from django.utils import timezone

from ifc_processor.models import IFCEntity
from ifc_processor.services.ifc_standard_psets import lookup_property
from ifc_processor.services.journal import (
    Mutation,
    MutationJournal,
    MutationOp,
    compute_fingerprint,
    new_journal_id,
    new_mutation_id,
)

logger = logging.getLogger(__name__)

_T1_OPS = frozenset(
    {
        MutationOp.SET_PROPERTY,
        MutationOp.ADD_PROPERTY,
        MutationOp.REMOVE_PROPERTY,
        MutationOp.SET_ATTRIBUTE,
    }
)

# Sentinel pset labels carried in Mutation.pset for ops whose "pset" is not a
# real IFC property set. They ride through applied_to_entity_changes' default
# branch to reproduce the legacy Tier2Writer EntityChange shape verbatim.
_MATERIAL_PSET = "(material)"
_CLASSIFICATION_PSET = "(classification)"
_ENTITY_PSET = "(entity)"

# IFC attributes mirrored to dedicated IFCEntity columns; the rest live
# in the properties JSON (same split _sync_entity_properties maintains).
_ATTRIBUTE_COLUMNS = {
    "Name": "name",
    "Description": "ifc_description",
    "Tag": "tag",
}


class JournalBuildError(Exception):
    """The pipeline output could not be normalized into a journal."""


class JournalBuilder:
    """Normalizes validated tier output into a MutationJournal."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.user = user

    def build_t1(
        self,
        intent: dict,
        entities: Sequence[IFCEntity],
        ifc_file,
    ) -> MutationJournal:
        """Expand a validated Tier 1 intent into one mutation per entity.

        Args:
            intent:   The assembled + validated T1 intent dict.
            entities: Entities the filter resolved (from Tier1Validator).
            ifc_file: The IFCFile the journal targets.

        Raises:
            JournalBuildError: unknown operation, no entities, or cap exceeded.
        """
        if not entities:
            raise JournalBuildError("Cannot build a journal with no target entities.")

        try:
            op = MutationOp(intent.get("operation", ""))
        except ValueError as e:
            raise JournalBuildError(f"Unknown Tier 1 operation: {intent.get('operation')!r}") from e
        if op not in _T1_OPS:
            raise JournalBuildError(f"Operation {op.value} is not a Tier 1 operation.")

        cap = getattr(settings, "WRITEBACK_JOURNAL_MAX_MUTATIONS", 2000)
        if len(entities) > cap:
            raise JournalBuildError(
                f"This change would touch {len(entities)} entities — more than the "
                f"{cap}-mutation safety cap. Please narrow the target filter."
            )

        pset = intent.get("pset") or ""
        prop = intent.get("property") or ""
        attribute = intent.get("attribute") or ""
        new_value = intent.get("new_value")
        value_type = self._registry_type(pset, prop)

        mutations = tuple(
            Mutation(
                id=new_mutation_id(),
                op=op,
                global_id=entity.global_id,
                entity_name=entity.name or "",
                ifc_type=entity.ifc_type,
                pset=pset,
                prop=prop,
                attribute=attribute,
                old_value=self._snapshot_old_value(op, entity, pset, prop, attribute),
                new_value=None if op == MutationOp.REMOVE_PROPERTY else new_value,
                value_type=value_type,
            )
            for entity in entities
        )

        journal = MutationJournal(
            journal_id=new_journal_id(),
            ifc_file_id=str(ifc_file.id),
            source_tier=1,
            base_fingerprint=compute_fingerprint(ifc_file.file.path),
            captured_at=timezone.now().isoformat(),
            mutations=mutations,
        )
        logger.info(
            "Built T1 journal %s: %s × %d entities",
            journal.journal_id,
            op.value,
            len(mutations),
        )
        return journal

    def build_t2(self, plan: dict, validation, ifc_file) -> MutationJournal:
        """Expand a validated Tier 2 plan into per-entity/per-property mutations.

        Reuses each step's already-resolved (and, for REMOVE_PSET, narrowed)
        ``validation.steps[i].entities`` rather than re-resolving filters.
        Granularity is one mutation per (entity, property) for pset ops and
        per entity for material/classification/T1 ops — a 1:1 mapping with the
        Tier2Writer's per-property EntityChange output.

        Args:
            plan:       The assembled + validated Tier 2 plan dict.
            validation: The PlanValidation from Tier2Validator.validate_plan.
            ifc_file:   The IFCFile the journal targets.

        Raises:
            JournalBuildError: unknown/unsupported op, empty plan, no
                applicable changes, or the mutation cap exceeded.
        """
        steps = plan.get("plan") or []
        if not steps:
            raise JournalBuildError("Cannot build a journal from an empty plan.")

        mutations: list[Mutation] = []
        for step, step_validation in zip(steps, validation.steps):
            params = step.get("params") or {}
            entities = list(getattr(step_validation, "entities", None) or [])
            mutations.extend(self._step_mutations(step.get("operation", ""), params, entities))

        if not mutations:
            raise JournalBuildError(
                "The plan produced no applicable changes (all target properties "
                "may already be set as requested)."
            )

        cap = getattr(settings, "WRITEBACK_JOURNAL_MAX_MUTATIONS", 2000)
        if len(mutations) > cap:
            raise JournalBuildError(
                f"This change would touch {len(mutations)} properties — more than "
                f"the {cap}-mutation safety cap. Please narrow the target filter."
            )

        journal = MutationJournal(
            journal_id=new_journal_id(),
            ifc_file_id=str(ifc_file.id),
            source_tier=2,
            base_fingerprint=compute_fingerprint(ifc_file.file.path),
            captured_at=timezone.now().isoformat(),
            mutations=tuple(mutations),
        )
        logger.info(
            "Built T2 journal %s: %d step(s) → %d mutation(s)",
            journal.journal_id,
            len(steps),
            len(mutations),
        )
        return journal

    def build_t3(self, ops: Sequence[dict], ifc_file) -> MutationJournal:
        """Turn validated Tier 3 typed ops into entity-lifecycle mutations.

        Every GlobalId an op references is re-checked against the DB for this
        file — a second, authoritative grounding pass independent of whatever
        the planner claimed, so a hallucinated id cannot reach execution.

        Raises:
            JournalBuildError: unknown op, ungrounded id, empty op list, or
                the mutation cap exceeded.
        """
        if not ops:
            raise JournalBuildError("Cannot build a Tier 3 journal with no operations.")

        known_ids = self._known_global_ids(ifc_file, ops)
        mutations: list[Mutation] = []

        for index, op in enumerate(ops):
            op_name = (op or {}).get("op", "")
            if op_name == MutationOp.CREATE_ENTITY.value:
                mutations.append(self._create_entity_mutation(op, known_ids))
            elif op_name == MutationOp.DELETE_ENTITY.value:
                mutations.append(self._delete_entity_mutation(op, ifc_file, known_ids))
            elif op_name == MutationOp.ASSIGN_RELATIONSHIP.value:
                mutations.append(self._assign_relationship_mutation(op, ifc_file, known_ids))
            else:
                raise JournalBuildError(
                    f"Operation #{index + 1} has unsupported Tier 3 op: {op_name!r}"
                )

        cap = getattr(settings, "WRITEBACK_JOURNAL_MAX_MUTATIONS", 2000)
        if len(mutations) > cap:
            raise JournalBuildError(
                f"This change would touch {len(mutations)} entities — more than "
                f"the {cap}-mutation safety cap. Please narrow the request."
            )

        journal = self._journal(mutations, ifc_file, source_tier=3)
        logger.info("Built T3 journal %s: %d typed op(s)", journal.journal_id, len(mutations))
        return journal

    def build_t3_code(self, code: str, ifc_file, *, explanation: str = "") -> MutationJournal:
        """Wrap generated code as a single RUN_CODE mutation.

        Exactly one mutation, always: the sandbox subprocess opens and writes
        the file itself, so it can never share a journal with typed ops that
        go through the in-memory writer.
        """
        if not code or not code.strip():
            raise JournalBuildError("Cannot build a RUN_CODE journal with no code.")

        mutation = Mutation(
            id=new_mutation_id(),
            op=MutationOp.RUN_CODE,
            global_id="",
            params={"code": code, "explanation": explanation},
        )
        journal = self._journal([mutation], ifc_file, source_tier=3)
        logger.info("Built T3 RUN_CODE journal %s (%d chars)", journal.journal_id, len(code))
        return journal

    # ── Tier 3 internals ───────────────────────────────────

    def _create_entity_mutation(self, op: dict, known_ids: set[str]) -> Mutation:
        ifc_class = (op.get("ifc_class") or "").strip()
        name = (op.get("name") or "").strip()
        if not ifc_class or not name:
            raise JournalBuildError("CREATE_ENTITY requires both 'ifc_class' and 'name'.")

        parent_global_id = (op.get("parent_global_id") or "").strip()
        members = [str(g).strip() for g in (op.get("member_global_ids") or []) if str(g).strip()]
        for referenced in [parent_global_id, *members]:
            if referenced and referenced not in known_ids:
                raise JournalBuildError(
                    f"CREATE_ENTITY references entity {referenced!r}, which does not "
                    f"exist in this IFC file."
                )

        return Mutation(
            id=new_mutation_id(),
            op=MutationOp.CREATE_ENTITY,
            # No GlobalId exists until the writer mints one at execution.
            # affected_global_ids filters empty ids, so a CREATE correctly
            # reports touching no existing entity.
            global_id="",
            entity_name=name,
            ifc_type=ifc_class,
            pset=_ENTITY_PSET,
            prop="CREATE",
            old_value=None,
            new_value=name,
            params={
                "long_name": (op.get("long_name") or "").strip(),
                "description": (op.get("description") or "").strip(),
                "parent_global_id": parent_global_id,
                "parent_relation": (op.get("parent_relation") or "none").strip(),
                "member_global_ids": members,
            },
        )

    def _delete_entity_mutation(self, op: dict, ifc_file, known_ids: set[str]) -> Mutation:
        global_id = (op.get("global_id") or "").strip()
        if not global_id:
            raise JournalBuildError("DELETE_ENTITY requires a 'global_id'.")
        if global_id not in known_ids:
            raise JournalBuildError(
                f"DELETE_ENTITY targets entity {global_id!r}, which does not exist "
                f"in this IFC file."
            )

        entity = IFCEntity.objects.filter(ifc_file=ifc_file, global_id=global_id).first()
        return Mutation(
            id=new_mutation_id(),
            op=MutationOp.DELETE_ENTITY,
            global_id=global_id,
            entity_name=(entity.name if entity else "") or "",
            ifc_type=(entity.ifc_type if entity else "") or "",
            pset=_ENTITY_PSET,
            prop="DELETE",
            old_value=(entity.name if entity else "") or global_id,
            new_value=None,
        )

    def _assign_relationship_mutation(self, op: dict, ifc_file, known_ids: set[str]) -> Mutation:
        """Move one entity into a different spatial container."""
        global_id = (op.get("global_id") or "").strip()
        destination_global_id = (op.get("destination_global_id") or "").strip()
        if not global_id or not destination_global_id:
            raise JournalBuildError(
                "ASSIGN_RELATIONSHIP requires both 'global_id' and 'destination_global_id'."
            )
        for referenced in (global_id, destination_global_id):
            if referenced not in known_ids:
                raise JournalBuildError(
                    f"ASSIGN_RELATIONSHIP references entity {referenced!r}, which does "
                    f"not exist in this IFC file."
                )

        entity = IFCEntity.objects.filter(ifc_file=ifc_file, global_id=global_id).first()
        destination = IFCEntity.objects.filter(
            ifc_file=ifc_file, global_id=destination_global_id
        ).first()

        # Snapshot the current container so the diff reads "Level 1 → Level 2"
        # rather than just naming the destination.
        current_container = ""
        if entity is not None and entity.spatial_container_id:
            current_container = getattr(entity.spatial_container.entity, "name", "") or ""

        return Mutation(
            id=new_mutation_id(),
            op=MutationOp.ASSIGN_RELATIONSHIP,
            global_id=global_id,
            entity_name=(entity.name if entity else "") or "",
            ifc_type=(entity.ifc_type if entity else "") or "",
            pset=_ENTITY_PSET,
            prop="CONTAINER",
            old_value=current_container or None,
            new_value=(destination.name if destination else "") or destination_global_id,
            params={
                "destination_global_id": destination_global_id,
                "relation": (op.get("relation") or "container").strip(),
            },
        )

    @staticmethod
    def _known_global_ids(ifc_file, ops: Sequence[dict]) -> set[str]:
        """Resolve every GlobalId the ops reference against the DB in one query."""
        referenced: set[str] = set()
        for op in ops or ():
            if not isinstance(op, dict):
                continue
            for key in ("global_id", "parent_global_id", "destination_global_id"):
                value = (op.get(key) or "").strip()
                if value:
                    referenced.add(value)
            for member in op.get("member_global_ids") or []:
                value = str(member).strip()
                if value:
                    referenced.add(value)

        if not referenced:
            return set()
        return set(
            IFCEntity.objects.filter(ifc_file=ifc_file, global_id__in=referenced).values_list(
                "global_id", flat=True
            )
        )

    def _journal(
        self, mutations: Sequence[Mutation], ifc_file, *, source_tier: int
    ) -> MutationJournal:
        """Assemble a journal with the fingerprint pinned to the file on disk."""
        return MutationJournal(
            journal_id=new_journal_id(),
            ifc_file_id=str(ifc_file.id),
            source_tier=source_tier,
            base_fingerprint=compute_fingerprint(ifc_file.file.path),
            captured_at=timezone.now().isoformat(),
            mutations=tuple(mutations),
        )

    def _step_mutations(
        self,
        operation: str,
        params: dict,
        entities: Sequence[IFCEntity],
    ) -> list[Mutation]:
        """Expand one plan step into per-entity/per-property mutations."""
        try:
            op = MutationOp(operation)
        except ValueError as e:
            raise JournalBuildError(f"Unsupported Tier 2 operation: {operation!r}") from e

        if op in _T1_OPS:
            pset = params.get("pset") or ""
            prop = params.get("property") or ""
            attribute = params.get("attribute") or ""
            value_type = self._registry_type(pset, prop)
            return [
                self._make_t1_mutation(
                    op, entity, pset, prop, attribute, params.get("new_value"), value_type
                )
                for entity in entities
            ]

        if op == MutationOp.ADD_PSET:
            return self._add_pset_mutations(params, entities)

        if op == MutationOp.REMOVE_PSET:
            return self._remove_pset_mutations(params, entities)

        if op == MutationOp.SET_MATERIAL:
            material_name = params.get("material_name") or ""
            return [
                self._entity_mutation(
                    op, entity, pset=_MATERIAL_PSET, prop="Material", new_value=material_name
                )
                for entity in entities
            ]

        if op == MutationOp.SET_CLASSIFICATION:
            system_name = params.get("system_name") or ""
            reference = params.get("reference") or ""
            name = params.get("name") or ""
            return [
                self._entity_mutation(
                    op,
                    entity,
                    pset=_CLASSIFICATION_PSET,
                    prop=system_name,
                    new_value=reference,
                    params={"name": name},
                )
                for entity in entities
            ]

        raise JournalBuildError(f"Operation {op.value} is not supported by build_t2.")

    def _add_pset_mutations(self, params: dict, entities: Sequence[IFCEntity]) -> list[Mutation]:
        pset = params.get("pset_name") or ""
        properties = params.get("properties") or {}
        mutations: list[Mutation] = []
        for entity in entities:
            existing = entity.properties or {}
            for prop, value in properties.items():
                # Skip properties already present — mirrors Tier2Writer.add_pset,
                # keeps the mutation count (and cap pressure) honest.
                if f"{pset}.{prop}" in existing:
                    continue
                mutations.append(
                    self._entity_mutation(
                        MutationOp.ADD_PSET,
                        entity,
                        pset=pset,
                        prop=prop,
                        new_value=value,
                        value_type=self._registry_type(pset, prop),
                    )
                )
        return mutations

    def _remove_pset_mutations(self, params: dict, entities: Sequence[IFCEntity]) -> list[Mutation]:
        pset = params.get("pset_name") or ""
        prefix = f"{pset}."
        mutations: list[Mutation] = []
        for entity in entities:
            for key, value in (entity.properties or {}).items():
                if not key.startswith(prefix):
                    continue
                mutations.append(
                    self._entity_mutation(
                        MutationOp.REMOVE_PSET,
                        entity,
                        pset=pset,
                        prop=key[len(prefix) :],
                        old_value=value,
                    )
                )
        return mutations

    # ── Internals ──────────────────────────────────────────

    def _make_t1_mutation(
        self,
        op: MutationOp,
        entity: IFCEntity,
        pset: str,
        prop: str,
        attribute: str,
        new_value: object,
        value_type: str,
    ) -> Mutation:
        """One Tier 1 mutation (also used for T1 ops embedded in a T2 plan)."""
        return Mutation(
            id=new_mutation_id(),
            op=op,
            global_id=entity.global_id,
            entity_name=entity.name or "",
            ifc_type=entity.ifc_type,
            pset=pset,
            prop=prop,
            attribute=attribute,
            old_value=self._snapshot_old_value(op, entity, pset, prop, attribute),
            new_value=None if op == MutationOp.REMOVE_PROPERTY else new_value,
            value_type=value_type,
        )

    @staticmethod
    def _entity_mutation(
        op: MutationOp,
        entity: IFCEntity,
        *,
        pset: str = "",
        prop: str = "",
        old_value: object = None,
        new_value: object = None,
        value_type: str = "",
        params: dict | None = None,
    ) -> Mutation:
        """A per-entity mutation with explicit fields (Tier 2 pset ops)."""
        return Mutation(
            id=new_mutation_id(),
            op=op,
            global_id=entity.global_id,
            entity_name=entity.name or "",
            ifc_type=entity.ifc_type,
            pset=pset,
            prop=prop,
            old_value=old_value,
            new_value=new_value,
            value_type=value_type,
            params=params or {},
        )

    @staticmethod
    def _registry_type(pset: str, prop: str) -> str:
        """Registry type tag ("bool", "real", "enum", …) or "" when unknown."""
        if not pset or not prop:
            return ""
        entry = lookup_property(pset, prop)
        if entry is None:
            return ""
        type_str, _enum_values = entry
        return type_str

    @staticmethod
    def _snapshot_old_value(
        op: MutationOp,
        entity: IFCEntity,
        pset: str,
        prop: str,
        attribute: str,
    ) -> object:
        """Read the current value from the DB index (None = not set)."""
        if op == MutationOp.ADD_PROPERTY:
            return None
        if op == MutationOp.SET_ATTRIBUTE:
            column = _ATTRIBUTE_COLUMNS.get(attribute)
            if column is not None:
                return getattr(entity, column) or None
            return (entity.properties or {}).get(attribute)
        return (entity.properties or {}).get(f"{pset}.{prop}")
