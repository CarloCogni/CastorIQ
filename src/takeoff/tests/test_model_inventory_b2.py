# takeoff/tests/test_model_inventory_b2.py
"""Package B2 — Model Inventory depth (levels, missing data, lazy entities)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory
from takeoff.services.model_inventory import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    UNASSIGNED_LEVEL_KEY,
    ModelInventoryService,
)


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


def _project_with_levels():
    """Two storey-placed walls + one unassigned; mixed Qto and links."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    storey_ent = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBuildingStorey",
        name="Roof 01",
        global_id="GID-STOREY",
        properties={},
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc, entity=storey_ent, spatial_type="building_storey"
    )
    w1 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="Wall A",
        global_id="GID-W1",
        spatial_container=storey,
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    w2 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="Wall B",
        global_id="GID-W2",
        spatial_container=storey,
        properties={},
    )
    unassigned = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        name="Orphan Slab",
        global_id="GID-S1",
        spatial_container=None,
        properties={"Qto_SlabBaseQuantities.NetArea": 5.0},
    )
    task = TaskFactory(project=project)
    _trusted(task, w1.global_id)
    _review(task, w2.global_id)  # must not count as linked
    return project, ifc, storey, w1, w2, unassigned


@pytest.mark.django_db
def test_by_level_summary_linked_unlinked_and_unassigned():
    """Level rows + Unassigned; linked counts use applied/confirmed only."""
    project, _ifc, storey, _w1, _w2, _u = _project_with_levels()

    payload = ModelInventoryService(project).build()
    by_level = {r["level_key"]: r for r in payload["by_level"]}

    storey_row = by_level[str(storey.pk)]
    assert storey_row["level_label"] == "Roof 01"
    assert storey_row["entity_count"] == 2
    assert storey_row["linked_count"] == 1
    assert storey_row["unlinked_count"] == 1
    assert storey_row["has_ifc_qto_count"] == 1
    assert storey_row["ifc_class_count"] == 1

    unassigned = by_level[UNASSIGNED_LEVEL_KEY]
    assert unassigned["level_label"] == "Unassigned"
    # Storey spatial node entity itself is typically unassigned, plus orphan slab.
    assert unassigned["entity_count"] == 2
    assert unassigned["linked_count"] == 0
    assert unassigned["unlinked_count"] == 2

    assert payload["missing_model_data"]["missing_level_count"] == 2
    blob = str(payload)
    assert "GID-W1" not in blob
    assert "Qto_WallBaseQuantities" not in blob


@pytest.mark.django_db
def test_missing_model_data_and_classification_unavailable(client):
    """Link Analysis page renders without readiness QTO chrome; GIDs stay off first paint."""
    project, *_ = _project_with_levels()
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="link-analysis-page"' in html
    assert "4D Link Analysis" in html
    # Property dumps stay off; element IDs may appear inside expandable link detail.
    assert "Qto_WallBaseQuantities" not in html
    assert "NetVolume" not in html


@pytest.mark.django_db
def test_entity_list_page_size_default_and_cap(client):
    """Default page size 50; page_size=500 capped at 100."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i in range(120):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcWall",
            global_id=f"GID-BULK-{i:04d}",
            properties={},
        )
    client.force_login(project.owner)
    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})

    default = ModelInventoryService(project).list_entities()
    assert default["page_size"] == DEFAULT_PAGE_SIZE
    assert len(default["rows"]) == DEFAULT_PAGE_SIZE

    capped = ModelInventoryService(project).list_entities(page_size=500)
    assert capped["page_size"] == MAX_PAGE_SIZE
    assert len(capped["rows"]) == MAX_PAGE_SIZE

    response = client.get(f"{url}?page_size=500")
    html = response.content.decode()
    assert response.status_code == 200
    assert "100 per page" in html
    assert html.count('data-testid="mi-entity-row"') == MAX_PAGE_SIZE


@pytest.mark.django_db
def test_entity_list_filters_class_level_linked_qto(client):
    """Filters: IFC class, level, applied/confirmed link, Has IFC Qto."""
    project, _ifc, storey, w1, w2, unassigned = _project_with_levels()
    svc = ModelInventoryService(project)

    by_class = svc.list_entities(ifc_class="IfcWall")
    assert by_class["total_matched"] == 2
    assert all(r["ifc_class"] == "IfcWall" for r in by_class["rows"])

    by_level = svc.list_entities(level=str(storey.pk))
    assert by_level["total_matched"] == 2
    assert all(r["level_label"] == "Roof 01" for r in by_level["rows"])

    unassigned_page = svc.list_entities(level=UNASSIGNED_LEVEL_KEY)
    assert unassigned_page["total_matched"] == 2
    assert any(r["display_name"] == "Orphan Slab" for r in unassigned_page["rows"])

    linked = svc.list_entities(linked_status="linked")
    assert linked["total_matched"] == 1
    assert linked["rows"][0]["link_status_label"] == "Applied/Confirmed"
    assert linked["rows"][0]["display_name"] == w1.name

    # Alias used in product wording
    linked_alias = svc.list_entities(linked_status="applied-confirmed")
    assert linked_alias["total_matched"] == 1

    unlinked = svc.list_entities(linked_status="unlinked")
    assert unlinked["total_matched"] == 3
    names = {r["display_name"] for r in unlinked["rows"]}
    assert w2.name in names
    assert unassigned.name in names

    has_qto = svc.list_entities(has_qto="yes")
    assert has_qto["total_matched"] == 2
    assert all(r["has_ifc_qto"] for r in has_qto["rows"])

    no_qto = svc.list_entities(has_qto="no")
    assert no_qto["total_matched"] == 2
    assert all(r["has_ifc_qto"] is False for r in no_qto["rows"])

    client.force_login(project.owner)
    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    html = client.get(f"{url}?ifc_class=IfcWall&linked_status=unlinked").content.decode()
    assert "Wall B" in html
    assert "Wall A" not in html
    assert "Applied/Confirmed" not in html or "Unlinked" in html


@pytest.mark.django_db
def test_entity_list_payload_excludes_properties_and_gids(client):
    """Entity endpoint must not dump properties JSON or GlobalId lists."""
    project, *_ = _project_with_levels()
    client.force_login(project.owner)
    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})

    result = ModelInventoryService(project).list_entities()
    blob = str(result)
    assert "properties" not in blob
    assert "GID-W1" not in blob
    assert "Qto_WallBaseQuantities" not in blob
    for row in result["rows"]:
        assert "properties" not in row
        assert "global_id" not in row

    html = client.get(url).content.decode()
    assert "GID-W1" not in html
    assert "Qto_WallBaseQuantities" not in html
    assert 'data-testid="mi-entities-table"' in html


@pytest.mark.django_db
def test_model_page_wording_honesty(client):
    """No BOQ/ERP/commercial 5D/approval/governance/authority labels on Link Analysis."""
    project, *_ = _project_with_levels()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert "Model Link Coverage" in html
    assert "Schedule Link Coverage" in html
    assert "Link Review Table" in html
    assert "Unlinked Model Elements" not in html
    assert "Task Link Coverage" not in html

    forbidden_positive = [
        "QS valuation",
        "Commercial 5D",
        "company actual cost",
        "Procurement",
        "Invoice",
        "ERP ledger",
        "approval workflow",
        "governance",
        "authority",
        "BOQ",
        "QTO",
        "NetVolume",
    ]
    for phrase in forbidden_positive:
        assert phrase not in html, phrase
    assert "Trusted link" not in html
    assert ">Trusted<" not in html
    assert "Unlink All" not in html


@pytest.mark.django_db
def test_first_paint_has_no_entity_rows(client):
    """Link Analysis first paint uses review table; no inventory HTMX entity block."""
    project, *_ = _project_with_levels()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert 'data-testid="link-analysis-table"' in html
    assert 'data-testid="model-inventory-entities"' not in html
    assert "inventory/entities/" not in html
    assert 'data-testid="mi-entity-row"' not in html
    assert len(html.encode("utf-8")) < 500_000
