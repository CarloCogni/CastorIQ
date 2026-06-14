# scheduling/views_executive_controls.py
"""E8 executive controls API and overview views."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin

logger = logging.getLogger(__name__)


def _overview_filters(request) -> Any:
    from scheduling.services.executive_controls.overview_filters import OverviewFilters

    return OverviewFilters.from_params(request.GET.dict())


def _section_view(
    request,
    project,
    *,
    build_fn: Callable,
    template: str,
) -> HttpResponse:
    """Render section fragment or JSON; isolate failures."""
    filters = _overview_filters(request)
    try:
        from scheduling.services.executive_controls.overview_service import (
            ExecutiveControlsOverviewService,
        )

        svc = ExecutiveControlsOverviewService(project)
        payload = build_fn(svc, filters)
        if request.headers.get("HX-Request"):
            return render(request, template, payload)
        return JsonResponse(payload)
    except Exception as exc:
        logger.exception("Executive controls section failed: %s", exc)
        err_ctx = {"section_error": "This section is temporarily unavailable."}
        if request.headers.get("HX-Request"):
            return render(request, template, err_ctx, status=200)
        return JsonResponse({"error": str(exc), "section": template}, status=500)


class ExecutiveControlsOverviewPageView(ProjectAccessMixin, TemplateView):
    """GET — executive overview shell with progressive HTMX sections."""

    template_name = "scheduling/executive_controls_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        from scheduling.services.executive_controls.context import AnalyticalContextService
        from scheduling.services.executive_controls.overview_filters import OverviewFilters

        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        filters = OverviewFilters.from_params(self.request.GET.dict())
        ctx["project"] = project
        ctx["analytical_context"] = AnalyticalContextService(project).build()
        ctx["filters"] = filters
        ctx["filter_query"] = filters.query_string()
        return ctx

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewJSONView(ProjectAccessMixin, View):
    """GET — full overview JSON (shell metadata only by default)."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.overview_service import (
            ExecutiveControlsOverviewService,
        )

        project = self.get_project()
        filters = _overview_filters(request)
        payload = ExecutiveControlsOverviewService(project).build_shell(filters)
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewScheduleView(ProjectAccessMixin, View):
    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        return _section_view(
            request,
            project,
            build_fn=lambda s, f: s.build_schedule_section(f),
            template="scheduling/components/executive_section_schedule.html",
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewCostView(ProjectAccessMixin, View):
    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        return _section_view(
            request,
            project,
            build_fn=lambda s, f: s.build_cost_section(f),
            template="scheduling/components/executive_section_cost.html",
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewDelaysView(ProjectAccessMixin, View):
    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        return _section_view(
            request,
            project,
            build_fn=lambda s, f: s.build_delays_section(f),
            template="scheduling/components/executive_section_delays.html",
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewModelView(ProjectAccessMixin, View):
    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        return _section_view(
            request,
            project,
            build_fn=lambda s, f: s.build_model_impact_section(f),
            template="scheduling/components/executive_section_model_impact.html",
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsOverviewCoverageView(ProjectAccessMixin, View):
    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        return _section_view(
            request,
            project,
            build_fn=lambda s, f: s.build_coverage_section(f),
            template="scheduling/components/executive_section_coverage.html",
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsContextView(ProjectAccessMixin, View):
    """GET — analytical context and source identity."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.context import AnalyticalContextService

        project = self.get_project()
        payload = AnalyticalContextService(project).build()
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsMethodologyView(ProjectAccessMixin, View):
    """GET — e8-v1 methodology registry."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.methodology import (
            E8_METHODOLOGY_VERSION,
            methodology_registry_payload,
        )

        project = self.get_project()
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "methodology_version": E8_METHODOLOGY_VERSION,
                "definitions": methodology_registry_payload(),
            }
        )

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsCoverageView(ProjectAccessMixin, View):
    """GET — analytical coverage contracts."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.coverage import AnalyticalCoverageService

        project = self.get_project()
        payload = AnalyticalCoverageService(str(project.pk)).build()
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsDelaySummaryView(ProjectAccessMixin, View):
    """GET — delay type counts summary."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.delays import (
            DelayFilters,
            ExecutiveDelayService,
        )

        project = self.get_project()
        filters = DelayFilters.from_params(request.GET.dict())
        payload = ExecutiveDelayService(str(project.pk)).build_summary(filters)
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsDelayDetailView(ProjectAccessMixin, View):
    """GET — paginated delay classification detail."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.delays import (
            DelayFilters,
            ExecutiveDelayService,
        )

        project = self.get_project()
        filters = DelayFilters.from_params(request.GET.dict())
        payload = ExecutiveDelayService(str(project.pk)).build_detail(filters)
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsEVMAvailabilityView(ProjectAccessMixin, View):
    """GET — EVM mode and metric availability."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.evm_availability import E8EVMAvailabilityService

        project = self.get_project()
        payload = E8EVMAvailabilityService(str(project.pk)).build()
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsResourceAvailabilityView(ProjectAccessMixin, View):
    """GET — equivalent workforce availability contract."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.resource_availability import (
            EquivalentWorkforceAvailabilityService,
        )

        project = self.get_project()
        payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsFoundationView(ProjectAccessMixin, View):
    """Minimal E8-A diagnostic surface — not the executive dashboard."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.context import AnalyticalContextService
        from scheduling.services.executive_controls.coverage import AnalyticalCoverageService
        from scheduling.services.executive_controls.delays import ExecutiveDelayService
        from scheduling.services.executive_controls.evm_availability import E8EVMAvailabilityService
        from scheduling.services.executive_controls.resource_availability import (
            EquivalentWorkforceAvailabilityService,
        )

        project = self.get_project()
        pid = str(project.pk)
        context = {
            "project": project,
            "analytical_context": AnalyticalContextService(project).build(),
            "coverage": AnalyticalCoverageService(pid).build(),
            "delay_summary": ExecutiveDelayService(pid).build_summary(),
            "evm_availability": E8EVMAvailabilityService(pid).build(),
            "resource_availability": EquivalentWorkforceAvailabilityService(pid).build(),
        }
        return render(request, "scheduling/tabs/e8_foundation.html", context)
