# scheduling/views_executive_controls.py
"""Controls pages — readiness/performance (not company-cost EVM).

Phase 3 main-based port: overview workspace + Schedule Performance detail.
Does not depend on package foundation migrations (governance/baseline/WBS).
"""

from __future__ import annotations

import logging

from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin
from scheduling.services.executive_controls.controls_workspace import (
    build_controls_workspace,
)
from scheduling.services.executive_controls.main_controls_profile import (
    build_main_controls_profile,
)
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    COMPANY_COST_SOURCE_ABSENT_NOTE,
    PRODUCT_MODE_LABEL,
)

logger = logging.getLogger(__name__)


class ExecutiveControlsOverviewPageView(ProjectAccessMixin, TemplateView):
    """Controls overview — schedule / model / quantity readiness workspace."""

    template_name = "scheduling/executive_controls_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        capability, analytical = build_main_controls_profile(project)
        workspace = build_controls_workspace(
            project,
            analytical_context=analytical,
            capability_profile=capability,
        )
        ctx["project"] = project
        ctx["castor_subtab"] = "executive_controls"
        ctx["capability_profile"] = capability
        ctx["analytical_context"] = analytical
        ctx["workspace"] = workspace
        ctx["filters"] = {
            "stage": self.request.GET.get("stage", ""),
            "status": self.request.GET.get("status", ""),
            "day_type": self.request.GET.get("day_type", "working"),
            "linked_trusted": self.request.GET.get("linked_trusted") == "1",
        }
        ctx["filter_query"] = self.request.GET.urlencode()
        return ctx


class ExecutiveControlsEVMPageView(ProjectAccessMixin, TemplateView):
    """Schedule Performance & Readiness detail — SPI only; company cost unavailable."""

    template_name = "scheduling/executive_controls_evm_page.html"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        capability, analytical = build_main_controls_profile(project)

        spi_value: float | None = None
        spi_available = False
        try:
            from scheduling.services.evm import compute_evm

            result = compute_evm(str(project.pk))
            raw_spi = result.get("spi") if isinstance(result, dict) else None
            if raw_spi is not None:
                spi_value = float(raw_spi)
                spi_available = True
        except Exception as exc:  # noqa: BLE001 — never invent SPI
            logger.debug("Controls SPI compute skipped: %s", exc)

        ctx["project"] = project
        ctx["castor_subtab"] = "executive_controls"
        ctx["capability_profile"] = capability
        ctx["analytical_context"] = analytical
        ctx["mode_label"] = PRODUCT_MODE_LABEL
        ctx["company_cost_unavailable"] = COMPANY_ACTUAL_COST_UNAVAILABLE
        ctx["company_cost_note"] = COMPANY_COST_SOURCE_ABSENT_NOTE
        ctx["spi_available"] = spi_available
        ctx["spi_value"] = spi_value
        ctx["filters"] = {
            "granularity": self.request.GET.get("granularity", "weekly"),
            "mode": "schedule_performance",
        }
        return ctx
