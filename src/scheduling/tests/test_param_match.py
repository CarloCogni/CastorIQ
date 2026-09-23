# scheduling/tests/test_param_match.py
"""Tests for IFC shared-parameter matching → TaskEntityBinding persistence."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.link_resolver import entity_gids_for_task
from scheduling.services.linker import param_match_tasks, persist_param_matches
from scheduling.tests.factories import TaskFactory


@pytest.mark.django_db
def test_persist_param_match_creates_trusted_binding():
    """Exact Activity ID match upserts TaskEntityBinding with needs_review=False."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A-1001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PARAM-001",
        properties={"Castor.Activity ID": "A-1001"},
    )

    matches = param_match_tasks([task], [entity], "Activity ID")
    persist_param_matches(matches, [entity])

    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=entity.global_id)
    assert binding.confidence == 1.0
    assert binding.link_method == TaskEntityBinding.LinkMethod.EXACT
    assert binding.needs_review is False
    assert entity_gids_for_task(task.pk) == [entity.global_id]
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_persist_param_match_idempotent():
    """Running parameter match persistence twice does not duplicate bindings."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="A-2002")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PARAM-002",
        properties={"Identity Data.Activity ID": "A-2002"},
    )
    matches = param_match_tasks([task], [entity], "Activity ID")

    persist_param_matches(matches, [entity])
    persist_param_matches(matches, [entity])

    assert (
        TaskEntityBinding.objects.filter(task=task, entity_global_id=entity.global_id).count() == 1
    )


@pytest.mark.django_db
def test_persist_param_match_does_not_remove_unrelated_bindings():
    """Unmatched tasks keep existing bindings when parameter match runs."""
    project = ProjectFactory()
    matched = TaskFactory(project=project, activity_code="A-3003")
    other = TaskFactory(project=project, activity_code="OTHER")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PARAM-003",
        properties={"Castor.Activity ID": "A-3003"},
    )
    TaskEntityBinding.objects.create(
        task=other,
        entity_global_id="GID-UNRELATED",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    matches = param_match_tasks([matched, other], [entity], "Activity ID")
    persist_param_matches(matches, [entity])

    assert TaskEntityBinding.objects.filter(
        task=matched, entity_global_id=entity.global_id
    ).exists()
    assert TaskEntityBinding.objects.filter(task=other, entity_global_id="GID-UNRELATED").exists()
    assert not TaskEntityBinding.objects.filter(
        task=other, entity_global_id=entity.global_id
    ).exists()


@pytest.mark.django_db
def test_link_param_view_post_blocked_until_approval(client):
    """LinkParamView POST is blocked — bindings require preview + approval (E1-E)."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="A-4004")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PARAM-004",
        properties={"Castor.Activity ID": "A-4004"},
    )
    client.force_login(user)

    url = reverse("scheduling:schedule_link_param", kwargs={"pk": project.pk})
    response = client.post(url, {"param_name": "Activity ID"})

    assert response.status_code == 400
    assert not TaskEntityBinding.objects.filter(task=task).exists()


@pytest.mark.django_db
def test_link_param_preview_shows_binding_without_m2m(client):
    """Preview reflects existing bindings even when legacy M2M is empty."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="BIND-ONLY-001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-OTHER",
        properties={"Castor.Activity ID": "NO-MATCH"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-BIND-ONLY",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    assert task.ifc_entities.count() == 0
    client.force_login(user)

    url = (
        reverse("scheduling:schedule_link_preview_param", kwargs={"pk": project.pk})
        + "?param_name=Activity+ID&format=json"
    )
    response = client.get(url)

    assert response.status_code == 200
    data = response.json()
    assert data["existing_accepted_bindings"] == 1
    assert data["matched_task_count"] == 0


@pytest.mark.django_db
def test_link_param_preview_shows_unlinked_task(client):
    """Preview reports zero matches for tasks without matching IFC Activity IDs."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="UNLINKED-001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-NOMATCH",
        properties={"Castor.Activity ID": "DIFFERENT"},
    )
    client.force_login(user)

    url = (
        reverse("scheduling:schedule_link_preview_param", kwargs={"pk": project.pk})
        + "?param_name=Activity+ID&format=json"
    )
    response = client.get(url)

    data = response.json()
    assert response.status_code == 200
    assert data["matched_task_count"] == 0
    assert data["schedule_only_activity_codes"] == 1
    assert not TaskEntityBinding.objects.filter(task__project=project).exists()
