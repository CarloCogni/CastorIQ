# scheduling/tests/test_evm_ac_cutover.py
"""DF-E3: EVM AC prefers canonical ResourceAssignment with P6 fallback."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from environments.tests.factories import ProjectFactory
from scheduling.models import P6ResourceAssignment
from scheduling.services.evm import (
    AC_SOURCE_CANONICAL,
    AC_SOURCE_NONE,
    AC_SOURCE_P6_FALLBACK,
    _load_actual_costs,
    compute_evm,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.tests.factories import ResourceAssignmentFactory, ResourceFactory, TaskFactory


@pytest.mark.django_db
def test_load_actual_costs_prefers_canonical_when_present():
    """Canonical rows win even when P6 has different actual cost."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    resource = ResourceFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=resource,
        actual_cost=Decimal("100.00"),
        is_pending=False,
    )
    P6ResourceAssignment.objects.create(
        project=project,
        task=task,
        p6_activity_object_id="A1",
        p6_resource_object_id="R1",
        resource_type="Labor",
        actual_cost=Decimal("999.00"),
        is_pending=False,
    )

    loaded = _load_actual_costs([str(task.pk)], project_id=str(project.pk))

    assert loaded.source == AC_SOURCE_CANONICAL
    assert loaded.by_task[str(task.pk)] == 100.0


@pytest.mark.django_db
def test_load_actual_costs_falls_back_to_p6_when_canonical_absent():
    """P6 path used only when project has zero canonical assignments."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    P6ResourceAssignment.objects.create(
        project=project,
        task=task,
        p6_activity_object_id="A1",
        p6_resource_object_id="R1",
        resource_type="Labor",
        actual_cost=Decimal("40.00"),
        is_pending=False,
    )

    loaded = _load_actual_costs([str(task.pk)], project_id=str(project.pk))

    assert loaded.source == AC_SOURCE_P6_FALLBACK
    assert loaded.by_task[str(task.pk)] == 40.0


@pytest.mark.django_db
def test_load_actual_costs_does_not_double_count():
    """When both stores exist, only canonical contributes."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("25.00"),
        is_pending=False,
    )
    P6ResourceAssignment.objects.create(
        project=project,
        task=task,
        p6_activity_object_id="A1",
        p6_resource_object_id="R1",
        actual_cost=Decimal("25.00"),
        is_pending=False,
    )

    loaded = _load_actual_costs([str(task.pk)], project_id=str(project.pk))

    assert loaded.source == AC_SOURCE_CANONICAL
    assert loaded.by_task[str(task.pk)] == 25.0


@pytest.mark.django_db
def test_zero_and_null_actual_cost_ignored_for_ac():
    """actual_cost 0 / NULL do not contribute to AC map."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    resource = ResourceFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=resource,
        actual_cost=Decimal("0.00"),
        is_pending=False,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project, resource_code="R2"),
        actual_cost=None,
        is_pending=False,
    )

    loaded = _load_actual_costs([str(task.pk)], project_id=str(project.pk))

    assert loaded.source == AC_SOURCE_CANONICAL
    assert loaded.by_task == {}


@pytest.mark.django_db
def test_ac_unavailable_when_neither_store_has_positive_actual():
    """No positive actual cost → ac_available false."""
    project = ProjectFactory()
    TaskFactory(
        project=project,
        cost=Decimal("100.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        status="complete",
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 10),
    )

    result = compute_evm(str(project.pk), as_of_date=date(2025, 2, 1))

    assert result["ac_available"] is False
    assert result["ac"] is None
    assert result["ac_source"] in {AC_SOURCE_NONE, AC_SOURCE_P6_FALLBACK, AC_SOURCE_CANONICAL}


@pytest.mark.django_db
def test_compute_evm_canonical_numeric_parity_with_p6_seed():
    """Canonical-only AC equals the seeded actual cost."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("200.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 20),
        status="complete",
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 15),
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("80.00"),
        is_pending=False,
    )

    result = compute_evm(str(project.pk), as_of_date=date(2025, 2, 1))

    assert result["ac_source"] == AC_SOURCE_CANONICAL
    assert result["ac_available"] is True
    assert result["ac"] == 80.0


@pytest.mark.django_db
def test_capability_ac_source_prefers_canonical():
    """Capability CPI source labels canonical when ResourceAssignment exists."""
    project = ProjectFactory()
    task = TaskFactory(project=project, cost=Decimal("100.00"))
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("50.00"),
        is_pending=False,
    )

    profile = ProjectAnalyticsCapabilityProfile(project).build()
    cpi = profile["capabilities"][FeatureId.CURRENT_CPI.value]

    assert "ResourceAssignment.actual_cost" in cpi["source"]
    assert "P6ResourceAssignment.actual_cost" not in cpi["source"]


@pytest.mark.django_db
def test_capability_ac_source_fallback_label_when_only_p6():
    """Capability labels P6 fallback when canonical rows are absent."""
    project = ProjectFactory()
    task = TaskFactory(project=project, cost=Decimal("100.00"))
    P6ResourceAssignment.objects.create(
        project=project,
        task=task,
        p6_activity_object_id="A1",
        p6_resource_object_id="R1",
        actual_cost=Decimal("50.00"),
        is_pending=False,
    )

    profile = ProjectAnalyticsCapabilityProfile(project).build()
    cpi = profile["capabilities"][FeatureId.CURRENT_CPI.value]

    assert cpi["source"] == "P6ResourceAssignment.actual_cost"
    assert any("fallback" in c.lower() for c in cpi.get("caveats", []))
