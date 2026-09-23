# scheduling/tests/test_evm_cost_hide_patch.py
"""Product-surface gate: company-cost EVM metrics stay unavailable."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.services.executive_controls.current_evm_analytics import (
    CurrentEVMAnalyticsService,
)
from scheduling.services.executive_controls.overview_filters import OverviewFilters
from scheduling.services.executive_controls.overview_service import (
    ExecutiveControlsOverviewService,
)
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    PRODUCT_MODE_LABEL,
)
from scheduling.tests.factories import (
    ResourceAssignmentFactory,
    ResourceFactory,
    TaskFactory,
)

_COMPANY_COST_IDS = ("e8.ac", "e8.cpi", "e8.eac", "e8.etc", "e8.vac", "e8.tcpi")


def _project_with_assignment_actual_cost():
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("200.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        status="complete",
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 15),
        is_non_physical=False,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("80.00"),
        planned_cost=Decimal("200.00"),
        is_pending=False,
    )
    return project


@pytest.mark.django_db
def test_assignment_ac_does_not_enable_company_cost_kpis():
    """Even with ResourceAssignment.actual_cost, product CPI/AC/EAC stay unavailable."""
    project = _project_with_assignment_actual_cost()
    payload = CurrentEVMAnalyticsService(project).build()

    assert payload["mode"] == "schedule_performance"
    assert payload["mode_label"] == PRODUCT_MODE_LABEL
    assert payload.get("cost_evm_available") is False
    assert payload.get("company_actual_cost_source_available") is False

    spi = payload["metrics"]["e8.spi"]
    assert spi["available"] is True
    assert spi["label"] == "Schedule Performance Indicator"
    assert "schedule/progress" in spi["caveat"].lower()

    for mid in _COMPANY_COST_IDS:
        m = payload["metrics"][mid]
        assert m["available"] is False, mid
        assert COMPANY_ACTUAL_COST_UNAVAILABLE in (m["missing_reason"] or "")
        assert mid in payload["unavailable_metrics"]

    assert "authoritative" not in str(payload["metrics"].get("e8.ac", {})).lower()
    blob = str(payload)
    assert "Monetary EVM" not in blob
    assert "Cost EVM" not in payload["mode_label"]


@pytest.mark.django_db
def test_overview_cost_section_hides_company_cost_values():
    """Overview cost section never lights up CPI/EAC/AC as available product KPIs."""
    project = _project_with_assignment_actual_cost()
    payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())

    assert payload["cost_evm_available"] is False
    assert payload["performance_mode_label"] == PRODUCT_MODE_LABEL
    by_id = {c["metric_id"]: c for c in payload["cards"]}
    assert by_id["e8.spi"]["available"] is True
    assert by_id["e8.spi"]["label"] == "Schedule Performance Indicator"
    assert by_id["e8.cpi"]["available"] is False
    assert by_id["e8.ac"]["available"] is False
    assert by_id["e8.eac"]["available"] is False
    assert COMPANY_ACTUAL_COST_UNAVAILABLE in by_id["e8.cpi"]["unavailable_reason"]


@pytest.mark.django_db
def test_evm_page_html_hides_cost_evm_and_company_cost_kpis(client):
    """EVM page chrome uses schedule readiness wording; no Cost EVM mode option."""
    project = _project_with_assignment_actual_cost()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert (
        "Schedule Performance &amp; Readiness" in html or "Schedule Performance & Readiness" in html
    )
    assert 'data-testid="exec-company-cost-unavailable"' in html
    assert COMPANY_ACTUAL_COST_UNAVAILABLE in html
    assert "not ERP, invoice, QS" in html or "no ERP, invoice, QS" in html
    assert ">Cost EVM<" not in html
    assert "Monetary EVM" not in html
    assert "authoritative actual cost" not in html.lower()
    assert "Loading cost position" not in html


@pytest.mark.django_db
def test_controls_overview_loading_copy(client):
    """Overview no longer says Loading cost position."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
    ).content.decode()
    assert "Loading cost position" not in html
    assert "Loading schedule and readiness indicators" in html


@pytest.mark.django_db
def test_cost_hx_section_wording(client):
    """HTMX cost section is schedule readiness, not cost position."""
    project = _project_with_assignment_actual_cost()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:executive_controls_overview_cost", kwargs={"pk": project.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Schedule Performance" in html
    assert "Cost Position" not in html
    assert COMPANY_ACTUAL_COST_UNAVAILABLE in html
    assert "not ERP, invoice, QS, or company actual spend" in html
