# scheduling/tests/test_resource_foundation.py
"""DF-E1 Resource Foundation schema and helper tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from environments.tests.factories import ProjectFactory
from scheduling.models import Resource, ResourceAssignment, ScheduleSourceVersion
from scheduling.services.evm import compute_evm
from scheduling.services.resource_foundation import (
    create_resource_assignment,
    sum_actual_cost_by_task,
    sum_planned_cost_by_task,
)
from scheduling.tests.factories import (
    ResourceAssignmentFactory,
    ResourceFactory,
    TaskFactory,
)


@pytest.mark.django_db
def test_resource_creation_defaults():
    """Resource stores type/status and leaves optional rate NULL."""
    project = ProjectFactory()
    resource = ResourceFactory(project=project, default_rate=None)

    assert resource.pk is not None
    assert resource.resource_type == Resource.ResourceType.LABOR
    assert resource.status == Resource.Status.ACTIVE
    assert resource.default_rate is None


@pytest.mark.django_db
def test_resource_code_unique_per_project_when_present():
    """Non-blank resource_code is unique within a project."""
    project = ProjectFactory()
    ResourceFactory(project=project, resource_code="LAB-01")
    with pytest.raises(IntegrityError):
        ResourceFactory(project=project, resource_code="LAB-01")


@pytest.mark.django_db
def test_resource_assignment_null_cost_and_units_by_default():
    """Missing cost/unit fields remain NULL — not coerced to zero."""
    assignment = ResourceAssignmentFactory(
        planned_cost=None,
        actual_cost=None,
        planned_units=None,
        actual_units=None,
        remaining_cost=None,
        remaining_units=None,
        at_completion_cost=None,
        at_completion_units=None,
    )
    assignment.refresh_from_db()

    assert assignment.planned_cost is None
    assert assignment.actual_cost is None
    assert assignment.planned_units is None
    assert assignment.actual_units is None
    assert assignment.remaining_cost is None
    assert assignment.remaining_units is None
    assert assignment.at_completion_cost is None
    assert assignment.at_completion_units is None


@pytest.mark.django_db
def test_resource_assignment_explicit_zero_preserved():
    """Explicit zero is stored and distinct from NULL."""
    assignment = ResourceAssignmentFactory(
        planned_cost=Decimal("0.00"),
        actual_cost=Decimal("0.00"),
        planned_units=Decimal("0.0000"),
    )
    assignment.refresh_from_db()

    assert assignment.planned_cost == Decimal("0.00")
    assert assignment.actual_cost == Decimal("0.00")
    assert assignment.planned_units == Decimal("0.0000")
    assert assignment.remaining_cost is None


@pytest.mark.django_db
def test_assignment_rejects_cross_project_task():
    """Task project must match assignment.project."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    task = TaskFactory(project=project_b)
    resource = ResourceFactory(project=project_a)

    with pytest.raises(ValidationError):
        create_resource_assignment(
            project=project_a,
            task=task,
            resource=resource,
            planned_cost=Decimal("10.00"),
        )


@pytest.mark.django_db
def test_assignment_rejects_cross_project_resource():
    """Resource project must match assignment.project."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    task = TaskFactory(project=project_a)
    resource = ResourceFactory(project=project_b)

    with pytest.raises(ValidationError):
        create_resource_assignment(
            project=project_a,
            task=task,
            resource=resource,
        )


@pytest.mark.django_db
def test_duplicate_source_identity_rejected_when_external_id_present():
    """project+source_version+source_system+external_id is unique when set."""
    from django.utils import timezone

    project = ProjectFactory()
    task = TaskFactory(project=project)
    resource = ResourceFactory(project=project)
    source_version = ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_filename="pilot.xml",
        imported_at=timezone.now(),
        status=ScheduleSourceVersion.Status.CURRENT,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=resource,
        source_version=source_version,
        source_system="p6xml",
        external_id="RA-100",
    )
    with pytest.raises(IntegrityError):
        ResourceAssignmentFactory(
            project=project,
            task=task,
            resource=resource,
            source_version=source_version,
            source_system="p6xml",
            external_id="RA-100",
        )


@pytest.mark.django_db
def test_sum_helpers_ignore_null_and_sum_values():
    """Query helpers ignore NULL costs and sum only present values."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    resource = ResourceFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=resource,
        planned_cost=Decimal("100.00"),
        actual_cost=None,
        is_pending=False,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project, resource_code="R-B"),
        planned_cost=None,
        actual_cost=Decimal("40.00"),
        is_pending=False,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project, resource_code="R-C"),
        planned_cost=Decimal("0.00"),
        actual_cost=Decimal("10.00"),
        is_pending=False,
    )

    planned = sum_planned_cost_by_task(project.pk, [task.pk])
    actual = sum_actual_cost_by_task(project.pk, [task.pk])

    assert planned[str(task.pk)] == Decimal("100.00")
    assert actual[str(task.pk)] == Decimal("50.00")


@pytest.mark.django_db
def test_canonical_assignments_enable_evm_ac():
    """DF-E3: canonical ResourceAssignment actual_cost > 0 enables EVM AC."""
    import datetime

    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("1000.00"),
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 1, 31),
        status="complete",
        actual_start=datetime.date(2025, 1, 1),
        actual_end=datetime.date(2025, 1, 20),
    )
    resource = ResourceFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=resource,
        actual_cost=Decimal("500.00"),
        planned_cost=Decimal("1000.00"),
        is_pending=False,
        status=ResourceAssignment.Status.ACTIVE,
    )

    result = compute_evm(str(project.pk))

    assert result.get("ac_available") is True
    assert result.get("ac") == 500.0
    assert result.get("ac_source") == "canonical_resource_assignment"
