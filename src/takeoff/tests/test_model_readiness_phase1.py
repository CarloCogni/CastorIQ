# takeoff/tests/test_model_readiness_phase1.py
"""Legacy Model Readiness tests — superseded by 4D Link Analysis.

Service-level inventory helpers still exist for IFC Elements / Quantities.
Page identity assertions live in test_link_analysis.py.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory
from takeoff.services.model_inventory import ModelInventoryService


def _trusted(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )


@pytest.mark.django_db
def test_inventory_route_serves_link_analysis(client):
    """Model hub inventory URL now serves 4D Link Analysis content."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-R1")
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()
    assert response.status_code == 200
    assert "4D Link Analysis" in html
    assert 'data-testid="link-analysis-page"' in html
    assert "Model Readiness" not in html


@pytest.mark.django_db
def test_model_inventory_service_still_builds_for_entities():
    """ModelInventoryService.build remains available for entities filter options."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-AID-1",
        properties={"Identity Data.Activity ID": "W-01"},
    )
    linked = TaskFactory(
        project=project,
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 3, 1),
        activity_code="W-01",
    )
    _trusted(linked, e1.global_id)

    payload = ModelInventoryService(project).build()
    assert payload["has_ifc"] is True
    assert payload["overview"]["total_entities"] >= 1
    assert "readiness" in payload


@pytest.mark.django_db
def test_ifc_elements_route_still_works(client):
    """IFC Elements drill-in remains a working grid, not a property dump."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-EL", properties={})
    client.force_login(project.owner)

    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="mi-entities-full-page"' in html
    assert "Back to Model" in html
    assert "GID-EL" not in html
