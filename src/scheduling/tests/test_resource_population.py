# scheduling/tests/test_resource_population.py
"""DF-E2 P6 → canonical resource population tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from environments.tests.factories import ProjectFactory
from scheduling.models import (
    P6ResourceAssignment,
    Resource,
    ResourceAssignment,
    ScheduleSourceVersion,
)
from scheduling.services.evm import compute_evm
from scheduling.services.resource_population import ResourceFoundationPopulationService
from scheduling.tests.factories import TaskFactory


def _p6_ra(project, task, **kwargs) -> P6ResourceAssignment:
    defaults = {
        "project": project,
        "task": task,
        "p6_activity_object_id": "ACT-1",
        "p6_resource_object_id": "RES-100",
        "resource_type": "Labor",
        "planned_cost": Decimal("100.00"),
        "actual_cost": Decimal("40.00"),
        "remaining_cost": Decimal("60.00"),
        "at_completion_cost": Decimal("100.00"),
        "planned_units": Decimal("10.0000"),
        "actual_units": Decimal("4.0000"),
        "is_pending": False,
    }
    defaults.update(kwargs)
    return P6ResourceAssignment.objects.create(**defaults)


@pytest.mark.django_db
def test_dry_run_creates_no_rows():
    """Dry-run reports work but writes nothing."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _p6_ra(project, task)

    result = ResourceFoundationPopulationService(project).run(dry_run=True, apply=False)

    assert result.dry_run is True
    assert result.p6_rows_found == 1
    assert result.assignments_created == 1
    assert Resource.objects.filter(project=project).count() == 0
    assert ResourceAssignment.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_apply_creates_resource_and_assignment():
    """Apply persists Resource + ResourceAssignment with provenance."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    p6 = _p6_ra(project, task)

    result = ResourceFoundationPopulationService(project).run(dry_run=False, apply=True)

    assert result.dry_run is False
    assert result.resources_created == 1
    assert result.assignments_created == 1

    resource = Resource.objects.get(project=project)
    assert resource.source_system == "p6"
    assert resource.external_id == "RES-100"
    assert resource.resource_type == Resource.ResourceType.LABOR
    assert resource.name == "P6 Resource RES-100"

    ra = ResourceAssignment.objects.get(project=project)
    assert ra.task_id == task.pk
    assert ra.resource_id == resource.pk
    assert ra.source_system == "p6"
    assert ra.external_id == str(p6.pk)
    assert ra.p6_resource_object_id == "RES-100"
    assert ra.planned_cost == Decimal("100.00")
    assert ra.actual_cost == Decimal("40.00")
    assert ra.remaining_units is None
    assert ra.at_completion_units is None
    assert ra.is_pending is False


@pytest.mark.django_db
def test_apply_is_idempotent():
    """Second apply updates in place — no duplicate assignments."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    p6 = _p6_ra(project, task, actual_cost=Decimal("40.00"))

    svc = ResourceFoundationPopulationService(project)
    first = svc.run(dry_run=False, apply=True)
    p6.actual_cost = Decimal("55.00")
    p6.save(update_fields=["actual_cost", "updated_at"])
    second = svc.run(dry_run=False, apply=True)

    assert first.assignments_created == 1
    assert second.assignments_created == 0
    assert second.assignments_updated == 1
    assert Resource.objects.filter(project=project).count() == 1
    assert ResourceAssignment.objects.filter(project=project).count() == 1
    assert ResourceAssignment.objects.get(project=project).actual_cost == Decimal("55.00")


@pytest.mark.django_db
def test_explicit_zero_preserved_from_p6():
    """Explicit P6 zeros are stored as Decimal(0), not NULL."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _p6_ra(
        project,
        task,
        planned_cost=Decimal("0.00"),
        actual_cost=Decimal("0.00"),
        planned_units=Decimal("0.0000"),
        actual_units=Decimal("0.0000"),
    )

    ResourceFoundationPopulationService(project).run(dry_run=False, apply=True)
    ra = ResourceAssignment.objects.get(project=project)

    assert ra.planned_cost == Decimal("0.00")
    assert ra.actual_cost == Decimal("0.00")
    assert ra.planned_units == Decimal("0.0000")


@pytest.mark.django_db
def test_orphan_resource_not_collapsed():
    """Blank P6 resource ObjectId yields per-assignment unknown resources."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    a = _p6_ra(
        project,
        task,
        p6_resource_object_id="",
        resource_type="Labor",
        p6_activity_object_id="A1",
    )
    b = _p6_ra(
        project,
        task,
        p6_resource_object_id="",
        resource_type="Labor",
        p6_activity_object_id="A2",
        planned_cost=Decimal("5.00"),
    )

    ResourceFoundationPopulationService(project).run(dry_run=False, apply=True)

    assert Resource.objects.filter(project=project).count() == 2
    assert ResourceAssignment.objects.filter(project=project).count() == 2
    ext_ids = set(Resource.objects.filter(project=project).values_list("external_id", flat=True))
    assert ext_ids == {f"p6-orphan:{a.pk}", f"p6-orphan:{b.pk}"}


@pytest.mark.django_db
def test_skip_null_task():
    """Rows without task FK are skipped."""
    project = ProjectFactory()
    P6ResourceAssignment.objects.create(
        project=project,
        task=None,
        p6_activity_object_id="ORPHAN-ACT",
        p6_resource_object_id="RES-9",
        resource_type="Material",
        is_pending=False,
    )

    result = ResourceFoundationPopulationService(project).run(dry_run=False, apply=True)

    assert result.skipped == 1
    assert result.assignments_created == 0
    assert ResourceAssignment.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_source_version_attached_when_provided():
    """Explicit --source-version attaches ScheduleSourceVersion."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _p6_ra(project, task)
    sv = ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_filename="pilot.xml",
        imported_at=timezone.now(),
        status=ScheduleSourceVersion.Status.CURRENT,
    )

    ResourceFoundationPopulationService(project).run(
        dry_run=False,
        apply=True,
        source_version_id=str(sv.pk),
    )
    ra = ResourceAssignment.objects.get(project=project)
    assert ra.source_version_id == sv.pk


@pytest.mark.django_db
def test_evm_unchanged_after_canonical_population():
    """Canonical population must not enable EVM AC (still P6-path only)."""
    import datetime

    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("1000.00"),
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 1, 31),
    )
    # Confirmed P6 row with actual cost — this DOES enable AC via legacy path.
    _p6_ra(project, task, actual_cost=Decimal("200.00"))

    before = compute_evm(str(project.pk))
    ResourceFoundationPopulationService(project).run(dry_run=False, apply=True)
    after = compute_evm(str(project.pk))

    assert before.get("ac_available") is True
    assert after.get("ac_available") is True
    assert after.get("ac") == before.get("ac")
    assert ResourceAssignment.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_management_command_dry_run_default(capsys):
    """Management command defaults to dry-run."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _p6_ra(project, task)

    call_command("populate_resource_foundation", f"--project={project.pk}")

    assert Resource.objects.filter(project=project).count() == 0
    out = capsys.readouterr().out
    assert "dry_run" in out
    assert "true" in out.lower() or '"dry_run": true' in out
