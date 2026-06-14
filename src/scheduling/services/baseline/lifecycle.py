# scheduling/services/baseline/lifecycle.py
"""BaselineVersion lifecycle — draft, publish, approve, select, supersede, archive."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import BaselineAuditEvent, BaselineVersion, ScheduleSourceVersion
from scheduling.services.baseline.audit import record_baseline_event
from scheduling.services.baseline.exceptions import (
    BaselineImmutabilityError,
    BaselineTransitionError,
    BaselineValidationError,
)

logger = logging.getLogger(__name__)

_IMMUTABLE_FIELDS = frozenset(
    {
        "source_version",
        "baseline_type",
        "data_date",
        "effective_date",
        "currency",
        "methodology_version",
        "revision_number",
        "parent_baseline",
    }
)

_SELECTABLE_STATUSES = frozenset({BaselineVersion.Status.PUBLISHED})
_NON_SELECTABLE_STATUSES = frozenset(
    {BaselineVersion.Status.REJECTED, BaselineVersion.Status.ARCHIVED}
)


class BaselineVersionService:
    """Lifecycle operations for BaselineVersion."""

    @staticmethod
    def _next_revision(project_id) -> int:
        latest = (
            BaselineVersion.objects.filter(project_id=project_id)
            .order_by("-revision_number")
            .values_list("revision_number", flat=True)
            .first()
        )
        return (latest or 0) + 1

    @staticmethod
    def _validate_source_version(
        project_id,
        source_version: ScheduleSourceVersion | None,
    ) -> None:
        if source_version is None:
            return
        if source_version.project_id != project_id:
            raise BaselineValidationError("Source version must belong to the same project.")
        if source_version.status == ScheduleSourceVersion.Status.REJECTED:
            raise BaselineValidationError("Rejected source versions cannot anchor a baseline.")

    @staticmethod
    def _validate_parent(project_id, parent: BaselineVersion | None) -> None:
        if parent is None:
            return
        if parent.project_id != project_id:
            raise BaselineValidationError("Parent baseline must belong to the same project.")

    @classmethod
    def create_draft(
        cls,
        *,
        project,
        name: str,
        baseline_type: str,
        actor: AbstractUser | None = None,
        source_version: ScheduleSourceVersion | None = None,
        parent_baseline: BaselineVersion | None = None,
        code: str = "",
        data_date=None,
        effective_date=None,
        currency: str = "",
        methodology_version: str = "",
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> BaselineVersion:
        """Create a draft BaselineVersion."""
        cls._validate_source_version(project.id, source_version)
        cls._validate_parent(project.id, parent_baseline)
        revision = cls._next_revision(project.id)
        baseline = BaselineVersion.objects.create(
            project=project,
            source_version=source_version,
            name=name,
            code=code,
            baseline_type=baseline_type,
            status=BaselineVersion.Status.DRAFT,
            data_date=data_date,
            effective_date=effective_date,
            parent_baseline=parent_baseline,
            revision_number=revision,
            currency=currency,
            methodology_version=methodology_version,
            notes=notes,
            created_by=actor,
            metadata=metadata or {},
        )
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_CREATED,
            actor=actor,
            new_status=baseline.status,
            source_version=source_version,
        )
        logger.info("Created draft baseline %s for project %s", baseline.id, project.id)
        return baseline

    @staticmethod
    def _assert_draft(baseline: BaselineVersion) -> None:
        if baseline.status != BaselineVersion.Status.DRAFT:
            raise BaselineTransitionError("Only draft baselines can be modified or published.")

    @classmethod
    @transaction.atomic
    def publish(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> BaselineVersion:
        """Publish a draft baseline — becomes immutable."""
        cls._assert_draft(baseline)
        previous = baseline.status
        baseline.status = BaselineVersion.Status.PUBLISHED
        baseline.published_by = actor
        baseline.published_at = timezone.now()
        baseline.save(update_fields=["status", "published_by", "published_at", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_PUBLISHED,
            actor=actor,
            previous_status=previous,
            new_status=baseline.status,
            reason=reason,
        )
        return baseline

    @classmethod
    @transaction.atomic
    def approve(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None,
        reason: str = "",
        authority_metadata: dict[str, Any] | None = None,
    ) -> BaselineVersion:
        """Approve a published baseline — requires published state and actor."""
        if baseline.status != BaselineVersion.Status.PUBLISHED:
            raise BaselineTransitionError("Only published baselines can be approved.")
        if baseline.baseline_type != BaselineVersion.BaselineType.APPROVED:
            raise BaselineTransitionError(
                "Approval applies only to baselines with type 'approved'."
            )
        if actor is None:
            raise BaselineValidationError("Approval requires an authenticated actor.")
        baseline.approved_by = actor
        baseline.approved_at = timezone.now()
        meta = dict(baseline.metadata or {})
        if authority_metadata:
            meta["approval_authority"] = authority_metadata
        if reason:
            meta["approval_note"] = reason
        baseline.metadata = meta
        baseline.save(update_fields=["approved_by", "approved_at", "metadata", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_APPROVED,
            actor=actor,
            previous_status=baseline.status,
            new_status=baseline.status,
            reason=reason,
            metadata=authority_metadata or {},
        )
        return baseline

    @classmethod
    @transaction.atomic
    def select_for_analysis(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> BaselineVersion:
        """Select baseline for analysis — unselects prior selection transactionally."""
        if baseline.status in _NON_SELECTABLE_STATUSES:
            raise BaselineTransitionError("Rejected or archived baselines cannot be selected.")
        if baseline.status not in _SELECTABLE_STATUSES:
            raise BaselineTransitionError("Only published baselines can be selected.")
        project = baseline.project
        BaselineVersion.objects.filter(
            project=project,
            is_selected_for_analysis=True,
        ).exclude(pk=baseline.pk).update(is_selected_for_analysis=False)
        baseline.is_selected_for_analysis = True
        baseline.save(update_fields=["is_selected_for_analysis", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_SELECTED,
            actor=actor,
            previous_status=baseline.status,
            new_status=baseline.status,
            reason=reason,
        )
        return baseline

    @classmethod
    def get_selected_baseline(cls, project) -> BaselineVersion | None:
        """Return the project's selected baseline, if any."""
        return (
            BaselineVersion.objects.filter(
                project=project,
                is_selected_for_analysis=True,
            )
            .select_related("source_version", "approved_by", "published_by")
            .first()
        )

    @classmethod
    @transaction.atomic
    def supersede(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> BaselineVersion:
        """Mark published baseline as superseded."""
        if baseline.status != BaselineVersion.Status.PUBLISHED:
            raise BaselineTransitionError("Only published baselines can be superseded.")
        previous = baseline.status
        baseline.status = BaselineVersion.Status.SUPERSEDED
        baseline.superseded_at = timezone.now()
        if baseline.is_selected_for_analysis:
            baseline.is_selected_for_analysis = False
        baseline.save(
            update_fields=[
                "status",
                "superseded_at",
                "is_selected_for_analysis",
                "updated_at",
            ]
        )
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_SUPERSEDED,
            actor=actor,
            previous_status=previous,
            new_status=baseline.status,
            reason=reason,
        )
        return baseline

    @classmethod
    @transaction.atomic
    def archive(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> BaselineVersion:
        """Archive a published or superseded baseline."""
        if baseline.status not in (
            BaselineVersion.Status.PUBLISHED,
            BaselineVersion.Status.SUPERSEDED,
        ):
            raise BaselineTransitionError("Only published or superseded baselines can be archived.")
        previous = baseline.status
        baseline.status = BaselineVersion.Status.ARCHIVED
        if baseline.is_selected_for_analysis:
            baseline.is_selected_for_analysis = False
        baseline.save(update_fields=["status", "is_selected_for_analysis", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_ARCHIVED,
            actor=actor,
            previous_status=previous,
            new_status=baseline.status,
            reason=reason,
        )
        return baseline

    @classmethod
    @transaction.atomic
    def reject(
        cls,
        baseline: BaselineVersion,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> BaselineVersion:
        """Reject a draft baseline."""
        cls._assert_draft(baseline)
        previous = baseline.status
        baseline.status = BaselineVersion.Status.REJECTED
        baseline.save(update_fields=["status", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_REJECTED,
            actor=actor,
            previous_status=previous,
            new_status=baseline.status,
            reason=reason,
        )
        return baseline

    @classmethod
    def guard_mutation(cls, baseline: BaselineVersion, field_names: set[str]) -> None:
        """Raise if attempting to mutate immutable fields on published baseline."""
        if not baseline.is_immutable:
            return
        blocked = field_names & _IMMUTABLE_FIELDS
        if blocked:
            raise BaselineImmutabilityError(
                f"Cannot modify {', '.join(sorted(blocked))} on published baseline."
            )

    @classmethod
    def update_draft(
        cls,
        baseline: BaselineVersion,
        *,
        updates: dict[str, Any],
    ) -> BaselineVersion:
        """Update allowed draft fields only."""
        cls._assert_draft(baseline)
        allowed = {
            "name",
            "code",
            "data_date",
            "effective_date",
            "currency",
            "methodology_version",
            "notes",
            "metadata",
            "validation_summary",
        }
        update_fields = []
        for key, value in updates.items():
            if key not in allowed:
                raise BaselineValidationError(f"Field '{key}' cannot be updated on draft.")
            setattr(baseline, key, value)
            update_fields.append(key)
        if update_fields:
            update_fields.append("updated_at")
            baseline.save(update_fields=update_fields)
        return baseline
