# scheduling/services/governed_mapping/review.py
"""Mapping proposal review and bulk approval (DF-D2)."""

from __future__ import annotations

import logging
from typing import Sequence
from uuid import UUID

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import AnalyticalMappingAssignment, MappingGovernanceEvent
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.exceptions import MappingValidationError

logger = logging.getLogger(__name__)


class MappingReviewService:
    """Controlled proposal review — no approve-all without explicit IDs."""

    @classmethod
    def submit_for_review(
        cls,
        assignment: AnalyticalMappingAssignment,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalMappingAssignment:
        """Move proposed assignment to under_review."""
        if assignment.governance_status != AnalyticalMappingAssignment.GovernanceStatus.PROPOSED:
            raise MappingValidationError("Only proposed assignments can be submitted for review.")
        assignment.governance_status = AnalyticalMappingAssignment.GovernanceStatus.UNDER_REVIEW
        assignment.reviewed_by = actor
        assignment.reviewed_at = timezone.now()
        assignment.save(
            update_fields=["governance_status", "reviewed_by", "reviewed_at", "updated_at"]
        )
        return assignment

    @classmethod
    @transaction.atomic
    def bulk_approve(
        cls,
        assignment_ids: Sequence[UUID],
        *,
        actor: AbstractUser | None = None,
    ) -> list[AnalyticalMappingAssignment]:
        """Atomically approve explicit proposal IDs."""
        assignments = list(
            AnalyticalMappingAssignment.objects.filter(pk__in=assignment_ids)
            .select_related("mapping_set", "mapping_set__dimension")
            .order_by("pk")
        )
        if len(assignments) != len(set(assignment_ids)):
            raise MappingValidationError("One or more assignment IDs not found.")
        approved: list[AnalyticalMappingAssignment] = []
        for assignment in assignments:
            approved.append(
                AnalyticalMappingAssignmentService.approve_assignment(assignment, actor=actor)
            )
        return approved

    @classmethod
    @transaction.atomic
    def bulk_reject(
        cls,
        assignment_ids: Sequence[UUID],
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> list[AnalyticalMappingAssignment]:
        """Atomically reject explicit proposal IDs."""
        assignments = list(
            AnalyticalMappingAssignment.objects.filter(pk__in=assignment_ids).order_by("pk")
        )
        if len(assignments) != len(set(assignment_ids)):
            raise MappingValidationError("One or more assignment IDs not found.")
        rejected: list[AnalyticalMappingAssignment] = []
        for assignment in assignments:
            rejected.append(
                AnalyticalMappingAssignmentService.reject_assignment(
                    assignment, actor=actor, reason=reason
                )
            )
        return rejected

    @classmethod
    def record_activation_failure(
        cls,
        mapping_set,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> None:
        """Audit mapping set activation failure."""
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.SET_ACTIVATION_FAILED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            reason_text=reason,
        )
