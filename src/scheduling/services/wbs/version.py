# scheduling/services/wbs/version.py
"""WBSVersion lifecycle — draft, activate, supersede, archive, reject (DF-C1)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import ScheduleSourceVersion, WBSVersion
from scheduling.services.wbs.exceptions import (
    WBSImmutabilityError,
    WBSTransitionError,
    WBSValidationError,
)

logger = logging.getLogger(__name__)

_SELECTABLE_STATUSES = frozenset({WBSVersion.Status.ACTIVE})
_NON_SELECTABLE_STATUSES = frozenset({WBSVersion.Status.REJECTED, WBSVersion.Status.ARCHIVED})


class WBSVersionService:
    """Lifecycle operations for canonical WBSVersion records."""

    @staticmethod
    def _next_revision(project_id) -> int:
        latest = (
            WBSVersion.objects.filter(project_id=project_id)
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
            raise WBSValidationError("Source version must belong to the same project.")
        if source_version.status == ScheduleSourceVersion.Status.REJECTED:
            raise WBSValidationError("Rejected source versions cannot anchor a WBS version.")

    @staticmethod
    def _validate_parent(project_id, parent: WBSVersion | None) -> None:
        if parent is None:
            return
        if parent.project_id != project_id:
            raise WBSValidationError("Parent WBS version must belong to the same project.")

    @classmethod
    def create_draft(
        cls,
        *,
        project,
        name: str,
        actor: AbstractUser | None = None,
        origin: str = WBSVersion.Origin.MANUAL,
        source_version: ScheduleSourceVersion | None = None,
        parent_version: WBSVersion | None = None,
        code: str = "",
        data_date=None,
        source_metadata: dict[str, Any] | None = None,
        validation_summary: dict[str, Any] | None = None,
    ) -> WBSVersion:
        """Create a draft WBSVersion."""
        cls._validate_source_version(project.id, source_version)
        cls._validate_parent(project.id, parent_version)
        revision = cls._next_revision(project.id)
        version = WBSVersion.objects.create(
            project=project,
            source_version=source_version,
            name=name,
            code=code,
            origin=origin,
            status=WBSVersion.Status.DRAFT,
            revision_number=revision,
            data_date=data_date,
            parent_version=parent_version,
            source_metadata=source_metadata or {},
            validation_summary=validation_summary or {},
            created_by=actor,
        )
        logger.info("WBS draft created project=%s version=%s", project.pk, version.pk)
        return version

    @classmethod
    @transaction.atomic
    def activate(
        cls,
        version: WBSVersion,
        *,
        actor: AbstractUser | None = None,
        select_for_analysis: bool = True,
    ) -> WBSVersion:
        """Activate a draft WBS version and optionally select it for analysis."""
        if version.status != WBSVersion.Status.DRAFT:
            raise WBSTransitionError("Only draft WBS versions can be activated.")
        now = timezone.now()
        if select_for_analysis:
            WBSVersion.objects.filter(
                project_id=version.project_id,
                is_selected_for_analysis=True,
            ).update(is_selected_for_analysis=False)
        version.status = WBSVersion.Status.ACTIVE
        version.activated_by = actor
        version.activated_at = now
        version.is_selected_for_analysis = select_for_analysis
        version.save(
            update_fields=[
                "status",
                "activated_by",
                "activated_at",
                "is_selected_for_analysis",
                "updated_at",
            ]
        )
        logger.info("WBS activated version=%s", version.pk)
        return version

    @classmethod
    @transaction.atomic
    def supersede(
        cls,
        *,
        current: WBSVersion,
        successor: WBSVersion,
        actor: AbstractUser | None = None,
    ) -> tuple[WBSVersion, WBSVersion]:
        """Mark current active version superseded and activate successor draft."""
        if current.project_id != successor.project_id:
            raise WBSValidationError("Successor must belong to the same project.")
        if current.status != WBSVersion.Status.ACTIVE:
            raise WBSTransitionError("Only active WBS versions can be superseded.")
        if successor.status != WBSVersion.Status.DRAFT:
            raise WBSTransitionError("Successor must be draft.")
        now = timezone.now()
        current.status = WBSVersion.Status.SUPERSEDED
        current.is_selected_for_analysis = False
        current.superseded_at = now
        current.save(
            update_fields=["status", "is_selected_for_analysis", "superseded_at", "updated_at"]
        )
        successor.parent_version = current
        successor.save(update_fields=["parent_version", "updated_at"])
        cls.activate(successor, actor=actor, select_for_analysis=True)
        return current, successor

    @classmethod
    def archive(cls, version: WBSVersion, *, actor: AbstractUser | None = None) -> WBSVersion:
        """Archive a non-active WBS version."""
        del actor
        if version.status == WBSVersion.Status.ACTIVE:
            raise WBSTransitionError("Active WBS versions must be superseded before archive.")
        if version.status not in {
            WBSVersion.Status.DRAFT,
            WBSVersion.Status.SUPERSEDED,
            WBSVersion.Status.REJECTED,
        }:
            raise WBSTransitionError(f"Cannot archive WBS version in status {version.status}.")
        version.status = WBSVersion.Status.ARCHIVED
        version.is_selected_for_analysis = False
        version.save(update_fields=["status", "is_selected_for_analysis", "updated_at"])
        return version

    @classmethod
    def reject(cls, version: WBSVersion, *, actor: AbstractUser | None = None) -> WBSVersion:
        """Reject a draft WBS version."""
        del actor
        if version.status != WBSVersion.Status.DRAFT:
            raise WBSTransitionError("Only draft WBS versions can be rejected.")
        version.status = WBSVersion.Status.REJECTED
        version.is_selected_for_analysis = False
        version.save(update_fields=["status", "is_selected_for_analysis", "updated_at"])
        return version

    @classmethod
    def select_for_analysis(cls, version: WBSVersion) -> WBSVersion:
        """Select an active WBS version for primary analytics."""
        if version.status in _NON_SELECTABLE_STATUSES:
            raise WBSTransitionError("Rejected or archived WBS versions cannot be selected.")
        if version.status not in _SELECTABLE_STATUSES:
            raise WBSTransitionError("Only active WBS versions can be selected for analysis.")
        WBSVersion.objects.filter(
            project_id=version.project_id,
            is_selected_for_analysis=True,
        ).exclude(pk=version.pk).update(is_selected_for_analysis=False)
        version.is_selected_for_analysis = True
        version.save(update_fields=["is_selected_for_analysis", "updated_at"])
        return version

    @staticmethod
    def get_selected(project) -> WBSVersion | None:
        """Return the selected WBS version for a project, if any."""
        return (
            WBSVersion.objects.filter(project=project, is_selected_for_analysis=True)
            .select_related("source_version")
            .first()
        )

    @staticmethod
    def assert_mutable(version: WBSVersion) -> None:
        """Raise when hierarchy mutations are not allowed."""
        if version.is_hierarchy_immutable:
            raise WBSImmutabilityError(
                "WBS hierarchy is immutable for active or superseded versions."
            )
