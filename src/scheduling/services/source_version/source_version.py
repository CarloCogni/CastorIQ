# scheduling/services/source_version/source_version.py
"""Schedule source version lifecycle — no failed version becomes current."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.db.models import Max

from scheduling.models import ScheduleSourceVersion
from scheduling.services.source_version.contracts import SourceVersionResult

logger = logging.getLogger(__name__)


class ScheduleSourceVersionService:
    """Manage accepted schedule source versions for a project."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.user = user

    def get_current(self) -> ScheduleSourceVersion | None:
        """Return the project's current source version, if any."""
        return (
            ScheduleSourceVersion.objects.filter(
                project_id=self.project_id,
                status=ScheduleSourceVersion.Status.CURRENT,
            )
            .select_related("schedule_source", "supersedes")
            .first()
        )

    def _next_version_number(self) -> int:
        current_max = (
            ScheduleSourceVersion.objects.filter(project_id=self.project_id).aggregate(
                mx=Max("version_number")
            )["mx"]
            or 0
        )
        return int(current_max) + 1

    @transaction.atomic
    def create_candidate(
        self,
        *,
        source_type: str,
        source_filename: str,
        imported_at: datetime | None = None,
        data_date=None,
        content_hash: str = "",
        external_project_id: str = "",
        external_schedule_id: str = "",
        external_revision: str = "",
        importer_version: str = "",
        parser_version: str = "",
        source_metadata: dict[str, Any] | None = None,
        validation_summary: dict[str, Any] | None = None,
        schedule_source_id: str | None = None,
    ) -> SourceVersionResult:
        """Create a candidate source version (not yet current)."""
        version = ScheduleSourceVersion.objects.create(
            project_id=self.project_id,
            schedule_source_id=schedule_source_id,
            version_number=self._next_version_number(),
            source_type=source_type,
            source_filename=source_filename,
            content_hash=content_hash,
            external_project_id=external_project_id,
            external_schedule_id=external_schedule_id,
            external_revision=external_revision,
            data_date=data_date,
            imported_at=imported_at or datetime.now(UTC),
            importer_version=importer_version,
            parser_version=parser_version,
            status=ScheduleSourceVersion.Status.CANDIDATE,
            source_metadata=source_metadata or {},
            validation_summary=validation_summary or {},
            created_by=self.user,
        )
        return SourceVersionResult(
            version_id=str(version.pk),
            status=version.status,
            version_number=version.version_number,
        )

    @transaction.atomic
    def accept_as_current(self, version_id: str) -> SourceVersionResult:
        """Promote candidate to current and supersede prior current version."""
        version = ScheduleSourceVersion.objects.select_for_update().get(
            pk=version_id,
            project_id=self.project_id,
        )
        if version.status == ScheduleSourceVersion.Status.CURRENT:
            return SourceVersionResult(
                version_id=str(version.pk),
                status=version.status,
                version_number=version.version_number,
            )
        if version.status in (
            ScheduleSourceVersion.Status.REJECTED,
            ScheduleSourceVersion.Status.ARCHIVED,
        ):
            return SourceVersionResult(
                version_id=str(version.pk),
                status=version.status,
                version_number=version.version_number,
                error="Cannot accept rejected or archived version.",
            )

        prior = (
            ScheduleSourceVersion.objects.select_for_update()
            .filter(
                project_id=self.project_id,
                status=ScheduleSourceVersion.Status.CURRENT,
            )
            .exclude(pk=version.pk)
            .first()
        )
        if prior:
            prior.status = ScheduleSourceVersion.Status.SUPERSEDED
            prior.save(update_fields=["status", "updated_at"])
            version.supersedes = prior

        version.status = ScheduleSourceVersion.Status.CURRENT
        version.save(update_fields=["status", "supersedes", "updated_at"])
        logger.info(
            "Accepted source version %s as current for project %s",
            version.pk,
            self.project_id,
        )
        return SourceVersionResult(
            version_id=str(version.pk),
            status=version.status,
            version_number=version.version_number,
        )

    @transaction.atomic
    def reject_candidate(self, version_id: str, reason: str = "") -> SourceVersionResult:
        """Mark a candidate version rejected — never becomes current."""
        version = ScheduleSourceVersion.objects.select_for_update().get(
            pk=version_id,
            project_id=self.project_id,
        )
        if version.status == ScheduleSourceVersion.Status.CURRENT:
            return SourceVersionResult(
                version_id=str(version.pk),
                status=version.status,
                error="Cannot reject the current version.",
            )
        version.status = ScheduleSourceVersion.Status.REJECTED
        summary = dict(version.validation_summary or {})
        if reason:
            summary["rejection_reason"] = reason
        version.validation_summary = summary
        version.save(update_fields=["status", "validation_summary", "updated_at"])
        return SourceVersionResult(
            version_id=str(version.pk),
            status=version.status,
            version_number=version.version_number,
        )
