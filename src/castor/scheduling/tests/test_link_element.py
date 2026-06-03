# castor/scheduling/tests/test_link_element.py
"""Tests for the manual element link/unlink API endpoints."""

from __future__ import annotations

import json

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from castor.scheduling.models import TaskEntityBinding
from castor.scheduling.tests.factories import TaskFactory


@pytest.mark.django_db
def test_link_element_creates_binding(client):
    """POST link-element creates a TaskEntityBinding and returns 201."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project)
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/tasks/{task.pk}/link-element/"
    r = client.post(
        url,
        data=json.dumps({"global_id": "19iO7YoRr7ze7jVuR7Kvet"}),
        content_type="application/json",
    )

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "linked"
    assert "binding_id" in data
    assert TaskEntityBinding.objects.filter(
        task=task, entity_global_id="19iO7YoRr7ze7jVuR7Kvet"
    ).exists()


@pytest.mark.django_db
def test_link_element_idempotent(client):
    """Calling link-element twice returns 200 on the second call, not an error."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project)
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/tasks/{task.pk}/link-element/"
    payload = json.dumps({"global_id": "19iO7YoRr7ze7jVuR7Kvet"})

    r1 = client.post(url, data=payload, content_type="application/json")
    r2 = client.post(url, data=payload, content_type="application/json")

    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.json()["status"] == "linked"
    # Still only one binding row
    assert (
        TaskEntityBinding.objects.filter(
            task=task, entity_global_id="19iO7YoRr7ze7jVuR7Kvet"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_unlink_element_removes_binding(client):
    """POST unlink-element deletes the binding and returns 200."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="19iO7YoRr7ze7jVuR7Kvet",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/tasks/{task.pk}/unlink-element/"
    r = client.post(
        url,
        data=json.dumps({"global_id": "19iO7YoRr7ze7jVuR7Kvet"}),
        content_type="application/json",
    )

    assert r.status_code == 200
    assert r.json()["status"] == "unlinked"
    assert not TaskEntityBinding.objects.filter(
        task=task, entity_global_id="19iO7YoRr7ze7jVuR7Kvet"
    ).exists()


@pytest.mark.django_db
def test_unlink_all_removes_all_bindings(client):
    """POST unlink-all deletes all bindings for a globalId across tasks and returns deleted count."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task_a = TaskFactory(project=project)
    task_b = TaskFactory(project=project)
    global_id = "19iO7YoRr7ze7jVuR7Kvet"
    TaskEntityBinding.objects.create(
        task=task_a,
        entity_global_id=global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )
    TaskEntityBinding.objects.create(
        task=task_b,
        entity_global_id=global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/elements/unlink-all/"
    r = client.post(
        url,
        data=json.dumps({"global_id": global_id}),
        content_type="application/json",
    )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "unlinked"
    assert data["deleted"] == 2
    assert not TaskEntityBinding.objects.filter(entity_global_id=global_id).exists()


@pytest.mark.django_db
def test_unlink_element_404_if_not_found(client):
    """POST unlink-element returns 404 when no binding exists."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project)
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/tasks/{task.pk}/unlink-element/"
    r = client.post(
        url,
        data=json.dumps({"global_id": "NONEXISTENT_GLOBAL_ID"}),
        content_type="application/json",
    )

    assert r.status_code == 404
    assert "error" in r.json()
