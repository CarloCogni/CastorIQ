# scheduling/tests/test_reliability_a_double_prime.py
"""Reliability Patch A″ — stop hidden fetches, EVM cache, Link Proposals pagination."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession
from scheduling.services.executive_controls.evm_result_cache import (
    EVM_RESULT_CACHE_TTL_SECONDS,
    get_or_compute_evm,
)
from scheduling.tests.factories import TaskFactory


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_schedule_health_hidden_sections_not_auto_fetched(client):
    """Hidden value-cleanup sections must not be auto-called on initial JS."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=evm")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="value-cleanup-hide-monte-carlo"' in html
    assert 'data-testid="value-cleanup-hide-ml"' in html
    assert 'data-testid="value-cleanup-hide-cashflow"' in html
    assert 'data-testid="value-cleanup-hide-chat"' in html
    assert 'data-testid="value-cleanup-hide-wbs-proxy"' in html
    assert 'data-testid="value-cleanup-hide-delay-rootcause"' in html
    assert 'data-testid="value-cleanup-hide-anomaly"' in html

    assert "function _sectionAllowsAutoLoad" in html
    # Unconditional auto-calls for hidden loaders must be gone.
    assert "\n        loadCashflow();\n" not in html
    assert "\n        loadML();\n" not in html
    assert "\n        loadAnomalies();\n" not in html
    assert "\n        loadDelayRootCause();\n" not in html
    assert "\n        loadIntelligence();\n" not in html
    assert "\n    loadWbsHeatmap();\n" not in html
    assert "\n    loadTrendAnalysis();\n" not in html
    # Gated forms remain.
    assert '_sectionAllowsAutoLoad("evm-cf-section")' in html
    assert '_sectionAllowsAutoLoad("evm-ml-section")' in html
    assert '_sectionAllowsAutoLoad("evm-wbs-section")' in html
    # Core Schedule Health still auto-loads.
    assert "loadEvm();" in html
    assert "loadDcmaCheck();" in html


@pytest.mark.django_db
def test_executive_evm_defers_scurve_and_periods(client):
    """First EVM view auto-loads current + methodology only — not curve/periods."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="exec-evm-current-autoload"' in html
    assert 'data-testid="exec-evm-methodology-autoload"' in html
    assert 'data-testid="exec-evm-scurve-lazy"' in html
    assert 'data-testid="exec-evm-periods-lazy"' in html
    assert 'data-testid="exec-evm-load-scurve"' in html
    assert 'data-testid="exec-evm-load-periods"' in html
    assert "Load derived S-curve" in html
    # S-curve / periods must not auto hx-trigger=load on first paint.
    assert 'id="exec-evm-scurve"' in html
    scurve_block = html.split('id="exec-evm-scurve"', 1)[1].split('id="exec-evm-periods"', 1)[0]
    assert 'hx-trigger="load"' not in scurve_block
    periods_block = html.split('id="exec-evm-periods"', 1)[1].split('id="exec-evm-methodology"', 1)[
        0
    ]
    assert 'hx-trigger="load"' not in periods_block
    # Controls honesty: schedule/progress indicators only, company cost gated off.
    assert "Schedule and progress indicators only — not financial Cost EVM." in html
    assert 'data-testid="exec-company-cost-unavailable"' in html
    assert (
        "Unavailable — requires a company cost source (ERP / invoice / QS / payroll / procurement)."
        in html
    )
    assert "ResourceAssignment.actual_cost is not company spend." in html


@pytest.mark.django_db
def test_evm_result_cache_dedupes_compute_evm():
    """Parallel sessions share one compute_evm within the short TTL."""
    from scheduling.services.utils import get_project_data_date

    project = ProjectFactory()
    TaskFactory(project=project)
    data_date, _ = get_project_data_date(str(project.pk))
    sentinel = {"has_data": True, "cached": True, "performance_mode": "schedule_performance"}

    with patch("scheduling.services.evm.compute_evm", return_value=sentinel) as mock_compute:
        a = get_or_compute_evm(str(project.pk), as_of_date=data_date)
        b = get_or_compute_evm(str(project.pk), as_of_date=data_date)
        c = E8EVMComputeSession(str(project.pk)).evm()

    assert a == sentinel
    assert b == sentinel
    assert c == sentinel
    assert mock_compute.call_count == 1
    assert EVM_RESULT_CACHE_TTL_SECONDS >= 30


@pytest.mark.django_db
def test_link_proposals_paginated_default_limit(client):
    """Link Proposals renders at most the default page size of bindings."""
    project = ProjectFactory()
    client.force_login(project.owner)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-PAGE")

    for i in range(12):
        task = TaskFactory(project=project, name=f"Task {i:03d}")
        TaskEntityBinding.objects.create(
            task=task,
            entity_global_id=entity.global_id,
            confidence=0.9,
            link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
            needs_review=True,
            governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        )

    response = client.get(
        reverse("scheduling:review", kwargs={"pk": project.pk}),
        {"filter": "all", "limit": "5", "page": "1"},
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="link-review-pagination"' in html
    assert 'data-testid="link-review-showing"' in html
    assert "Showing 1–5" in html
    assert "of 12 bindings" in html
    assert html.count('id="binding-row-') == 5
    assert "Next" in html


@pytest.mark.django_db
def test_link_proposals_all_does_not_embed_unlinked_side_list(client):
    """filter=all no longer dumps every unlinked task into the HTML."""
    project = ProjectFactory()
    client.force_login(project.owner)
    linked = TaskFactory(project=project, name="Linked Task")
    TaskFactory(project=project, name="Unlinked Alpha Unique")
    TaskFactory(project=project, name="Unlinked Beta Unique")
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-LINKED")
    TaskEntityBinding.objects.create(
        task=linked,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
    )

    response = client.get(
        reverse("scheduling:review", kwargs={"pk": project.pk}),
        {"filter": "all"},
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Unlinked Alpha Unique" not in html
    assert "Unlinked Beta Unique" not in html
    assert "Linked Task" in html
