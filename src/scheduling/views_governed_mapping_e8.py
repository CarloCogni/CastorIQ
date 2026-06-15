# scheduling/views_governed_mapping_e8.py
"""E8 governed dimension mapping APIs — read-only (DF-D3)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import AnalyticalDimension
from scheduling.services.executive_controls.dimension_mode import DimensionModeService
from scheduling.services.executive_controls.governed_mapping_aggregation import (
    GovernedMappingAggregationService,
)
from scheduling.services.executive_controls.governed_mapping_drilldown import (
    GovernedMappingDrilldownService,
)

logger = logging.getLogger(__name__)


def _page_params(request) -> tuple[int, int]:
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    try:
        page_size = min(50, max(1, int(request.GET.get("page_size", "50"))))
    except ValueError:
        page_size = 50
    return page, page_size


def _requested_mode(request) -> str | None:
    mode = request.GET.get("mode")
    return mode if mode else None


class ExecutiveGovernedDimensionsListView(ProjectAccessMixin, View):
    """GET — list governed dimension mode contracts for E8."""

    def get(self, request, **kwargs):
        project = self.get_project()
        ctx = DimensionModeService(project).build()
        dims = []
        for key in ("trade", "package"):
            mode = ctx.dimensions.get(key)
            if mode:
                dims.append(mode.to_dict())
        custom = (
            AnalyticalDimension.objects.filter(
                project=project,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            )
            .exclude(dimension_type__in=["trade", "package"])
            .order_by("dimension_key")
        )
        for dim in custom:
            dims.append(DimensionModeService(project).get_mode(dim.dimension_key).to_dict())
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "dimensions": dims,
                "cutover_caveats": list(ctx.cutover_caveats),
                "snapshot_governed_mapping_analytics": "unavailable",
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ExecutiveGovernedDimensionSummaryView(ProjectAccessMixin, View):
    """GET — governed dimension summary."""

    def get(self, request, dimension_key, **kwargs):
        project = self.get_project()
        if not self._dimension_in_project(project, dimension_key):
            return JsonResponse({"error": "Dimension not found."}, status=404)
        payload = GovernedMappingAggregationService(project).build_summary(
            dimension_key,
            requested_mode=_requested_mode(request),
        )
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])

    def _dimension_in_project(self, project, dimension_key: str) -> bool:
        if dimension_key in ("trade", "package"):
            return True
        return AnalyticalDimension.objects.filter(
            project=project, dimension_key=dimension_key
        ).exists()


class ExecutiveGovernedDimensionValuesView(ProjectAccessMixin, View):
    """GET — governed dimension value rows."""

    def get(self, request, dimension_key, **kwargs):
        project = self.get_project()
        page, page_size = _page_params(request)
        payload = GovernedMappingAggregationService(project).build_values(
            dimension_key,
            page=page,
            page_size=page_size,
            requested_mode=_requested_mode(request),
        )
        if payload.get("error"):
            return JsonResponse(payload, status=404)
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ExecutiveGovernedDimensionValueTasksView(ProjectAccessMixin, View):
    """GET — tasks for a governed dimension value."""

    def get(self, request, dimension_key, value_id, **kwargs):
        project = self.get_project()
        page, page_size = _page_params(request)
        payload = GovernedMappingDrilldownService(project).tasks_for_value(
            dimension_key,
            value_id,
            page=page,
            page_size=page_size,
            requested_mode=_requested_mode(request),
        )
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ExecutiveGovernedDimensionUnmappedTasksView(ProjectAccessMixin, View):
    """GET — unmapped tasks for dimension."""

    def get(self, request, dimension_key, **kwargs):
        project = self.get_project()
        page, page_size = _page_params(request)
        payload = GovernedMappingDrilldownService(project).unmapped_tasks(
            dimension_key, page=page, page_size=page_size
        )
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ExecutiveGovernedDimensionConflictsView(ProjectAccessMixin, View):
    """GET — conflict bucket for dimension."""

    def get(self, request, dimension_key, **kwargs):
        project = self.get_project()
        page, page_size = _page_params(request)
        payload = GovernedMappingDrilldownService(project).conflicts(
            dimension_key, page=page, page_size=page_size
        )
        if payload.get("error"):
            return JsonResponse(payload, status=404)
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
