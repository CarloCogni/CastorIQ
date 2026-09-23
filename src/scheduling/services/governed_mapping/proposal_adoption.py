# scheduling/services/governed_mapping/proposal_adoption.py
"""Adopt existing suggestions as governed proposals (DF-D2)."""

from __future__ import annotations

import logging

from django.contrib.auth.models import AbstractUser
from django.db import transaction

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalDimensionValue,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
    Task,
    WBSNode,
)
from scheduling.services.governed_mapping.adapters import ADAPTER_REGISTRY
from scheduling.services.governed_mapping.adapters.base import MappingSourceAdapter
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.contracts import (
    MappingProposalDTO,
    ProposalAdoptionResult,
)
from scheduling.services.governed_mapping.exceptions import MappingValidationError

logger = logging.getLogger(__name__)


class ProposalAdoptionService:
    """Normalize suggestions and create proposed assignments idempotently."""

    def __init__(self, project, *, actor: AbstractUser | None = None) -> None:
        self.project = project
        self.actor = actor

    def adopt(
        self,
        *,
        source: str,
        dimension_key: str,
        mapping_set: AnalyticalMappingSet,
        dry_run: bool = True,
        limit: int | None = None,
    ) -> ProposalAdoptionResult:
        """Inspect or write proposals from a registered source adapter."""
        result = ProposalAdoptionResult(
            source=source,
            dimension_key=dimension_key,
            dry_run=dry_run,
        )
        adapter_cls = ADAPTER_REGISTRY.get(source)
        if adapter_cls is None:
            result.errors.append(f"Unknown source adapter: {source}")
            return result

        dimension = AnalyticalDimension.objects.filter(
            project=self.project,
            dimension_key=dimension_key,
            is_selected_for_analysis=True,
        ).first()
        if dimension is None:
            dimension = (
                AnalyticalDimension.objects.filter(
                    project=self.project, dimension_key=dimension_key
                )
                .order_by("-revision_number")
                .first()
            )
        if dimension is None:
            result.errors.append(f"No dimension for key: {dimension_key}")
            return result

        adapter: MappingSourceAdapter = adapter_cls(self.project, dimension_key=dimension_key)
        proposals = adapter.collect_proposals(limit=limit)
        result.suggestions_inspected = len(proposals)

        value_cache = self._value_cache(dimension)
        open_proposal_keys = self._open_proposal_keys(mapping_set) if dry_run else set()

        for proposal in proposals:
            if not self._validate_proposal(proposal, result):
                continue
            result.valid_proposals += 1
            value = self._resolve_value(dimension, proposal, value_cache, result)
            if value is None:
                result.unresolved_values += 1
                continue
            task, wbs_node, activity_id, gid = self._resolve_target(
                proposal, result, dry_run=dry_run
            )
            if (
                proposal.target_type == "task"
                and task is None
                and not (dry_run and proposal.target_id)
            ):
                result.unresolved_targets += 1
                continue
            if proposal.target_type == "wbs_node" and wbs_node is None:
                result.unresolved_targets += 1
                continue

            if dry_run:
                if self._is_duplicate_key(
                    open_proposal_keys,
                    mapping_set,
                    value,
                    proposal,
                    task,
                    wbs_node,
                    gid,
                    activity_id,
                ):
                    result.duplicates_skipped += 1
                else:
                    result.proposals_created += 1
                continue

            created, dup = self._write_proposal(
                mapping_set=mapping_set,
                dimension=dimension,
                value=value,
                proposal=proposal,
                task=task,
                wbs_node=wbs_node,
                activity_id=activity_id,
                entity_global_id=gid,
            )
            if dup:
                result.duplicates_skipped += 1
            elif created:
                result.proposals_created += 1

        if dry_run:
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.ADOPTION_DRY_RUN,
                project=self.project,
                dimension=dimension,
                mapping_set=None,
                actor=self.actor,
                evidence_summary={
                    **result.to_dict(),
                    "mapping_set_id": str(mapping_set.pk) if mapping_set.pk else None,
                },
            )
        elif result.proposals_created:
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.PROPOSAL_ADOPTED,
                project=self.project,
                dimension=dimension,
                mapping_set=mapping_set,
                actor=self.actor,
                evidence_summary=result.to_dict(),
            )
        return result

    def _validate_proposal(
        self, proposal: MappingProposalDTO, result: ProposalAdoptionResult
    ) -> bool:
        if not proposal.target_id and not proposal.target_identity:
            result.warnings.append("Proposal missing target identity.")
            return False
        return True

    def _value_cache(self, dimension: AnalyticalDimension) -> dict[str, AnalyticalDimensionValue]:
        return {
            (v.code or v.name).lower(): v
            for v in AnalyticalDimensionValue.objects.filter(dimension=dimension, status="active")
        }

    def _resolve_value(
        self,
        dimension: AnalyticalDimension,
        proposal: MappingProposalDTO,
        cache: dict[str, AnalyticalDimensionValue],
        result: ProposalAdoptionResult,
    ) -> AnalyticalDimensionValue | None:
        key = (proposal.proposed_value_code or proposal.proposed_value).lower()
        if key in cache:
            return cache[key]
        result.warnings.append(f"Unresolved value: {proposal.proposed_value}")
        return None

    def _resolve_target(
        self,
        proposal: MappingProposalDTO,
        result: ProposalAdoptionResult,
        *,
        dry_run: bool = False,
    ) -> tuple[Task | None, WBSNode | None, str | None, str]:
        identity = proposal.target_identity
        task = None
        wbs_node = None
        activity_id = None
        gid = ""
        if proposal.target_type == "task":
            tid = proposal.target_id
            if identity and identity.task_id:
                tid = identity.task_id
            if dry_run and tid:
                task = Task(pk=tid)
            else:
                task = Task.objects.filter(pk=tid, project=self.project).first()
        elif proposal.target_type == "wbs_node":
            nid = proposal.target_id
            if identity and identity.wbs_node_id:
                nid = identity.wbs_node_id
            wbs_node = WBSNode.objects.filter(pk=nid, wbs_version__project=self.project).first()
        elif proposal.target_type == "ifc_entity":
            gid = proposal.target_id
        elif proposal.target_type == "schedule_activity":
            activity_id = proposal.target_id
            if identity and identity.schedule_activity_id:
                activity_id = identity.schedule_activity_id
        return task, wbs_node, activity_id, gid

    def _open_proposal_keys(self, mapping_set: AnalyticalMappingSet) -> set[tuple]:
        if not mapping_set.pk:
            return set()
        keys: set[tuple] = set()
        rows = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status__in={
                AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
                AnalyticalMappingAssignment.GovernanceStatus.UNDER_REVIEW,
            },
        ).values_list(
            "target_type",
            "task_id",
            "wbs_node_id",
            "schedule_activity_id",
            "entity_global_id",
            "dimension_value_id",
        )
        for target_type, task_id, wbs_id, activity_id, gid, value_id in rows:
            keys.add(
                (
                    target_type,
                    str(task_id) if task_id else "",
                    str(wbs_id) if wbs_id else "",
                    str(activity_id) if activity_id else "",
                    gid or "",
                    str(value_id),
                )
            )
        return keys

    def _proposal_key(
        self,
        proposal: MappingProposalDTO,
        value: AnalyticalDimensionValue,
        task,
        wbs_node,
        gid: str,
        activity_id: str | None,
    ) -> tuple:
        return (
            proposal.target_type,
            str(task.pk)
            if task
            else (proposal.target_id if proposal.target_type == "task" else ""),
            str(wbs_node.pk) if wbs_node else "",
            activity_id or "",
            gid or "",
            str(value.pk),
        )

    def _is_duplicate_key(
        self,
        open_keys: set[tuple],
        mapping_set: AnalyticalMappingSet,
        value: AnalyticalDimensionValue,
        proposal: MappingProposalDTO,
        task,
        wbs_node,
        gid: str,
        activity_id: str | None,
    ) -> bool:
        if not mapping_set.pk:
            return False
        key = self._proposal_key(proposal, value, task, wbs_node, gid, activity_id)
        return key in open_keys

    def _would_duplicate(
        self,
        mapping_set: AnalyticalMappingSet,
        value: AnalyticalDimensionValue,
        proposal: MappingProposalDTO,
        task,
        wbs_node,
        gid: str,
        activity_id: str | None,
    ) -> bool:
        try:
            return (
                AnalyticalMappingAssignmentService.find_open_proposal(
                    mapping_set=mapping_set,
                    dimension_value=value,
                    target_type=proposal.target_type,
                    task=task,
                    wbs_node=wbs_node,
                    entity_global_id=gid,
                    schedule_activity_id=activity_id,
                )
                is not None
            )
        except MappingValidationError:
            return False

    @transaction.atomic
    def _write_proposal(
        self,
        *,
        mapping_set: AnalyticalMappingSet,
        dimension: AnalyticalDimension,
        value: AnalyticalDimensionValue,
        proposal: MappingProposalDTO,
        task,
        wbs_node,
        activity_id: str | None,
        entity_global_id: str,
    ) -> tuple[bool, bool]:
        from scheduling.models import ScheduleActivity

        activity = None
        if proposal.target_type == "schedule_activity" and activity_id:
            activity = ScheduleActivity.objects.filter(pk=activity_id, project=self.project).first()
        assignment, created = AnalyticalMappingAssignmentService.create_proposal_idempotent(
            mapping_set=mapping_set,
            dimension_value=value,
            target_type=proposal.target_type,
            actor=self.actor,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=activity,
            entity_global_id=entity_global_id,
            evidence=proposal.evidence,
            confidence=proposal.confidence,
            provenance={
                "source": proposal.source,
                "rule_version": proposal.rule_version,
                "caveats": list(proposal.caveats),
            },
        )
        if not created:
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.PROPOSAL_DUPLICATE_SKIPPED,
                project=self.project,
                dimension=dimension,
                mapping_set=mapping_set,
                assignment=assignment,
                actor=self.actor,
                target_type=proposal.target_type,
                target_id=proposal.target_id,
            )
        return created, not created
