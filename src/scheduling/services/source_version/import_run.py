# scheduling/services/source_version/import_run.py
"""Schedule import run audit lifecycle."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from django.db import transaction

from scheduling.models import ScheduleImportRun, ScheduleSourceVersion
from scheduling.services.source_version.contracts import ImportRunCounts, ImportRunResult

logger = logging.getLogger(__name__)

_TERMINAL = frozenset(
    {
        ScheduleImportRun.Status.SUCCEEDED,
        ScheduleImportRun.Status.FAILED,
        ScheduleImportRun.Status.CANCELLED,
    }
)


class ScheduleImportRunService:
    """Record import attempts independently from accepted source versions."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.user = user

    @transaction.atomic
    def start_run(
        self,
        *,
        source_type: str,
        source_filename: str,
        mode: str = ScheduleImportRun.Mode.UNKNOWN,
        content_hash: str = "",
        schedule_source_id: str | None = None,
        importer_version: str = "",
        parser_version: str = "",
    ) -> ImportRunResult:
        """Create a pending import run and mark it running."""
        run = ScheduleImportRun.objects.create(
            project_id=self.project_id,
            schedule_source_id=schedule_source_id,
            source_type=source_type,
            source_filename=source_filename,
            content_hash=content_hash,
            status=ScheduleImportRun.Status.RUNNING,
            mode=mode,
            started_at=datetime.now(UTC),
            importer_version=importer_version,
            parser_version=parser_version,
            requested_by=self.user,
        )
        return ImportRunResult(run_id=str(run.pk), status=run.status)

    @transaction.atomic
    def mark_succeeded(
        self,
        run_id: str,
        *,
        source_version_id: str | None,
        counts: ImportRunCounts | None = None,
    ) -> ImportRunResult:
        """Complete run successfully; attach accepted source version if provided."""
        run = ScheduleImportRun.objects.select_for_update().get(
            pk=run_id,
            project_id=self.project_id,
        )
        if run.status in _TERMINAL:
            return ImportRunResult(
                run_id=str(run.pk),
                status=run.status,
                source_version_id=str(run.source_version_id) if run.source_version_id else None,
                error="Import run already terminal.",
            )
        if source_version_id:
            if not ScheduleSourceVersion.objects.filter(
                pk=source_version_id,
                project_id=self.project_id,
            ).exists():
                return ImportRunResult(
                    run_id=str(run.pk),
                    status=run.status,
                    error="Source version not found for project.",
                )
            run.source_version_id = source_version_id

        payload = counts or ImportRunCounts()
        run.status = ScheduleImportRun.Status.SUCCEEDED
        run.completed_at = datetime.now(UTC)
        run.task_count = payload.task_count
        run.dependency_count = payload.dependency_count
        run.skipped_count = payload.skipped_count
        run.warning_count = payload.warning_count
        run.error_count = payload.error_count
        run.validation_summary = payload.validation_summary
        run.save()
        return ImportRunResult(
            run_id=str(run.pk),
            status=run.status,
            source_version_id=str(run.source_version_id) if run.source_version_id else None,
        )

    @transaction.atomic
    def mark_failed(
        self,
        run_id: str,
        *,
        error_summary: str = "",
        counts: ImportRunCounts | None = None,
    ) -> ImportRunResult:
        """Fail run — no source version attached."""
        run = ScheduleImportRun.objects.select_for_update().get(
            pk=run_id,
            project_id=self.project_id,
        )
        if run.status in _TERMINAL:
            return ImportRunResult(
                run_id=str(run.pk),
                status=run.status,
                error="Import run already terminal.",
            )
        payload = counts or ImportRunCounts()
        run.status = ScheduleImportRun.Status.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_summary = error_summary
        run.task_count = payload.task_count
        run.dependency_count = payload.dependency_count
        run.skipped_count = payload.skipped_count
        run.warning_count = payload.warning_count
        run.error_count = max(payload.error_count, 1)
        run.validation_summary = payload.validation_summary
        run.source_version_id = None
        run.save()
        return ImportRunResult(run_id=str(run.pk), status=run.status)
