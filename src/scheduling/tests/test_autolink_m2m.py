# scheduling/tests/test_autolink_m2m.py
"""Tests for AutoLink legacy M2M cleanup on re-run."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.autolink import run_autolink
from scheduling.tests.factories import TaskFactory


@pytest.fixture
def mock_schedule_context():
    """Skip LLM schedule-context analysis during autolink tests."""
    with patch(
        "scheduling.services.autolink.analyse_schedule_context",
        return_value={},
    ):
        yield


@pytest.mark.django_db
def test_autolink_rerun_clears_stale_m2m_for_physical_tasks(mock_schedule_context):
    """Re-run removes legacy M2M links when bindings are replaced and no match is found."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="NO-MATCH-CODE")
    stale_entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-STALE-001")
    matched_entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-OTHER-001",
        properties={"Castor.Activity ID": "DIFFERENT"},
    )
    task.ifc_entities.add(stale_entity, matched_entity)
    assert task.ifc_entities.count() == 2

    summary = run_autolink(project, ifc_param_name="Activity ID")

    assert summary["linked_exact"] == 0
    assert task.ifc_entities.count() == 0
    assert not TaskEntityBinding.objects.filter(task=task).exists()


@pytest.mark.django_db
def test_autolink_exact_match_syncs_m2m_for_accepted_bindings(mock_schedule_context):
    """Auto-accepted exact matches still populate legacy M2M for backward compatibility."""
    project = ProjectFactory()
    code = "MATCH-1001"
    task = TaskFactory(project=project, activity_code=code)
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-MATCH-1001",
        properties={"Castor.Activity ID": code},
    )

    summary = run_autolink(project, ifc_param_name="Activity ID")

    assert summary["linked_exact"] == 1
    assert task.ifc_entities.filter(pk=entity.pk).exists()
    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=entity.global_id)
    assert binding.needs_review is False


@pytest.mark.django_db
def test_autolink_does_not_clear_m2m_for_other_projects(mock_schedule_context):
    """M2M cleanup is scoped to the project being auto-linked."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    task_a = TaskFactory(project=project_a, activity_code="A-ONLY")
    task_b = TaskFactory(project=project_b, activity_code="B-KEEP")
    entity_b = IFCEntityFactory(ifc_file__project=project_b, global_id="GID-B-KEEP")
    task_b.ifc_entities.add(entity_b)
    IFCEntityFactory(
        ifc_file__project=project_a,
        global_id="GID-A-OTHER",
        properties={"Castor.Activity ID": "OTHER"},
    )

    run_autolink(project_a, ifc_param_name="Activity ID")

    assert task_a.ifc_entities.count() == 0
    assert task_b.ifc_entities.filter(pk=entity_b.pk).exists()


@pytest.mark.django_db
def test_autolink_does_not_clear_non_physical_task_m2m(mock_schedule_context):
    """Non-physical tasks are outside autolink replacement scope and keep legacy M2M."""
    project = ProjectFactory()
    physical = TaskFactory(project=project, activity_code="PHY-001", is_non_physical=False)
    non_physical = TaskFactory(
        project=project,
        name="Project Management",
        activity_code="NP-001",
        is_non_physical=True,
        non_physical_locked=True,
    )
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-NP-001")
    non_physical.ifc_entities.add(entity)
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PHY-OTHER",
        properties={"Castor.Activity ID": "OTHER"},
    )

    run_autolink(project, ifc_param_name="Activity ID")

    assert physical.ifc_entities.count() == 0
    assert non_physical.ifc_entities.filter(pk=entity.pk).exists()
    assert not TaskEntityBinding.objects.filter(task=non_physical).exists()


@pytest.mark.django_db
def test_autolink_needs_review_binding_does_not_restore_stale_m2m(mock_schedule_context):
    """Heuristic review bindings clear stale M2M and do not write M2M until accepted."""
    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        name="Install wall",
        activity_code="HEUR-001",
    )
    stale_entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-STALE-WALL")
    wall_entity = IFCEntityFactory(
        ifc_file__project=project,
        ifc_type="IfcWall",
        global_id="GID-WALL-001",
        properties={"Castor.Activity ID": "UNRELATED"},
    )
    task.ifc_entities.add(stale_entity)

    summary = run_autolink(project, ifc_param_name="Activity ID")

    assert summary["linked_heuristic"] == 1
    assert summary["needs_review"] == 1
    assert task.ifc_entities.count() == 0
    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=wall_entity.global_id)
    assert binding.needs_review is True
