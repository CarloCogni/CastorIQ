# scheduling/views_source_provenance.py
"""Read-only source version and task provenance endpoints (DF-A1)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import ScheduleImportRun, ScheduleSourceVersion, Task

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25


def _paginate(request, qs, *, order_by: str):
    """Return paginated slice and metadata."""
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    try:
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    total = qs.count()
    start = (page - 1) * page_size
    items = list(qs.order_by(order_by)[start : start + page_size])
    return items, {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + page_size < total,
    }


def _serialize_source_version(version: ScheduleSourceVersion) -> dict:
    return {
        "id": str(version.pk),
        "version_number": version.version_number,
        "source_type": version.source_type,
        "source_filename": version.source_filename,
        "content_hash": version.content_hash or None,
        "data_date": version.data_date.isoformat() if version.data_date else None,
        "imported_at": version.imported_at.isoformat(),
        "status": version.status,
        "importer_version": version.importer_version or None,
        "parser_version": version.parser_version or None,
        "legacy_schedule_source_id": str(version.schedule_source_id)
        if version.schedule_source_id
        else None,
        "supersedes_id": str(version.supersedes_id) if version.supersedes_id else None,
        "validation_summary": version.validation_summary or {},
    }


def _serialize_import_run(run: ScheduleImportRun) -> dict:
    return {
        "id": str(run.pk),
        "source_type": run.source_type,
        "source_filename": run.source_filename,
        "content_hash": run.content_hash or None,
        "status": run.status,
        "mode": run.mode,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "source_version_id": str(run.source_version_id) if run.source_version_id else None,
        "task_count": run.task_count,
        "dependency_count": run.dependency_count,
        "skipped_count": run.skipped_count,
        "warning_count": run.warning_count,
        "error_count": run.error_count,
        "error_summary": run.error_summary or None,
        "validation_summary": run.validation_summary or {},
    }


class SourceVersionListView(ProjectAccessMixin, View):
    """GET — list schedule source versions for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = ScheduleSourceVersion.objects.filter(project=project).select_related("supersedes")
        items, pagination = _paginate(request, qs, order_by="-imported_at")
        current = ScheduleSourceVersion.objects.filter(
            project=project,
            status=ScheduleSourceVersion.Status.CURRENT,
        ).first()
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "current_source_version_id": str(current.pk) if current else None,
                "items": [_serialize_source_version(v) for v in items],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ImportRunListView(ProjectAccessMixin, View):
    """GET — list schedule import runs for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = ScheduleImportRun.objects.filter(project=project)
        items, pagination = _paginate(request, qs, order_by="-started_at")
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "items": [_serialize_import_run(r) for r in items],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class TaskProvenanceView(ProjectAccessMixin, View):
    """GET — provenance detail for one task."""

    def get(self, request, **kwargs):
        project = self.get_project()
        task = get_object_or_404(
            Task.objects.select_related(
                "schedule_activity",
                "source_version",
                "schedule_source",
            ),
            pk=kwargs["task_pk"],
            project=project,
        )
        activity = task.schedule_activity
        version = task.source_version
        legacy = task.schedule_source
        return JsonResponse(
            {
                "task_id": str(task.pk),
                "project_id": str(project.pk),
                "provenance_available": bool(activity or version),
                "schedule_activity": {
                    "id": str(activity.pk),
                    "canonical_activity_key": activity.canonical_activity_key,
                    "identity_status": activity.identity_status,
                    "external_activity_id": activity.external_activity_id or None,
                    "activity_code": activity.activity_code or None,
                }
                if activity
                else None,
                "source_version": _serialize_source_version(version) if version else None,
                "legacy_schedule_source": {
                    "id": str(legacy.pk),
                    "filename": legacy.filename,
                    "source_format": legacy.source_format,
                    "imported_at": legacy.imported_at.isoformat(),
                    "data_date": legacy.data_date.isoformat() if legacy.data_date else None,
                }
                if legacy
                else None,
                "caveats": [
                    "Legacy tasks may have null schedule_activity and source_version until backfill.",
                ],
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
