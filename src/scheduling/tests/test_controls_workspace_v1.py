# scheduling/tests/test_controls_workspace_v1.py
"""Controls Workspace V1 — layout, wording, and company-cost compliance."""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.services.executive_controls.controls_workspace import (
    build_controls_workspace,
)
from scheduling.services.executive_controls.main_controls_profile import (
    build_main_controls_profile,
)
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    company_actual_cost_source_available,
)
from scheduling.tests.factories import TaskFactory

_FORBIDDEN_PRIMARY = (
    "EVM Dashboard",
    "Cost EVM",
    "Monetary EVM",
    "Trusted-linked",
    "trusted-binding-v1",
    "Cost-weighted schedule progress",
    "Data and Methodology Coverage",
    "e8-v1",
)

_FORBIDDEN_AVAILABLE_COST = (
    ">CPI<",
    "Actual Cost</",
    ">EAC<",
    ">ETC<",
    ">VAC<",
    ">TCPI<",
)


def _workspace_shell_html(html: str) -> str:
    """Primary Controls workspace chrome (excludes demoted details / global chrome)."""
    start = html.find('data-testid="controls-workspace-shell"')
    end = html.find('data-testid="controls-advanced-details"')
    if start == -1:
        return html
    if end == -1 or end < start:
        return html[start:]
    return html[start:end]


@pytest.mark.django_db
def test_controls_workspace_markers(client):
    """Overview renders command bar, stats, rail, grid, and inspector."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="controls-command-bar"' in html
    assert 'data-testid="controls-stats-strip"' in html
    assert 'data-testid="controls-left-rail"' in html
    assert 'data-testid="controls-readiness-grid"' in html
    assert 'data-testid="controls-selected-inspector"' in html
    assert 'data-testid="controls-workspace-subtitle"' in html
    assert "Schedule / Model / Quantity Readiness" in html


@pytest.mark.django_db
def test_controls_workspace_supported_signals(client):
    """Supported readiness signals and company-cost unavailable are visible."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
    ).content.decode()

    assert 'data-testid="controls-stat-spi"' in html
    assert (
        "Schedule Performance Indicator" in html
        or 'data-testid="controls-row-schedule-performance"' in html
    )
    assert 'data-testid="controls-row-link-coverage"' in html
    assert "Link Coverage" in html
    assert 'data-testid="controls-row-model-readiness"' in html
    assert 'data-testid="controls-row-quantity-readiness"' in html
    assert 'data-testid="controls-open-quantities"' in html
    assert 'data-testid="controls-company-cost-unavailable"' in html
    assert COMPANY_ACTUAL_COST_UNAVAILABLE in html
    assert "ResourceAssignment.actual_cost is not company spend" in html


@pytest.mark.django_db
def test_controls_workspace_compliance_no_available_cost_kpis(client):
    """Primary Controls chrome does not advertise available company-cost KPIs."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
    ).content.decode()
    first = _workspace_shell_html(html)

    assert "Company actual cost" not in html
    assert "Monetary EVM" not in first
    assert ">Cost EVM<" not in first
    for token in _FORBIDDEN_AVAILABLE_COST:
        assert token not in first


@pytest.mark.django_db
def test_controls_workspace_wording_primary_chrome(client):
    """Workspace shell avoids trusted/authority/methodology report chrome."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
    ).content.decode()
    first = _workspace_shell_html(html).lower()

    for phrase in _FORBIDDEN_PRIMARY:
        assert phrase.lower() not in first, phrase
    assert "governance" not in first
    assert "authoritative" not in first
    assert "trusted-linked" not in first


@pytest.mark.django_db
def test_controls_workspace_builder_rows():
    """Presentation builder returns readiness rows without inventing cost metrics."""
    project = ProjectFactory()
    TaskFactory(project=project)
    capability, analytical = build_main_controls_profile(project)
    workspace = build_controls_workspace(
        project,
        analytical_context=analytical,
        capability_profile=capability,
    )

    ids = {r["id"] for r in workspace["rows"]}
    assert "schedule-performance" in ids
    assert "link-coverage" in ids
    assert "model-readiness" in ids
    assert "quantity-readiness" in ids
    assert "company-cost" in ids
    company = next(r for r in workspace["rows"] if r["id"] == "company-cost")
    assert company["status"] == "Unavailable"
    assert "CPI" not in company["value"]
    blob = str(workspace)
    assert "Monetary EVM" not in blob
    assert "Cost EVM" not in blob
    assert company_actual_cost_source_available() is False


@pytest.mark.django_db
def test_schedule_performance_detail_page(client):
    """Schedule Performance detail stays schedule-framed with company cost unavailable."""
    project = ProjectFactory()
    TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        physical_percent_complete=40.0,
    )
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert (
        "Schedule Performance &amp; Readiness" in html or "Schedule Performance & Readiness" in html
    )
    assert 'data-testid="exec-company-cost-unavailable"' in html
    assert COMPANY_ACTUAL_COST_UNAVAILABLE in html
    assert ">Cost EVM<" not in html
    assert "Monetary EVM" not in html
    assert "EVM Dashboard" not in html
