# scheduling/services/governed_mapping/mapping_set.py
"""AnalyticalMappingSet lifecycle service (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import AnalyticalDimension, AnalyticalMappingSet, MappingGovernanceEvent
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.exceptions import (
    MappingImmutabilityError,
    MappingTransitionError,
    MappingValidationError,
)

logger = logging.getLogger(__name__)


class AnalyticalMappingSetService:
    """Lifecycle for versioned mapping sets."""

    @staticmethod
    def _next_revision(dimension: AnalyticalDimension) -> int:
        latest = (
            AnalyticalMappingSet.objects.filter(dimension=dimension)
            .order_by("-revision")
            .values_list("revision", flat=True)
            .first()
        )
        return (latest or 0) + 1

    @classmethod
    def create_draft(
        cls,
        *,
        dimension: AnalyticalDimension,
        name: str,
        actor: AbstractUser | None = None,
        source_version=None,
        baseline_version=None,
        supersedes: AnalyticalMappingSet | None = None,
        inherit_wbs_to_tasks: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> AnalyticalMappingSet:
        """Create draft mapping set revision."""
        if dimension.status not in {
            AnalyticalDimension.Status.DRAFT,
            AnalyticalDimension.Status.ACTIVE,
        }:
            raise MappingValidationError("Dimension must be draft or active for mapping sets.")
        if supersedes and supersedes.dimension_id != dimension.pk:
            raise MappingValidationError("Supersedes mapping set must belong to same dimension.")
        mapping_set = AnalyticalMappingSet.objects.create(
            project=dimension.project,
            dimension=dimension,
            name=name,
            source_version=source_version,
            baseline_version=baseline_version,
            status=AnalyticalMappingSet.Status.DRAFT,
            revision=cls._next_revision(dimension),
            supersedes=supersedes,
            inherit_wbs_to_tasks=inherit_wbs_to_tasks,
            metadata=metadata or {},
            created_by=actor,
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.MAPPING_SET_CREATED,
            project=dimension.project,
            dimension=dimension,
            mapping_set=mapping_set,
            actor=actor,
            resulting_state=mapping_set.status,
        )
        return mapping_set

    @classmethod
    def submit(
        cls,
        mapping_set: AnalyticalMappingSet,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalMappingSet:
        """Submit draft mapping set for review."""
        if mapping_set.status != AnalyticalMappingSet.Status.DRAFT:
            raise MappingTransitionError("Only draft mapping sets can be submitted.")
        now = timezone.now()
        mapping_set.status = AnalyticalMappingSet.Status.UNDER_REVIEW
        mapping_set.submitted_by = actor
        mapping_set.submitted_at = now
        mapping_set.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.MAPPING_SET_SUBMITTED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            previous_state=AnalyticalMappingSet.Status.DRAFT,
            resulting_state=mapping_set.status,
        )
        return mapping_set

    @classmethod
    def approve(
        cls,
        mapping_set: AnalyticalMappingSet,
        *,
        actor: AbstractUser | None = None,
        validation_summary: dict[str, Any] | None = None,
    ) -> AnalyticalMappingSet:
        """Approve mapping set under review."""
        if mapping_set.status != AnalyticalMappingSet.Status.UNDER_REVIEW:
            raise MappingTransitionError("Only under-review mapping sets can be approved.")
        now = timezone.now()
        mapping_set.status = AnalyticalMappingSet.Status.APPROVED
        mapping_set.approved_by = actor
        mapping_set.approved_at = now
        if validation_summary is not None:
            mapping_set.validation_summary = validation_summary
        mapping_set.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "validation_summary",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.MAPPING_SET_APPROVED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            previous_state=AnalyticalMappingSet.Status.UNDER_REVIEW,
            resulting_state=mapping_set.status,
        )
        return mapping_set

    @classmethod
    @transaction.atomic
    def activate(
        cls,
        mapping_set: AnalyticalMappingSet,
        *,
        actor: AbstractUser | None = None,
        select_for_analysis: bool = True,
    ) -> AnalyticalMappingSet:
        """Activate approved mapping set."""
        if mapping_set.status not in {
            AnalyticalMappingSet.Status.APPROVED,
            AnalyticalMappingSet.Status.DRAFT,
        }:
            raise MappingTransitionError("Mapping set must be approved or draft to activate.")
        now = timezone.now()
        if select_for_analysis:
            AnalyticalMappingSet.objects.filter(
                dimension_id=mapping_set.dimension_id,
                is_selected_for_analysis=True,
            ).update(is_selected_for_analysis=False)
        mapping_set.status = AnalyticalMappingSet.Status.ACTIVE
        mapping_set.activated_by = actor
        mapping_set.activated_at = now
        mapping_set.is_selected_for_analysis = select_for_analysis
        mapping_set.save(
            update_fields=[
                "status",
                "activated_by",
                "activated_at",
                "is_selected_for_analysis",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.MAPPING_SET_ACTIVATED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            resulting_state=mapping_set.status,
        )
        return mapping_set

    @classmethod
    def reject(
        cls,
        mapping_set: AnalyticalMappingSet,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> AnalyticalMappingSet:
        """Reject draft or under-review mapping set."""
        if mapping_set.status not in {
            AnalyticalMappingSet.Status.DRAFT,
            AnalyticalMappingSet.Status.UNDER_REVIEW,
        }:
            raise MappingTransitionError("Only draft or under-review sets can be rejected.")
        prev = mapping_set.status
        mapping_set.status = AnalyticalMappingSet.Status.REJECTED
        mapping_set.rejected_by = actor
        mapping_set.rejected_at = timezone.now()
        mapping_set.is_selected_for_analysis = False
        mapping_set.save(
            update_fields=[
                "status",
                "rejected_by",
                "rejected_at",
                "is_selected_for_analysis",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.MAPPING_SET_REJECTED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            previous_state=prev,
            resulting_state=mapping_set.status,
            reason_text=reason,
        )
        return mapping_set

    @classmethod
    @transaction.atomic
    def supersede(
        cls,
        mapping_set: AnalyticalMappingSet,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalMappingSet:
        """Supersede active mapping set."""
        if mapping_set.status != AnalyticalMappingSet.Status.ACTIVE:
            raise MappingTransitionError("Only active mapping sets can be superseded.")
        now = timezone.now()
        mapping_set.status = AnalyticalMappingSet.Status.SUPERSEDED
        mapping_set.superseded_at = now
        mapping_set.is_selected_for_analysis = False
        mapping_set.save(
            update_fields=["status", "superseded_at", "is_selected_for_analysis", "updated_at"]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.REVISION_SUPERSEDED,
            project=mapping_set.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=actor,
            previous_state=AnalyticalMappingSet.Status.ACTIVE,
            resulting_state=mapping_set.status,
        )
        return mapping_set

    @classmethod
    def assert_mutable(cls, mapping_set: AnalyticalMappingSet) -> None:
        """Guard assignment mutations."""
        if mapping_set.is_immutable:
            raise MappingImmutabilityError("Approved/active mapping sets are immutable.")
