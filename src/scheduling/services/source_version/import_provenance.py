# scheduling/services/source_version/import_provenance.py
"""End-to-end import provenance coordinator (DF-A1.1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction

from scheduling.models import ScheduleImportRun, ScheduleSourceVersion, Task
from scheduling.services.source_version.contracts import ImportRunCounts
from scheduling.services.source_version.failure_hooks import maybe_raise
from scheduling.services.source_version.identity_adapters import BatchScheduleActivityLinker
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.source_version.import_run import ScheduleImportRunService
from scheduling.services.source_version.source_version import ScheduleSourceVersionService

logger = logging.getLogger(__name__)

_MAX_ERROR_SUMMARY = 2000


@dataclass(frozen=True)
class ImportProvenanceContext:
    """Metadata captured at import save time."""

    source_type: str
    source_filename: str
    content_hash: str
    mode: str
    data_date: date | None
    importer_version: str = ""
    parser_version: str = ""
    external_project_id: str = ""
    external_schedule_id: str = ""
    external_revision: str = ""
    validation_summary: dict[str, Any] | None = None


class ScheduleImportProvenanceCoordinator:
    """Wrap schedule import execution with auditable provenance lifecycle."""

    def __init__(self, project, user=None) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.user = user
        self._run_service = ScheduleImportRunService(project, user)
        self._version_service = ScheduleSourceVersionService(project, user)

    def start_run(self, ctx: ImportProvenanceContext) -> str:
        """Create a running import run record."""
        result = self._run_service.start_run(
            source_type=ctx.source_type,
            source_filename=ctx.source_filename,
            mode=ctx.mode,
            content_hash=ctx.content_hash,
            importer_version=ctx.importer_version,
            parser_version=ctx.parser_version,
        )
        if not result.run_id:
            raise RuntimeError("Failed to start import run.")
        return result.run_id

    @transaction.atomic
    def complete_success(
        self,
        run_id: str,
        ctx: ImportProvenanceContext,
        persist_result: ImportPersistResult,
    ) -> str:
        """Attach provenance after task persistence — promote version and mark run succeeded."""
        if persist_result.current_source is None:
            raise ValueError("Import persistence did not create ScheduleSource.")

        candidate = self._version_service.create_candidate(
            source_type=ctx.source_type,
            source_filename=ctx.source_filename,
            data_date=ctx.data_date,
            content_hash=ctx.content_hash,
            external_project_id=ctx.external_project_id,
            external_schedule_id=ctx.external_schedule_id,
            external_revision=ctx.external_revision,
            importer_version=ctx.importer_version,
            parser_version=ctx.parser_version,
            source_metadata={
                "legacy_schedule_source_id": str(persist_result.current_source.pk),
                "filename": ctx.source_filename,
            },
            validation_summary=ctx.validation_summary or {},
            schedule_source_id=str(persist_result.current_source.pk),
        )
        if not candidate.version_id:
            raise RuntimeError(candidate.error or "Failed to create candidate source version.")

        version_id = candidate.version_id
        touched_items = list(
            zip(persist_result.touched_pks, persist_result.touched_task_data, strict=False)
        )
        activity_map = BatchScheduleActivityLinker(self.project, ctx.source_type).link_task_rows(
            touched_items
        )
        maybe_raise("after_activity_link")

        if persist_result.touched_pks:
            Task.objects.filter(pk__in=persist_result.touched_pks).update(
                source_version_id=version_id
            )
            tasks = list(Task.objects.filter(pk__in=persist_result.touched_pks))
            for task in tasks:
                act_id = activity_map.get(str(task.pk))
                if act_id:
                    task.schedule_activity_id = act_id
            Task.objects.bulk_update(tasks, ["schedule_activity"])
            maybe_raise("after_provenance_assign")

        maybe_raise("before_version_activation")
        accepted = self._version_service.accept_as_current(version_id)
        if accepted.error:
            raise RuntimeError(accepted.error)

        counts = ImportRunCounts(
            task_count=persist_result.created + persist_result.updated + persist_result.unchanged,
            dependency_count=persist_result.dep_count,
            skipped_count=persist_result.skipped_count,
            warning_count=persist_result.skipped_count,
            error_count=0,
            validation_summary=ctx.validation_summary or {},
        )
        run_result = self._run_service.mark_succeeded(
            run_id,
            source_version_id=version_id,
            counts=counts,
        )
        if run_result.error:
            raise RuntimeError(run_result.error)
        maybe_raise("before_import_run_success")
        return version_id

    @transaction.atomic
    def complete_failure(
        self,
        run_id: str,
        *,
        error_summary: str,
        candidate_version_id: str | None = None,
        counts: ImportRunCounts | None = None,
    ) -> None:
        """Mark run failed and reject orphan candidate version if present."""
        safe_error = (error_summary or "Import failed.")[:_MAX_ERROR_SUMMARY]
        if candidate_version_id:
            try:
                self._version_service.reject_candidate(
                    candidate_version_id,
                    reason=safe_error,
                )
            except ScheduleSourceVersion.DoesNotExist:
                logger.warning(
                    "Candidate version %s missing during failure cleanup",
                    candidate_version_id,
                )
        self._run_service.mark_failed(run_id, error_summary=safe_error, counts=counts)

    @staticmethod
    def resolve_mode(replace_mode: bool, preview: bool = False) -> str:
        """Map view flags to ScheduleImportRun.Mode."""
        if preview:
            return ScheduleImportRun.Mode.PREVIEW
        if replace_mode:
            return ScheduleImportRun.Mode.REPLACE
        return ScheduleImportRun.Mode.UPDATE
