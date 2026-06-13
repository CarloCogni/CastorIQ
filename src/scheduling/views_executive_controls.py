# scheduling/views_executive_controls.py
"""E8-A read-only executive controls API and diagnostic views."""

from __future__ import annotations

import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View

from core.mixins import ProjectAccessMixin

logger = logging.getLogger(__name__)


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
