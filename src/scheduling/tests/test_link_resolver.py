# scheduling/tests/test_link_resolver.py
"""Tests for binding-first Task ↔ IFC link resolution."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.health_check import run_health_check
from scheduling.services.link_resolver import (
    entity_gids_by_task,
    entity_gids_for_task,
    link_status_for_task,
    linked_entity_gids_for_project,
    task_is_linked,
)
from scheduling.tests.factories import TaskFactory


@pytest.mark.django_db
def test_link_resolver_returns_binding_gids_not_m2m_only():
    """Gantt-style lookup uses TaskEntityBinding even when M2M is empty."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A100")
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-BIND-001")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    assert task.ifc_entities.count() == 0
    assert entity_gids_for_task(task.pk) == [entity.global_id]
    assert entity_gids_by_task(project.pk)[str(task.pk)] == [entity.global_id]
    assert entity.global_id in linked_entity_gids_for_project(project.pk)
    assert task_is_linked(task.pk)
    assert link_status_for_task(task, [entity.global_id]) == "linked"


@pytest.mark.django_db
def test_link_resolver_batch_matches_single():
    """Batch map matches per-task resolver for the same binding."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-XYZ",
        confidence=0.95,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )

    single = entity_gids_for_task(task.pk)
    batch = entity_gids_by_task(project.pk, [task.pk]).get(str(task.pk), [])
    assert single == batch == ["GID-XYZ"]


@pytest.mark.django_db
def test_link_status_returns_needs_review_for_review_only_bindings():
    """Review-only bindings are not reported as fully trusted linked."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-REVIEW",
        confidence=0.7,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )

    gids = entity_gids_for_task(task.pk)
    assert gids == ["GID-REVIEW"]
    assert link_status_for_task(task, gids) == "needs_review"
    assert task_is_linked(task.pk, gids)


@pytest.mark.django_db
def test_link_status_returns_linked_when_accepted_binding_exists():
    """Accepted bindings return linked even when review bindings also exist."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-REVIEW",
        confidence=0.7,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-ACCEPTED",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    gids = entity_gids_for_task(task.pk)
    assert link_status_for_task(task, gids) == "linked"


@pytest.mark.django_db
def test_gantt_data_reports_review_only_task_as_needs_review(client):
    """GanttDataView distinguishes review-only bindings from trusted linked."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, is_non_physical=False)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-GANTT-001",
        confidence=0.88,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )
    client.force_login(user)

    url = reverse("scheduling:gantt_data", kwargs={"pk": project.pk})
    response = client.get(url)
    assert response.status_code == 200
    payload = json.loads(response.content)
    row = next(t for t in payload["tasks"] if t["id"] == str(task.pk))
    assert row["link_status"] == "needs_review"
    assert row["entity_global_ids"] == ["GID-GANTT-001"]


@pytest.mark.django_db
def test_gantt_data_reports_accepted_binding_as_linked(client):
    """GanttDataView reports trusted accepted bindings as linked."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, is_non_physical=False)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-GANTT-TRUSTED",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )
    client.force_login(user)

    url = reverse("scheduling:gantt_data", kwargs={"pk": project.pk})
    response = client.get(url)
    payload = json.loads(response.content)
    row = next(t for t in payload["tasks"] if t["id"] == str(task.pk))
    assert row["link_status"] == "linked"


@pytest.mark.django_db
def test_health_check_counts_binding_as_linked():
    """Physical tasks with TaskEntityBinding are not flagged unlinked."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A200")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-HC-001",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    assert task.ifc_entities.count() == 0
    result = run_health_check(project)
    codes = [issue["code"] for issue in result["issues"]]
    assert "unlinked_physical" not in codes


@pytest.mark.django_db
def test_task_list_shows_binding_link_count(client):
    """Task list partial shows binding count when M2M is empty."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, name="Bound Task")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-LIST-001",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    client.force_login(user)

    url = reverse("scheduling:task_list_partial", kwargs={"pk": project.pk})
    response = client.get(url)
    assert response.status_code == 200
    assert 'badge bg-primary">1</span>' in response.content.decode()
