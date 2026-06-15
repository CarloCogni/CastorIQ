# scheduling/services/governed_mapping/assignment.py
"""AnalyticalMappingAssignment service (DF-D1/DF-D2)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalDimensionValue,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
    ScheduleActivity,
    Task,
    WBSNode,
)
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.contracts import MappingProposalDTO
from scheduling.services.governed_mapping.exceptions import (
    MappingImmutabilityError,
    MappingValidationError,
)
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService

logger = logging.getLogger(__name__)

_OPEN_STATUSES = frozenset(
    {
        AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
        AnalyticalMappingAssignment.GovernanceStatus.UNDER_REVIEW,
    }
)
_EFFECTIVE_STATUSES = frozenset({AnalyticalMappingAssignment.GovernanceStatus.APPROVED})


class AnalyticalMappingAssignmentService:
    """Create, approve, and reject governed mapping assignments."""

    @staticmethod
    def _validate_target_project(
        project_id,
        *,
        task=None,
        wbs_node=None,
        schedule_activity=None,
    ) -> None:
        if task is not None and task.project_id != project_id:
            raise MappingValidationError("Task must belong to the same project.")
        if wbs_node is not None and wbs_node.wbs_version.project_id != project_id:
            raise MappingValidationError("WBS node must belong to the same project.")
        if schedule_activity is not None and schedule_activity.project_id != project_id:
            raise MappingValidationError("Schedule activity must belong to the same project.")

    @staticmethod
    def _validate_value_compatibility(
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
    ) -> None:
        if dimension_value.dimension_id != mapping_set.dimension_id:
            raise MappingValidationError("Dimension value must belong to mapping set dimension.")

    @classmethod
    def find_open_proposal(
        cls,
        *,
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
        target_type: str,
        task=None,
        wbs_node=None,
        entity_global_id: str = "",
        schedule_activity=None,
        schedule_activity_id: str | None = None,
    ) -> AnalyticalMappingAssignment | None:
        """Return equivalent open proposal if present."""
        if not mapping_set.pk:
            return None
        qs = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            governance_status__in=_OPEN_STATUSES,
        )
        if target_type == AnalyticalMappingAssignment.TargetType.TASK:
            qs = qs.filter(task=task)
        elif target_type == AnalyticalMappingAssignment.TargetType.WBS_NODE:
            qs = qs.filter(wbs_node=wbs_node)
        elif target_type == AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY:
            sa_id = schedule_activity.pk if schedule_activity else schedule_activity_id
            qs = qs.filter(schedule_activity_id=sa_id)
        else:
            qs = qs.filter(entity_global_id=entity_global_id)
        return qs.first()

    @classmethod
    def _duplicate_exists(
        cls,
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
        *,
        target_type: str,
        task=None,
        wbs_node=None,
        entity_global_id: str = "",
        schedule_activity=None,
    ) -> bool:
        if not mapping_set.pk:
            return False
        qs = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            governance_status__in=_EFFECTIVE_STATUSES | _OPEN_STATUSES,
        )
        if target_type == AnalyticalMappingAssignment.TargetType.TASK:
            qs = qs.filter(task=task)
        elif target_type == AnalyticalMappingAssignment.TargetType.WBS_NODE:
            qs = qs.filter(wbs_node=wbs_node)
        elif target_type == AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY:
            qs = qs.filter(schedule_activity=schedule_activity)
        else:
            qs = qs.filter(entity_global_id=entity_global_id)
        return qs.exists()

    @classmethod
    def create_proposal_idempotent(
        cls,
        *,
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
        target_type: str,
        actor: AbstractUser | None = None,
        task: Task | None = None,
        wbs_node: WBSNode | None = None,
        schedule_activity: ScheduleActivity | None = None,
        entity_global_id: str = "",
        ifc_file=None,
        evidence: dict[str, Any] | None = None,
        confidence: float | None = None,
        provenance: dict[str, Any] | None = None,
        mapping_method: str | None = None,
        authority: str | None = None,
        governance_status: str | None = None,
    ) -> tuple[AnalyticalMappingAssignment, bool]:
        """Create proposal or return existing open proposal."""
        existing = cls.find_open_proposal(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            task=task,
            wbs_node=wbs_node,
            entity_global_id=entity_global_id,
            schedule_activity=schedule_activity,
        )
        if existing:
            return existing, False
        assignment = cls.create_proposal(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            actor=actor,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=schedule_activity,
            entity_global_id=entity_global_id,
            ifc_file=ifc_file,
            evidence=evidence,
            confidence=confidence,
            provenance=provenance,
        )
        updates: list[str] = []
        if mapping_method:
            assignment.mapping_method = mapping_method
            updates.append("mapping_method")
        if authority:
            assignment.authority = authority
            updates.append("authority")
        if governance_status:
            assignment.governance_status = governance_status
            updates.append("governance_status")
        if updates:
            assignment.save(update_fields=updates + ["updated_at"])
        return assignment, True

    @classmethod
    def create_proposal(
        cls,
        *,
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
        target_type: str,
        actor: AbstractUser | None = None,
        task: Task | None = None,
        wbs_node: WBSNode | None = None,
        schedule_activity: ScheduleActivity | None = None,
        entity_global_id: str = "",
        ifc_file=None,
        evidence: dict[str, Any] | None = None,
        confidence: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> AnalyticalMappingAssignment:
        """Create proposed assignment — not effective until approved."""
        if mapping_set.pk:
            AnalyticalMappingSetService.assert_mutable(mapping_set)
        cls._validate_value_compatibility(mapping_set, dimension_value)
        cls._validate_target_project(
            mapping_set.project_id,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=schedule_activity,
        )
        cls._assert_exactly_one_target(
            target_type, task, wbs_node, entity_global_id, schedule_activity
        )
        if cls._duplicate_exists(
            mapping_set,
            dimension_value,
            target_type=target_type,
            task=task,
            wbs_node=wbs_node,
            entity_global_id=entity_global_id,
            schedule_activity=schedule_activity,
        ):
            raise MappingValidationError("Duplicate mapping assignment.")
        assignment = AnalyticalMappingAssignment.objects.create(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=schedule_activity,
            entity_global_id=entity_global_id or "",
            ifc_file=ifc_file,
            mapping_method=AnalyticalMappingAssignment.MappingMethod.MANUAL,
            authority=AnalyticalMappingAssignment.MappingAuthority.SUGGESTED,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
            confidence=confidence,
            evidence=evidence or {},
            provenance=provenance or {},
            proposed_by=actor,
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.ASSIGNMENT_PROPOSED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            assignment=assignment,
            actor=actor,
            target_type=target_type,
            target_id=cls._target_id(assignment),
            resulting_state=assignment.governance_status,
        )
        return assignment

    @classmethod
    def assign_manually(
        cls,
        *,
        mapping_set: AnalyticalMappingSet,
        dimension_value: AnalyticalDimensionValue,
        target_type: str,
        actor: AbstractUser | None = None,
        task: Task | None = None,
        wbs_node: WBSNode | None = None,
        schedule_activity: ScheduleActivity | None = None,
        entity_global_id: str = "",
        ifc_file=None,
        auto_approve: bool = False,
        evidence: dict[str, Any] | None = None,
    ) -> AnalyticalMappingAssignment:
        """Manual assignment — optionally auto-approved in draft sets only."""
        proposal = cls.create_proposal(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=target_type,
            actor=actor,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=schedule_activity,
            entity_global_id=entity_global_id,
            ifc_file=ifc_file,
            evidence=evidence,
        )
        proposal.mapping_method = AnalyticalMappingAssignment.MappingMethod.MANUAL
        proposal.save(update_fields=["mapping_method", "updated_at"])
        if auto_approve:
            return cls.approve_assignment(proposal, actor=actor)
        return proposal

    @classmethod
    def create_from_proposal_dto(
        cls,
        *,
        mapping_set: AnalyticalMappingSet,
        proposal: MappingProposalDTO,
        dimension_value: AnalyticalDimensionValue,
        actor: AbstractUser | None = None,
        task: Task | None = None,
        wbs_node: WBSNode | None = None,
        schedule_activity: ScheduleActivity | None = None,
        entity_global_id: str = "",
        ifc_file=None,
    ) -> AnalyticalMappingAssignment:
        """Adapter boundary — suggestions become proposed only, never auto-approved."""
        gid = ""
        if proposal.target_type == AnalyticalMappingAssignment.TargetType.IFC_ENTITY:
            gid = entity_global_id or proposal.target_id
        assignment, _ = cls.create_proposal_idempotent(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=proposal.target_type,
            actor=actor,
            task=task,
            wbs_node=wbs_node,
            schedule_activity=schedule_activity,
            entity_global_id=gid,
            ifc_file=ifc_file,
            evidence=proposal.evidence,
            confidence=proposal.confidence,
            provenance={
                "source": proposal.source,
                "rule_version": proposal.rule_version,
                "caveats": list(proposal.caveats),
            },
        )
        return assignment

    @classmethod
    @transaction.atomic
    def approve_assignment(
        cls,
        assignment: AnalyticalMappingAssignment,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalMappingAssignment:
        """Approve proposed assignment."""
        if assignment.governance_status not in _OPEN_STATUSES:
            raise MappingValidationError("Only proposed assignments can be approved.")
        dimension = assignment.mapping_set.dimension
        if dimension.cardinality == AnalyticalDimension.Cardinality.SINGLE:
            cls._check_single_cardinality_conflict(assignment)
        assignment.governance_status = AnalyticalMappingAssignment.GovernanceStatus.APPROVED
        assignment.authority = AnalyticalMappingAssignment.MappingAuthority.APPROVED
        assignment.approved_by = actor
        assignment.approved_at = timezone.now()
        assignment.save(
            update_fields=[
                "governance_status",
                "authority",
                "approved_by",
                "approved_at",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.ASSIGNMENT_APPROVED,
            project=assignment.mapping_set.project,
            dimension=assignment.mapping_set.dimension,
            mapping_set=assignment.mapping_set,
            assignment=assignment,
            actor=actor,
            target_type=assignment.target_type,
            target_id=cls._target_id(assignment),
            previous_state=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
            resulting_state=assignment.governance_status,
        )
        return assignment

    @classmethod
    def reject_assignment(
        cls,
        assignment: AnalyticalMappingAssignment,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> AnalyticalMappingAssignment:
        """Reject proposed assignment."""
        if assignment.governance_status not in _OPEN_STATUSES:
            raise MappingValidationError("Only proposed assignments can be rejected.")
        prev = assignment.governance_status
        assignment.governance_status = AnalyticalMappingAssignment.GovernanceStatus.REJECTED
        assignment.rejected_by = actor
        assignment.rejected_at = timezone.now()
        assignment.rejection_reason = reason
        assignment.save(
            update_fields=[
                "governance_status",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.ASSIGNMENT_REJECTED,
            project=assignment.mapping_set.project,
            dimension=assignment.mapping_set.dimension,
            mapping_set=assignment.mapping_set,
            assignment=assignment,
            actor=actor,
            target_type=assignment.target_type,
            target_id=cls._target_id(assignment),
            previous_state=prev,
            resulting_state=assignment.governance_status,
            reason_text=reason,
        )
        return assignment

    @classmethod
    def _check_single_cardinality_conflict(cls, assignment: AnalyticalMappingAssignment) -> None:
        """Detect conflicting approved values for single-valued dimensions."""
        others = AnalyticalMappingAssignment.objects.filter(
            mapping_set=assignment.mapping_set,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
        ).exclude(pk=assignment.pk)
        if assignment.target_type == AnalyticalMappingAssignment.TargetType.TASK:
            others = others.filter(task_id=assignment.task_id)
        elif assignment.target_type == AnalyticalMappingAssignment.TargetType.WBS_NODE:
            others = others.filter(wbs_node_id=assignment.wbs_node_id)
        elif assignment.target_type == AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY:
            others = others.filter(schedule_activity_id=assignment.schedule_activity_id)
        else:
            others = others.filter(entity_global_id=assignment.entity_global_id)
        if others.exclude(dimension_value_id=assignment.dimension_value_id).exists():
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.CONFLICT_DETECTED,
                project=assignment.mapping_set.project,
                dimension=assignment.mapping_set.dimension,
                mapping_set=assignment.mapping_set,
                assignment=assignment,
                target_type=assignment.target_type,
                target_id=cls._target_id(assignment),
                reason_code="single_cardinality_conflict",
            )
            raise MappingValidationError(
                "Single-valued dimension conflict — multiple approved values for target."
            )

    @staticmethod
    def _assert_exactly_one_target(
        target_type: str,
        task: Task | None,
        wbs_node: WBSNode | None,
        entity_global_id: str,
        schedule_activity: ScheduleActivity | None,
    ) -> None:
        count = sum(
            [
                task is not None,
                wbs_node is not None,
                schedule_activity is not None,
                bool(entity_global_id),
            ]
        )
        if count != 1:
            raise MappingValidationError("Exactly one mapping target must be specified.")
        if target_type == AnalyticalMappingAssignment.TargetType.TASK and task is None:
            raise MappingValidationError("Task target required for target_type=task.")
        if target_type == AnalyticalMappingAssignment.TargetType.WBS_NODE and wbs_node is None:
            raise MappingValidationError("WBS node target required for target_type=wbs_node.")
        if (
            target_type == AnalyticalMappingAssignment.TargetType.IFC_ENTITY
            and not entity_global_id
        ):
            raise MappingValidationError("entity_global_id required for target_type=ifc_entity.")
        if (
            target_type == AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY
            and schedule_activity is None
        ):
            raise MappingValidationError(
                "schedule_activity required for target_type=schedule_activity."
            )

    @staticmethod
    def _target_id(assignment: AnalyticalMappingAssignment) -> str:
        if assignment.task_id:
            return str(assignment.task_id)
        if assignment.wbs_node_id:
            return str(assignment.wbs_node_id)
        if assignment.schedule_activity_id:
            return str(assignment.schedule_activity_id)
        return assignment.entity_global_id or ""

    @classmethod
    def protect_approved(cls, assignment: AnalyticalMappingAssignment) -> None:
        """Block mutation on approved assignments."""
        if assignment.governance_status == AnalyticalMappingAssignment.GovernanceStatus.APPROVED:
            raise MappingImmutabilityError("Approved assignments are immutable.")

    @classmethod
    def bulk_create_proposals(
        cls,
        rows: list[AnalyticalMappingAssignment],
        *,
        batch_size: int = 500,
    ) -> list[AnalyticalMappingAssignment]:
        """Persist proposals in batches — no per-row save loop."""
        return AnalyticalMappingAssignment.objects.bulk_create(rows, batch_size=batch_size)
