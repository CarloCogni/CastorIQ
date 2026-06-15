# scheduling/views_executive_controls.py
"""E8 executive controls API and overview views."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin

logger = logging.getLogger(__name__)


def _capability_profile(project) -> dict[str, Any]:
    """Project analytics capability profile — cached per view call."""
    from scheduling.services.executive_controls.capability_profile import (
        ProjectAnalyticsCapabilityProfile,
    )

    return ProjectAnalyticsCapabilityProfile(project).build()


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
        capability = _capability_profile(project)
        ctx["project"] = project
        ctx["analytical_context"] = AnalyticalContextService(project).build(capability)
        ctx["capability_profile"] = capability
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


class ExecutiveControlsCapabilitiesView(ProjectAccessMixin, View):
    """GET — project analytics capability profile (read-only)."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        payload = _capability_profile(project)
        methodology_url = reverse(
            "scheduling:executive_controls_methodology",
            kwargs={"pk": project.pk},
        )
        payload["methodology_url"] = methodology_url
        return JsonResponse(payload)

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


class ExecutiveControlsEVMPageView(ProjectAccessMixin, TemplateView):
    """GET — E8-D EVM analytics shell."""

    template_name = "scheduling/executive_controls_evm_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        from scheduling.services.executive_controls.context import AnalyticalContextService

        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        capability = _capability_profile(project)
        filters = _evm_filters(self.request)
        ctx["project"] = project
        ctx["analytical_context"] = AnalyticalContextService(project).build(capability)
        ctx["capability_profile"] = capability
        ctx["filters"] = filters
        ctx["filter_query"] = filters.query_string()
        ctx["exec_subtab"] = "evm"
        return ctx

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsEVMCurrentView(ProjectAccessMixin, View):
    """GET — current-point EVM metrics fragment or JSON."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.analytical_snapshot.snapshot_evm_read import (
            PersistedSnapshotEVMReadService,
        )
        from scheduling.services.executive_controls.current_evm_analytics import (
            CurrentEVMAnalyticsService,
        )

        project = self.get_project()
        filters = _evm_filters(request)
        capability = _capability_profile(project)
        try:
            snapshot = _resolve_snapshot_for_e8_read(project, request)
            if snapshot is not None:
                payload = PersistedSnapshotEVMReadService(snapshot).build_current_payload()
            else:
                session = _evm_session(project)
                payload = CurrentEVMAnalyticsService(
                    project, capability_profile=capability, session=session
                ).build(mode=filters.mode)
            if request.headers.get("HX-Request"):
                return render(request, "scheduling/components/executive_evm_current.html", payload)
            return JsonResponse(payload)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=404)
        except Exception as exc:
            logger.exception("EVM current metrics failed: %s", exc)
            err = {"section_error": "Current metrics temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request, "scheduling/components/executive_evm_current.html", err, status=200
                )
            return JsonResponse({"error": str(exc)}, status=500)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsEVMSCureView(ProjectAccessMixin, View):
    """GET — derived as-of S-curve fragment or JSON."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.derived_asof_scurve import (
            DerivedAsOfSCurveService,
        )

        project = self.get_project()
        filters = _evm_filters(request)
        capability = _capability_profile(project)
        session = _evm_session(project)
        try:
            svc = DerivedAsOfSCurveService(project, capability_profile=capability, session=session)
            payload = svc.build_scurve(filters)
            if request.headers.get("HX-Request"):
                return render(request, "scheduling/components/executive_evm_scurve.html", payload)
            return JsonResponse(payload)
        except Exception as exc:
            logger.exception("EVM S-curve failed: %s", exc)
            err = {"section_error": "Derived curve temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request, "scheduling/components/executive_evm_scurve.html", err, status=200
                )
            return JsonResponse({"error": str(exc)}, status=500)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsEVMPeriodsView(ProjectAccessMixin, View):
    """GET — underlying period table fragment or JSON."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.derived_asof_scurve import (
            DerivedAsOfSCurveService,
        )

        project = self.get_project()
        filters = _evm_filters(request)
        capability = _capability_profile(project)
        session = _evm_session(project)
        try:
            svc = DerivedAsOfSCurveService(project, capability_profile=capability, session=session)
            payload = svc.build_periods(filters)
            if request.headers.get("HX-Request"):
                return render(request, "scheduling/components/executive_evm_periods.html", payload)
            return JsonResponse(payload)
        except Exception as exc:
            logger.exception("EVM periods failed: %s", exc)
            err = {"section_error": "Period table temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request, "scheduling/components/executive_evm_periods.html", err, status=200
                )
            return JsonResponse({"error": str(exc)}, status=500)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsEVMMethodologyView(ProjectAccessMixin, View):
    """GET — E8-D methodology and coverage panel."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.current_evm_analytics import (
            CurrentEVMAnalyticsService,
        )
        from scheduling.services.executive_controls.methodology import methodology_registry_payload

        project = self.get_project()
        filters = _evm_filters(request)
        capability = _capability_profile(project)
        session = _evm_session(project)
        current = CurrentEVMAnalyticsService(
            project, capability_profile=capability, session=session
        ).build(mode=filters.mode)
        payload = {
            "definitions": [
                d
                for d in methodology_registry_payload()
                if d["metric_id"].startswith("e8.")
                and (
                    "evm" in d.get("drilldown_route", "")
                    or "current" in d["metric_id"]
                    or "derived" in d["metric_id"]
                )
            ],
            "current": current,
            "capability_profile": capability,
        }
        if request.headers.get("HX-Request"):
            return render(request, "scheduling/components/executive_evm_methodology.html", payload)
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


def _matrix_filters(request) -> Any:
    from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters

    return ExecutiveMatrixFilters.from_params(request.GET.dict())


def _evm_filters(request) -> Any:
    from scheduling.services.executive_controls.evm_filters import EVMFilters

    return EVMFilters.from_params(request.GET.dict())


def _evm_session(project) -> Any:
    from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession

    return E8EVMComputeSession(str(project.pk))


def _resolve_snapshot_for_e8_read(project, request):
    """Resolve explicit snapshot read mode — default live returns None."""
    from django.shortcuts import get_object_or_404

    from scheduling.models import AnalyticalSnapshot
    from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService

    mode = (request.GET.get("analytical_mode") or "live").strip().lower()
    snapshot_id = request.GET.get("snapshot_id")
    if snapshot_id:
        snap = get_object_or_404(AnalyticalSnapshot, pk=snapshot_id, project=project)
        if snap.status not in (
            AnalyticalSnapshot.Status.COMPLETED,
            AnalyticalSnapshot.Status.PUBLISHED,
        ):
            raise ValueError("Snapshot read mode requires a completed or published snapshot.")
        return snap
    if mode == "latest_completed":
        return AnalyticalSnapshotService.get_latest_completed(project)
    if mode == "latest_published":
        return AnalyticalSnapshotService.get_latest_published(project)
    return None


def _hierarchy_context(project, request, filters=None):
    from scheduling.services.executive_controls.hierarchy_mode import HierarchyModeResolver

    override = None
    if filters is not None:
        override = filters.hierarchy_mode_override
    else:
        override = (request.GET.get("hierarchy_mode") or "").strip() or None
    force_stage = override == "stage_proxy"
    return HierarchyModeResolver(project).resolve(force_stage_proxy=force_stage)


def _build_matrix_rows(project, request, filters):
    from scheduling.services.executive_controls.hierarchy_mode import (
        CANONICAL_MODES,
        HierarchyMode,
    )
    from scheduling.services.executive_controls.performance_cube import (
        ProjectPerformanceCubeService,
    )
    from scheduling.services.executive_controls.wbs_analytics_session import WBSAnalyticsSession
    from scheduling.services.executive_controls.wbs_matrix import WBSMatrixService

    hierarchy = _hierarchy_context(project, request, filters)
    snapshot = _resolve_snapshot_for_e8_read(project, request)

    if hierarchy.hierarchy_mode in {m.value for m in CANONICAL_MODES}:
        session = WBSAnalyticsSession.load(project, hierarchy)
        if snapshot is not None:
            return WBSMatrixService(project, session).build_snapshot_unavailable(
                reason="Snapshot read mode — WBS node metrics are live-only in DF-C3.",
            )
        return WBSMatrixService(project, session).build_rows(filters)

    if hierarchy.hierarchy_mode == HierarchyMode.STAGE_PROXY.value:
        payload = ProjectPerformanceCubeService(project).build_rows(filters)
        payload["hierarchy"] = hierarchy.to_dict()
        return payload

    return {
        "section": "matrix_rows",
        "hierarchy": hierarchy.to_dict(),
        "rows": [],
        "summary": {"filtered_task_count": 0, "group_count": 0},
        "pagination": {
            "page": filters.page,
            "page_size": filters.page_size,
            "total": 0,
            "pages": 1,
        },
        "warnings": list(hierarchy.caveats),
        "unavailable_reason": "No usable canonical WBS or stage proxy hierarchy.",
    }


class ExecutiveControlsMatrixPageView(ProjectAccessMixin, TemplateView):
    """GET — hierarchical performance matrix shell."""

    template_name = "scheduling/executive_controls_matrix_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        from scheduling.services.executive_controls.context import AnalyticalContextService

        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        filters = _matrix_filters(self.request)
        capability = _capability_profile(project)
        ctx["project"] = project
        ctx["analytical_context"] = AnalyticalContextService(project).build(capability)
        ctx["capability_profile"] = capability
        ctx["filters"] = filters
        ctx["filter_query"] = filters.query_string()
        from scheduling.services.executive_controls.matrix_hierarchy_options import (
            MatrixHierarchyOptionsService,
        )

        matrix_options = MatrixHierarchyOptionsService(project).build(
            hierarchy_mode_override=filters.hierarchy_mode_override,
        )
        ctx["matrix_hierarchy_options"] = matrix_options
        ctx["available_dimensions"] = matrix_options["filter_dimensions"]
        ctx["hierarchy_context"] = matrix_options["hierarchy"]
        ctx["exec_subtab"] = "matrix"
        return ctx

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsMatrixRowsView(ProjectAccessMixin, View):
    """GET — paginated matrix rows fragment or JSON."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        filters = _matrix_filters(request)
        try:
            payload = _build_matrix_rows(project, request, filters)
            if request.headers.get("HX-Request"):
                return render(request, "scheduling/components/executive_matrix_rows.html", payload)
            return JsonResponse(payload)
        except Exception as exc:
            logger.exception("Matrix rows failed: %s", exc)
            err = {"section_error": "Matrix rows temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request, "scheduling/components/executive_matrix_rows.html", err, status=200
                )
            return JsonResponse({"error": str(exc)}, status=500)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsHierarchyContextView(ProjectAccessMixin, View):
    """GET — hierarchy mode and WBS context for E8 matrix."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        filters = _matrix_filters(request)
        return JsonResponse({"hierarchy": _hierarchy_context(project, request, filters).to_dict()})

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsWBSNodeView(ProjectAccessMixin, View):
    """GET — canonical WBS node analytics."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.hierarchy_mode import CANONICAL_MODES
        from scheduling.services.executive_controls.wbs_analytics_session import WBSAnalyticsSession
        from scheduling.services.executive_controls.wbs_drilldown import WBSDrilldownService

        project = self.get_project()
        filters = _matrix_filters(request)
        hierarchy = _hierarchy_context(project, request, filters)
        if hierarchy.hierarchy_mode not in {m.value for m in CANONICAL_MODES}:
            return JsonResponse({"error": "Canonical WBS not available."}, status=404)
        session = WBSAnalyticsSession.load(project, hierarchy)
        payload = WBSDrilldownService(project, session).node_detail(
            str(kwargs["node_pk"]),
            filters,
        )
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsWBSTasksView(ProjectAccessMixin, View):
    """GET — tasks for a WBS node or unassigned bucket."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.hierarchy_mode import CANONICAL_MODES
        from scheduling.services.executive_controls.wbs_analytics_session import WBSAnalyticsSession
        from scheduling.services.executive_controls.wbs_drilldown import WBSDrilldownService

        project = self.get_project()
        filters = _matrix_filters(request)
        hierarchy = _hierarchy_context(project, request, filters)
        if hierarchy.hierarchy_mode not in {m.value for m in CANONICAL_MODES}:
            return JsonResponse({"error": "Canonical WBS not available."}, status=404)
        session = WBSAnalyticsSession.load(project, hierarchy)
        payload = WBSDrilldownService(project, session).task_list(str(kwargs["node_pk"]), filters)
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsWBSModelScopeView(ProjectAccessMixin, View):
    """GET — trusted IFC model scope for a WBS node."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.executive_controls.hierarchy_mode import CANONICAL_MODES
        from scheduling.services.executive_controls.wbs_analytics_session import WBSAnalyticsSession
        from scheduling.services.executive_controls.wbs_drilldown import WBSDrilldownService

        project = self.get_project()
        filters = _matrix_filters(request)
        hierarchy = _hierarchy_context(project, request, filters)
        if hierarchy.hierarchy_mode not in {m.value for m in CANONICAL_MODES}:
            return JsonResponse({"error": "Canonical WBS not available."}, status=404)
        session = WBSAnalyticsSession.load(project, hierarchy)
        payload = WBSDrilldownService(project, session).model_scope(str(kwargs["node_pk"]), filters)
        return JsonResponse(payload)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsTradesPageView(ProjectAccessMixin, TemplateView):
    """GET — trade and package analysis page."""

    template_name = "scheduling/executive_controls_trades_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        from scheduling.services.executive_controls.context import AnalyticalContextService

        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        filters = _matrix_filters(self.request)
        capability = _capability_profile(project)
        ctx["project"] = project
        ctx["analytical_context"] = AnalyticalContextService(project).build(capability)
        ctx["capability_profile"] = capability
        ctx["filters"] = filters
        ctx["filter_query"] = filters.query_string()
        ctx["exec_subtab"] = "trades"
        return ctx

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsTradesAnalysisView(ProjectAccessMixin, View):
    """GET — trade/package analysis fragment or JSON."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.trade_package_analysis import (
            TradePackageAnalysisService,
        )

        project = self.get_project()
        filters = _matrix_filters(request)
        try:
            payload = TradePackageAnalysisService(project).build(filters)
            payload["project"] = project
            if request.headers.get("HX-Request"):
                return render(request, "scheduling/components/executive_trades_table.html", payload)
            return JsonResponse(payload)
        except Exception as exc:
            logger.exception("Trade analysis failed: %s", exc)
            err = {"section_error": "Trade analysis temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request, "scheduling/components/executive_trades_table.html", err, status=200
                )
            return JsonResponse({"error": str(exc)}, status=500)

    def post(self, request, **kwargs: object) -> JsonResponse:
        return JsonResponse({"error": "Method not allowed."}, status=405)


class ExecutiveControlsMatrixActivitiesView(ProjectAccessMixin, View):
    """GET — paginated activity drilldown for a matrix group."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.executive_controls.activity_drilldown import (
            ActivityDrilldownService,
        )

        project = self.get_project()
        filters = _matrix_filters(request)
        try:
            payload = ActivityDrilldownService(project).build(filters)
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "scheduling/components/executive_matrix_activity_drilldown.html",
                    payload,
                )
            return JsonResponse(payload)
        except Exception as exc:
            logger.exception("Activity drilldown failed: %s", exc)
            err = {"section_error": "Activity drilldown temporarily unavailable."}
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "scheduling/components/executive_matrix_activity_drilldown.html",
                    err,
                    status=200,
                )
            return JsonResponse({"error": str(exc)}, status=500)

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
