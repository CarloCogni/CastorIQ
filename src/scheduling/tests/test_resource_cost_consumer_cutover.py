# scheduling/tests/test_resource_cost_consumer_cutover.py
"""DF-E4: cashflow / workforce / coverage / evm_availability prefer canonical RA."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from environments.tests.factories import ProjectFactory
from scheduling.models import P6ResourceAssignment
from scheduling.services.cashflow import compute_cashflow
from scheduling.services.executive_controls.coverage import AnalyticalCoverageService
from scheduling.services.executive_controls.evm_availability import E8EVMAvailabilityService
from scheduling.services.executive_controls.methodology import E8_METRIC_REGISTRY
from scheduling.services.executive_controls.resource_availability import (
    EquivalentWorkforceAvailabilityService,
)
from scheduling.services.resource_foundation import (
    COST_SOURCE_CANONICAL,
    COST_SOURCE_P6_FALLBACK,
)
from scheduling.tests.factories import ResourceAssignmentFactory, ResourceFactory, TaskFactory


def _p6_ra(project, task, **kwargs) -> P6ResourceAssignment:
    defaults = {
        "project": project,
        "task": task,
        "p6_activity_object_id": "A1",
        "p6_resource_object_id": "R1",
        "resource_type": "Labor",
        "planned_cost": Decimal("100.00"),
        "actual_cost": Decimal("40.00"),
        "remaining_cost": Decimal("60.00"),
        "planned_units": Decimal("10.0"),
        "actual_units": Decimal("4.0"),
        "is_pending": False,
    }
    defaults.update(kwargs)
    return P6ResourceAssignment.objects.create(**defaults)


@pytest.mark.django_db
def test_cashflow_prefers_canonical_when_present():
    """Cashflow sums canonical costs even when P6 differs."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 20),
        status="complete",
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_cost=Decimal("100.00"),
        actual_cost=Decimal("80.00"),
        remaining_cost=Decimal("0.00"),
        is_pending=False,
    )
    _p6_ra(project, task, actual_cost=Decimal("999.00"), planned_cost=Decimal("999.00"))

    result = compute_cashflow(str(project.pk))

    assert result["has_data"] is True
    assert result["source"] == COST_SOURCE_CANONICAL
    assert result["metrics"]["ac"] == 80.0
    assert result["metrics"]["bac"] == 100.0


@pytest.mark.django_db
def test_cashflow_falls_back_to_p6_when_canonical_absent():
    """P6 path used only when project has zero canonical assignments."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        actual_start=date(2025, 1, 1),
        status="active",
    )
    _p6_ra(project, task, actual_cost=Decimal("40.00"), planned_cost=Decimal("100.00"))

    result = compute_cashflow(str(project.pk))

    assert result["source"] == COST_SOURCE_P6_FALLBACK
    assert result["metrics"]["ac"] == 40.0
    assert result["metrics"]["bac"] == 100.0


@pytest.mark.django_db
def test_cashflow_does_not_double_count_when_both_exist():
    """When both stores exist, only canonical contributes."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        actual_start=date(2025, 1, 1),
        status="active",
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_cost=Decimal("50.00"),
        actual_cost=Decimal("25.00"),
        remaining_cost=Decimal("25.00"),
        is_pending=False,
    )
    _p6_ra(
        project,
        task,
        planned_cost=Decimal("50.00"),
        actual_cost=Decimal("25.00"),
        remaining_cost=Decimal("25.00"),
    )

    result = compute_cashflow(str(project.pk))

    assert result["source"] == COST_SOURCE_CANONICAL
    assert result["metrics"]["ac"] == 25.0
    assert result["metrics"]["bac"] == 50.0


@pytest.mark.django_db
def test_workforce_prefers_canonical_labor_units():
    """Equivalent workforce sums canonical labor units when present."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project, resource_type="labor"),
        planned_units=Decimal("20.0"),
        actual_units=Decimal("8.0"),
        is_pending=False,
    )
    _p6_ra(
        project,
        task,
        planned_units=Decimal("999.0"),
        actual_units=Decimal("999.0"),
    )

    payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()

    assert payload["units_source"] == COST_SOURCE_CANONICAL
    assert payload["planned_manhours"] == 20.0
    assert payload["actual_manhours"] == 8.0
    assert "Full workforce curves deferred to E8-E." in payload["caveats"]


@pytest.mark.django_db
def test_workforce_falls_back_to_p6_when_canonical_absent():
    """Workforce uses P6 labor units when no canonical rows exist."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _p6_ra(project, task, planned_units=Decimal("12.0"), actual_units=Decimal("3.0"))

    payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()

    assert payload["units_source"] == COST_SOURCE_P6_FALLBACK
    assert payload["planned_manhours"] == 12.0
    assert payload["actual_manhours"] == 3.0
    assert any("legacy" in c.lower() for c in payload["caveats"])


@pytest.mark.django_db
def test_coverage_prefers_canonical_counts():
    """Coverage cost/labor counts use canonical when present."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project, resource_type="labor"),
        planned_cost=Decimal("100.00"),
        actual_cost=Decimal("40.00"),
        planned_units=Decimal("10.0"),
        actual_units=Decimal("4.0"),
        is_pending=False,
    )
    # Divergent P6 row must not inflate coverage.
    _p6_ra(
        project,
        task,
        planned_cost=Decimal("1.00"),
        actual_cost=Decimal("1.00"),
        planned_units=Decimal("1.0"),
        actual_units=Decimal("1.0"),
    )
    # Extra P6-only row would double-count if both stores were queried.
    _p6_ra(
        project,
        TaskFactory(project=project),
        p6_activity_object_id="A2",
        p6_resource_object_id="R2",
        planned_cost=Decimal("5.00"),
        actual_cost=Decimal("5.00"),
    )

    payload = AnalyticalCoverageService(str(project.pk)).build()

    assert payload["resource_assignment_source"] == COST_SOURCE_CANONICAL
    actual_item = next(i for i in payload["cost"] if i["metric_id"] == "e8.actual_cost_coverage")
    assert actual_item["numerator"] == 1


@pytest.mark.django_db
def test_evm_availability_prefers_canonical_ac_row_count():
    """EVM availability ra_count and ac_source align with canonical store."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("200.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        status="complete",
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 15),
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("80.00"),
        planned_cost=Decimal("200.00"),
        is_pending=False,
    )
    _p6_ra(project, task, actual_cost=Decimal("999.00"))

    payload = E8EVMAvailabilityService(str(project.pk)).build()

    assert payload["ac_source"] == COST_SOURCE_CANONICAL
    assert payload["coverage"]["resource_actual_cost_source"] == COST_SOURCE_CANONICAL
    assert payload["coverage"]["resource_actual_cost_rows"] == 1
    assert payload["evm_snapshot"]["ac"] == 80.0


@pytest.mark.django_db
def test_methodology_and_help_no_longer_p6_only():
    """Manhour methodology sources mention canonical preference."""
    for mid in ("e8.planned_manhours", "e8.actual_manhours", "e8.remaining_manhours"):
        src = E8_METRIC_REGISTRY[mid].primary_source
        assert "ResourceAssignment" in src
        assert src != "P6ResourceAssignment"

    from pathlib import Path

    help_html = Path(__file__).resolve().parents[1] / (
        "templates/scheduling/components/evm_help_modal.html"
    )
    text = help_html.read_text(encoding="utf-8")
    assert "canonical" in text.lower()
    assert "P6ResourceAssignment.actual_cost</code></strong>" not in text
    assert "legacy" in text.lower() or "fallback" in text.lower()
