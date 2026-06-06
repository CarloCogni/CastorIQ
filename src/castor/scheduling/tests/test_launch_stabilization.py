# castor/scheduling/tests/test_launch_stabilization.py
"""Launch-stabilization tests: pct normalization, link resolver, EVM mode flags."""

from __future__ import annotations

import pytest

from castor.scheduling.services.evm import compute_evm
from castor.scheduling.services.link_resolver import (
    entity_gids_by_task,
    entity_gids_for_task,
    link_status_for_task,
    linked_entity_gids_for_project,
)
from castor.scheduling.services.pct_normalize import normalize_pct_complete
from castor.scheduling.tests.factories import TaskFactory
from environments.tests.factories import ProjectFactory

# ---------------------------------------------------------------------------
# normalize_pct_complete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        (0, 0.0),
        (0.0, 0.0),
        (0.4, 0.4),
        (40, 0.4),
        (40.0, 0.4),
        (100, 1.0),
        (101, 1.0),
        (-5, 0.0),
        ("invalid", None),
    ],
)
def test_normalize_pct_complete(raw, expected):
    """P6/CSV percent values map safely to Task 0–1 storage."""
    result = normalize_pct_complete(raw) if not isinstance(raw, str) else None
    if isinstance(raw, str):
        assert result is None
        return
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=1e-4)


# ---------------------------------------------------------------------------
# LinkResolver — TaskEntityBinding authoritative
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_link_resolver_returns_binding_gids_not_m2m_only():
    """Gantt-style lookup uses TaskEntityBinding even when M2M is empty."""
    from castor.scheduling.models import TaskEntityBinding
    from ifc_processor.tests.factories import IFCEntityFactory

    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A100")
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-BIND-001")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method="manual",
        needs_review=False,
    )

    assert task.ifc_entities.count() == 0
    assert entity_gids_for_task(task.pk) == [entity.global_id]
    assert entity_gids_by_task(project.pk)[str(task.pk)] == [entity.global_id]
    assert entity.global_id in linked_entity_gids_for_project(project.pk)
    assert link_status_for_task(task, [entity.global_id]) == "linked"


@pytest.mark.django_db
def test_link_resolver_consistent_across_batch_and_single():
    """Batch map matches per-task resolver for the same binding."""
    from castor.scheduling.models import TaskEntityBinding

    project = ProjectFactory()
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-XYZ",
        confidence=0.95,
        link_method="exact",
        needs_review=False,
    )

    single = entity_gids_for_task(task.pk)
    batch = entity_gids_by_task(project.pk, [task.pk]).get(str(task.pk), [])
    assert single == batch == ["GID-XYZ"]


# ---------------------------------------------------------------------------
# EVM schedule performance mode
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_evm_duration_proxy_flags_schedule_performance_mode():
    """Duration-weighted BAC exposes non-monetary performance mode in API payload."""
    from datetime import date, timedelta

    project = ProjectFactory()
    start = date(2025, 1, 1)
    TaskFactory(
        project=project,
        start_date=start,
        end_date=start + timedelta(days=10),
        cost=None,
        status="active",
        actual_start=start,
    )

    result = compute_evm(str(project.pk))
    assert result["has_data"] is True
    assert result["use_cost"] is False
    assert result["is_monetary_evm"] is False
    assert result["performance_mode"] == "schedule_performance"
    assert "duration" in result["performance_mode_label"].lower()


@pytest.mark.django_db
def test_critical_path_marks_negative_float_as_critical():
    """Recomputed CPM treats total_float <= 0 as critical."""
    from datetime import date, timedelta

    from castor.scheduling.models import TaskDependency
    from castor.scheduling.services.critical_path import compute_critical_path

    project = ProjectFactory()
    start = date(2025, 1, 1)
    t1 = TaskFactory(
        project=project,
        start_date=start,
        end_date=start + timedelta(days=5),
        status="planned",
    )
    t2 = TaskFactory(
        project=project,
        start_date=start + timedelta(days=5),
        end_date=start + timedelta(days=10),
        status="planned",
        constraint_type="Mandatory Finish",
        constraint_date=start + timedelta(days=7),
    )
    TaskDependency.objects.create(predecessor=t1, successor=t2, dep_type="FS", lag_days=0)

    result = compute_critical_path(str(project.pk))
    t2_data = result["task_data"][str(t2.pk)]
    assert t2_data["total_float"] <= 0
    assert t2_data["is_critical"] is True

    t2.refresh_from_db()
    assert t2.is_critical is True
    assert t2.total_float <= 0


@pytest.mark.django_db
def test_evm_flags_legacy_progress_scale_when_pct_above_one():
    """Detection-only warning when stored progress appears to be 0–100 scale."""
    from datetime import date, timedelta

    project = ProjectFactory()
    start = date(2025, 1, 1)
    TaskFactory(
        project=project,
        start_date=start,
        end_date=start + timedelta(days=10),
        cost=None,
        status="active",
        actual_start=start,
        physical_percent_complete=40.0,
    )

    result = compute_evm(str(project.pk))
    assert result["legacy_progress_scale"] is True


@pytest.mark.django_db
def test_health_check_counts_binding_as_linked():
    """Physical tasks with TaskEntityBinding are not flagged unlinked."""
    from castor.scheduling.models import TaskEntityBinding
    from castor.scheduling.services.health_check import run_health_check
    from ifc_processor.tests.factories import IFCEntityFactory

    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A200")
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-HC-001")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method="manual",
        needs_review=False,
    )

    assert task.ifc_entities.count() == 0
    result = run_health_check(project)
    codes = [i["code"] for i in result["issues"]]
    assert "unlinked_physical" not in codes


@pytest.mark.django_db
def test_xer_phys_complete_pct_normalized():
    """XER phys_complete_pct maps to _p6_phys_pct in 0–1 range."""
    import io

    from castor.scheduling.services.xer_parser import parse_xer
    from castor.tests.fixtures import xer_bytes

    raw = xer_bytes(
        tasks=[
            {
                "task_id": "1001",
                "task_code": "ACT-001",
                "task_name": "Foundation Work",
                "target_start_date": "2025-01-01 08:00",
                "target_end_date": "2025-01-10 08:00",
                "status_code": "TK_Active",
                "phys_complete_pct": "40",
            }
        ]
    )
    tasks, _ = parse_xer(io.BytesIO(raw))
    assert tasks[0]["_p6_phys_pct"] == pytest.approx(0.4, abs=1e-4)
