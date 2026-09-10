# takeoff/tests/test_model_inventory_b1.py
"""Package B1 — Model Inventory spine (summary-first, trusted-only)."""

from __future__ import annotations

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


def _review(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )


@pytest.mark.django_db
def test_inventory_overview_counts_and_trusted_only():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    e2 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={},
    )
    e3 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        global_id="GID-S1",
        properties={"Qto_SlabBaseQuantities.NetArea": 10.0},
    )
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcDoor", global_id="GID-D1", properties={})
    task = TaskFactory(project=project)
    _trusted(task, e1.global_id)
    _review(task, e2.global_id)  # must not count as linked
    _trusted(task, e3.global_id)

    payload = ModelInventoryService(project).build()
    ov = payload["overview"]

    assert payload["has_ifc"] is True
    assert ov["total_entities"] == 4
    assert ov["ifc_class_count"] == 3
    assert ov["trusted_linked_entities"] == 2
    assert ov["unlinked_entities"] == 2
    assert ov["entities_with_quantity"] == 2
    assert ov["has_quantities"] is True
    assert ov["has_trusted_links"] is True

    walls = next(r for r in payload["by_class"] if r["ifc_type"] == "IfcWall")
    assert walls["element_count"] == 2
    assert walls["trusted_linked"] == 1
    assert walls["unlinked"] == 1
    assert walls["quantity_available"] == 1

    # Summary-first: no GlobalIds / properties in payload
    blob = str(payload)
    assert "GID-W1" not in blob
    assert "Qto_WallBaseQuantities" not in blob
    assert "properties" not in blob.lower() or "quantity_available" in blob


@pytest.mark.django_db
def test_inventory_empty_no_ifc(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="link-analysis-empty-no-ifc"' in html
    assert "No IFC model indexed yet" in html
    assert "BOQ control" not in html
    assert "Commercial 5D" not in html
    assert "Company actual cost" not in html


@pytest.mark.django_db
def test_inventory_page_renders_link_analysis(client):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="GID-C1",
        properties={"Qto_ColumnBaseQuantities.NetVolume": 1.1},
    )
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="link-analysis-page"' in html
    assert "4D Link Analysis" in html
    assert "IfcColumn" in html or "Linked Elements by IFC Class" in html
    assert "NetVolume" not in html
    assert "Unlink All" not in html
    assert len(response.content) < 500_000


@pytest.mark.django_db
def test_inventory_no_trusted_links_shows_zero_coverage(client):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-U1", properties={})
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert 'data-testid="la-kpi-model-coverage"' in html
    assert "0 / 1" in html or ">0<" in html
    assert "Model Link Coverage" in html
    assert "Unlinked Model Elements" not in html


@pytest.mark.django_db
def test_hub_nav_includes_model_inventory(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()

    assert 'data-testid="hub-model"' in html
    assert reverse("takeoff:model_inventory", kwargs={"pk": project.pk}) in html
