# model_quality/tests/test_binding_metrics.py
"""Tests for binding-aware 4D/5D metrics in model quality services."""

from __future__ import annotations

from decimal import Decimal

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from model_quality.services.metrics import entity_metrics
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory


@pytest.mark.django_db
def test_entity_metrics_4d_ring_includes_binding_only():
    """4D readiness counts entities linked via TaskEntityBinding without Activity ID."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-MQ-4D", properties={})
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.85,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )

    metrics = entity_metrics(ifc_file)
    assert metrics["ring_4d"]["pct"] == 100
    assert metrics["missing_4d"] == 0


@pytest.mark.django_db
def test_entity_metrics_property_only_still_counts_4d():
    """Activity ID property alone still contributes to 4D readiness."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    IFCEntityFactory(
        ifc_file=ifc_file,
        properties={"Castor.Activity ID": "ACT-300"},
    )

    metrics = entity_metrics(ifc_file)
    assert metrics["ring_4d"]["pct"] == 100
    assert metrics["missing_4d"] == 0


@pytest.mark.django_db
def test_entity_metrics_schedule_cost_from_binding():
    """5D schedule cost map uses TaskEntityBinding, not M2M."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-MQ-COST", properties={})
    task = TaskFactory(project=project, cost=Decimal("5000.00"))
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    assert task.ifc_entities.count() == 0

    metrics = entity_metrics(ifc_file)
    assert metrics["has_cost_data"] is True
    assert metrics["ring_5d"]["pct"] == 100
    assert metrics["total_cost"] == 5000.0
