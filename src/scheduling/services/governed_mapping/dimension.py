# scheduling/services/governed_mapping/dimension.py
"""AnalyticalDimension lifecycle service (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import AnalyticalDimension, MappingGovernanceEvent
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.exceptions import (
    MappingTransitionError,
    MappingValidationError,
)

logger = logging.getLogger(__name__)

_NON_SELECTABLE = frozenset(
    {AnalyticalDimension.Status.REJECTED, AnalyticalDimension.Status.ARCHIVED}
)


class AnalyticalDimensionService:
    """Lifecycle operations for governed analytical dimensions."""

    @staticmethod
    def _next_revision(project_id, dimension_key: str) -> int:
        latest = (
            AnalyticalDimension.objects.filter(project_id=project_id, dimension_key=dimension_key)
            .order_by("-revision_number")
            .values_list("revision_number", flat=True)
            .first()
        )
        return (latest or 0) + 1

    @classmethod
    def create_draft(
        cls,
        *,
        project,
        dimension_key: str,
        name: str,
        dimension_type: str,
        actor: AbstractUser | None = None,
        description: str = "",
        structure_type: str = AnalyticalDimension.StructureType.FLAT,
        cardinality: str = AnalyticalDimension.Cardinality.SINGLE,
        authority_policy: str = AnalyticalDimension.AuthorityPolicy.MANUAL_APPROVAL,
        parent_dimension: AnalyticalDimension | None = None,
        source_metadata: dict[str, Any] | None = None,
        governance_metadata: dict[str, Any] | None = None,
    ) -> AnalyticalDimension:
        """Create a draft dimension revision."""
        if parent_dimension and parent_dimension.project_id != project.id:
            raise MappingValidationError("Parent dimension must belong to the same project.")
        revision = cls._next_revision(project.id, dimension_key)
        dimension = AnalyticalDimension.objects.create(
            project=project,
            dimension_key=dimension_key,
            name=name,
            description=description,
            dimension_type=dimension_type,
            structure_type=structure_type,
            cardinality=cardinality,
            authority_policy=authority_policy,
            status=AnalyticalDimension.Status.DRAFT,
            revision_number=revision,
            parent_dimension=parent_dimension,
            source_metadata=source_metadata or {},
            governance_metadata=governance_metadata or {},
            created_by=actor,
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.DIMENSION_CREATED,
            project=project,
            dimension=dimension,
            actor=actor,
            resulting_state=dimension.status,
        )
        return dimension

    @classmethod
    @transaction.atomic
    def activate(
        cls,
        dimension: AnalyticalDimension,
        *,
        actor: AbstractUser | None = None,
        select_for_analysis: bool = True,
    ) -> AnalyticalDimension:
        """Activate draft dimension revision."""
        if dimension.status != AnalyticalDimension.Status.DRAFT:
            raise MappingTransitionError("Only draft dimensions can be activated.")
        now = timezone.now()
        if select_for_analysis:
            AnalyticalDimension.objects.filter(
                project_id=dimension.project_id,
                dimension_key=dimension.dimension_key,
                is_selected_for_analysis=True,
            ).update(is_selected_for_analysis=False)
        dimension.status = AnalyticalDimension.Status.ACTIVE
        dimension.activated_by = actor
        dimension.activated_at = now
        dimension.is_selected_for_analysis = (
            select_for_analysis and dimension.status not in _NON_SELECTABLE
        )
        dimension.save(
            update_fields=[
                "status",
                "activated_by",
                "activated_at",
                "is_selected_for_analysis",
                "updated_at",
            ]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.DIMENSION_ACTIVATED,
            project=dimension.project,
            dimension=dimension,
            actor=actor,
            previous_state=AnalyticalDimension.Status.DRAFT,
            resulting_state=dimension.status,
        )
        return dimension

    @classmethod
    @transaction.atomic
    def supersede(
        cls,
        dimension: AnalyticalDimension,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalDimension:
        """Mark active dimension as superseded."""
        if dimension.status != AnalyticalDimension.Status.ACTIVE:
            raise MappingTransitionError("Only active dimensions can be superseded.")
        now = timezone.now()
        dimension.status = AnalyticalDimension.Status.SUPERSEDED
        dimension.superseded_at = now
        dimension.is_selected_for_analysis = False
        dimension.save(
            update_fields=["status", "superseded_at", "is_selected_for_analysis", "updated_at"]
        )
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.DIMENSION_SUPERSEDED,
            project=dimension.project,
            dimension=dimension,
            actor=actor,
            previous_state=AnalyticalDimension.Status.ACTIVE,
            resulting_state=dimension.status,
        )
        return dimension

    @classmethod
    def archive(
        cls, dimension: AnalyticalDimension, *, actor: AbstractUser | None = None
    ) -> AnalyticalDimension:
        """Archive a dimension revision."""
        if dimension.status in {
            AnalyticalDimension.Status.ARCHIVED,
            AnalyticalDimension.Status.REJECTED,
        }:
            raise MappingTransitionError("Dimension already terminal.")
        prev = dimension.status
        dimension.status = AnalyticalDimension.Status.ARCHIVED
        dimension.is_selected_for_analysis = False
        dimension.save(update_fields=["status", "is_selected_for_analysis", "updated_at"])
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.DIMENSION_SUPERSEDED,
            project=dimension.project,
            dimension=dimension,
            actor=actor,
            previous_state=prev,
            resulting_state=dimension.status,
            reason_code="archived",
        )
        return dimension

    @classmethod
    def reject(
        cls, dimension: AnalyticalDimension, *, actor: AbstractUser | None = None
    ) -> AnalyticalDimension:
        """Reject a draft dimension."""
        if dimension.status != AnalyticalDimension.Status.DRAFT:
            raise MappingTransitionError("Only draft dimensions can be rejected.")
        dimension.status = AnalyticalDimension.Status.REJECTED
        dimension.is_selected_for_analysis = False
        dimension.save(update_fields=["status", "is_selected_for_analysis", "updated_at"])
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.DIMENSION_SUPERSEDED,
            project=dimension.project,
            dimension=dimension,
            actor=actor,
            previous_state=AnalyticalDimension.Status.DRAFT,
            resulting_state=dimension.status,
            reason_code="rejected",
        )
        return dimension
