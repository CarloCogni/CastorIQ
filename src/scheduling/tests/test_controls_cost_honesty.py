# scheduling/tests/test_controls_cost_honesty.py
"""Company-cost honesty gate for Phase 3 Controls (main-compatible)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    COMPANY_COST_METRIC_IDS,
    PRODUCT_MODE_LABEL,
    company_actual_cost_source_available,
    gate_company_cost_metrics_unavailable,
)
from scheduling.tests.factories import TaskFactory


def test_company_actual_cost_source_always_unavailable():
    assert company_actual_cost_source_available() is False
    gated = gate_company_cost_metrics_unavailable()
    for mid in COMPANY_COST_METRIC_IDS:
        assert mid in gated
        assert COMPANY_ACTUAL_COST_UNAVAILABLE in gated[mid]
    assert PRODUCT_MODE_LABEL == "Schedule Performance & Readiness"


@pytest.mark.django_db
def test_hub_exposes_controls_and_demotes_evm(client):
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()

    assert 'data-testid="hub-schedule"' in html
    assert ">Schedule<" in html or "Schedule</a>" in html
    assert 'data-testid="hub-controls"' in html
    assert "Controls" in html
    assert 'data-testid="hub-schedule-health-advanced"' in html
    # Legacy EVM is demoted (hidden), not a primary hub claim.
    advanced = html.split('data-testid="hub-schedule-health-advanced"', 1)[1][:200]
    assert "d-none" in html.split('data-testid="hub-schedule-health-advanced"', 1)[0][-80:] or (
        'class="nav-item d-none"' in html and "Schedule Readiness" in advanced
    )
