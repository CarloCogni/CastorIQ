# scheduling/views_baseline.py
"""Read-only baseline domain endpoints (DF-A2)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import BaselineTaskState, BaselineVersion
from scheduling.services.baseline.comparison import BaselineComparisonService
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.baseline.population import BaselinePopulationService

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


def _serialize_baseline(baseline: BaselineVersion) -> dict:
    coverage = BaselinePopulationService.coverage_summary(baseline)
    return {
        "id": str(baseline.pk),
        "name": baseline.name,
        "code": baseline.code or None,
        "baseline_type": baseline.baseline_type,
        "status": baseline.status,
        "revision_number": baseline.revision_number,
        "data_date": baseline.data_date.isoformat() if baseline.data_date else None,
        "effective_date": baseline.effective_date.isoformat() if baseline.effective_date else None,
        "source_version_id": str(baseline.source_version_id)
        if baseline.source_version_id
        else None,
        "parent_baseline_id": str(baseline.parent_baseline_id)
        if baseline.parent_baseline_id
        else None,
        "currency": baseline.currency or None,
        "methodology_version": baseline.methodology_version or None,
        "is_selected_for_analysis": baseline.is_selected_for_analysis,
        "approved_by_id": str(baseline.approved_by_id) if baseline.approved_by_id else None,
        "approved_at": baseline.approved_at.isoformat() if baseline.approved_at else None,
        "published_at": baseline.published_at.isoformat() if baseline.published_at else None,
        "created_at": baseline.created_at.isoformat(),
        "coverage": coverage,
        "metadata": baseline.metadata or {},
        "validation_summary": baseline.validation_summary or {},
    }


def _serialize_task_state(state: BaselineTaskState) -> dict:
    return {
        "id": str(state.pk),
        "schedule_activity_id": str(state.schedule_activity_id),
        "activity_code": state.activity_code or None,
        "name_snapshot": state.name_snapshot,
        "planned_start": state.planned_start.isoformat() if state.planned_start else None,
        "planned_finish": state.planned_finish.isoformat() if state.planned_finish else None,
        "duration_days": state.duration_days,
        "baseline_cost": str(state.baseline_cost) if state.baseline_cost is not None else None,
        "planned_resource_units": str(state.planned_resource_units)
        if state.planned_resource_units is not None
        else None,
        "activity_type": state.activity_type or None,
        "field_provenance": state.field_provenance or {},
    }


class BaselineListView(ProjectAccessMixin, View):
    """GET — list baseline versions for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = BaselineVersion.objects.filter(project=project).select_related(
            "source_version", "approved_by"
        )
        items, pagination = _paginate(request, qs, order_by="-created_at")
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "items": [_serialize_baseline(b) for b in items],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class BaselineDetailView(ProjectAccessMixin, View):
    """GET — baseline version detail."""

    def get(self, request, baseline_pk, **kwargs):
        project = self.get_project()
        baseline = get_object_or_404(
            BaselineVersion.objects.select_related("source_version", "approved_by"),
            pk=baseline_pk,
            project=project,
        )
        return JsonResponse(_serialize_baseline(baseline))

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class BaselineSelectedView(ProjectAccessMixin, View):
    """GET — currently selected baseline for analysis."""

    def get(self, request, **kwargs):
        project = self.get_project()
        baseline = BaselineVersionService.get_selected_baseline(project)
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "selected": _serialize_baseline(baseline) if baseline else None,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class BaselineComparisonView(ProjectAccessMixin, View):
    """GET — comparison summary vs current operational schedule."""

    def get(self, request, **kwargs):
        project = self.get_project()
        baseline_pk = request.GET.get("baseline_id")
        baseline = None
        if baseline_pk:
            baseline = get_object_or_404(
                BaselineVersion,
                pk=baseline_pk,
                project=project,
            )
        svc = BaselineComparisonService(project, baseline=baseline)
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "comparison": svc.summary(),
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class BaselineTaskStateListView(ProjectAccessMixin, View):
    """GET — paginated baseline task states."""

    def get(self, request, baseline_pk, **kwargs):
        project = self.get_project()
        baseline = get_object_or_404(BaselineVersion, pk=baseline_pk, project=project)
        qs = baseline.task_states.select_related("schedule_activity")
        items, pagination = _paginate(request, qs, order_by="activity_code")
        return JsonResponse(
            {
                "baseline_id": str(baseline.pk),
                "items": [_serialize_task_state(s) for s in items],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
