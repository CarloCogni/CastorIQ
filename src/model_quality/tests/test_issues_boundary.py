# model_quality/tests/test_issues_boundary.py
"""Tests for IFC Issues property audits aligned with trusted 4D/5D bindings."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from model_quality.views import (
    _issues_property_gap_counts,
    _missing_activity_rows,
    _missing_cost_rows,
)
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory


@pytest.mark.django_db
def test_s1_excludes_accepted_binding_without_activity_id_property():
    """Accepted binding without IFC Activity ID property is not a missing 4D property gap."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-S1-ACCEPT", properties={})
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    rows = _missing_activity_rows(ifc_file, project)
    assert rows == []


@pytest.mark.django_db
def test_s1_includes_review_only_binding_without_activity_id_property():
    """Review-only bindings do not suppress missing IFC Activity ID property rows."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-S1-REVIEW", properties={})
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.7,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )

    rows = _missing_activity_rows(ifc_file, project)
    assert len(rows) == 1
    assert rows[0]["global_id"] == entity.global_id


@pytest.mark.django_db
def test_s1_includes_unbound_entity_without_activity_id_property():
    """Entities with no property and no accepted binding remain in S1."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-S1-UNBOUND", properties={})

    rows = _missing_activity_rows(ifc_file, project)
    assert len(rows) == 1
    assert rows[0]["global_id"] == entity.global_id


@pytest.mark.django_db
def test_s2_excludes_accepted_binding_with_schedule_cost():
    """Accepted binding to a task with schedule cost suppresses missing IFC cost rows."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-S2-COST", properties={})
    task = TaskFactory(project=project, cost=Decimal("12000.00"))
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    rows = _missing_cost_rows(ifc_file, project)
    assert rows == []


@pytest.mark.django_db
def test_issues_count_matches_filtered_s1_and_s2_rows(client):
    """IssuesCountView badge total matches S1 + S2 filtered row counts."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    ifc_file = IFCFileFactory(project=project)
    bound = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-BADGE-BOUND", properties={})
    review = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-BADGE-REVIEW", properties={})
    unbound = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-BADGE-OPEN", properties={})
    cost_task = TaskFactory(project=project, cost=Decimal("500.00"))
    TaskEntityBinding.objects.create(
        task=cost_task,
        entity_global_id=bound.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    TaskEntityBinding.objects.create(
        task=TaskFactory(project=project),
        entity_global_id=review.global_id,
        confidence=0.6,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )
    client.force_login(user)

    missing_4d, missing_5d = _issues_property_gap_counts(ifc_file, project)
    assert missing_4d == len(_missing_activity_rows(ifc_file, project)) == 2
    assert missing_5d == len(_missing_cost_rows(ifc_file, project)) == 2
    assert unbound.global_id in {r["global_id"] for r in _missing_activity_rows(ifc_file, project)}
    assert review.global_id in {r["global_id"] for r in _missing_activity_rows(ifc_file, project)}
    assert bound.global_id not in {
        r["global_id"] for r in _missing_activity_rows(ifc_file, project)
    }

    url = reverse("model_quality:issues_count", kwargs={"pk": project.pk})
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["total"] == missing_4d + missing_5d
