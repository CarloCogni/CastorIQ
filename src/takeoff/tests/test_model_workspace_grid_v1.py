# takeoff/tests/test_model_workspace_grid_v1.py
"""Model Workspace Reference V2 + interactions — layout and interaction markers."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

_FORBIDDEN_PRIMARY = (
    "Governance",
    "Authority",
    "raw dump",
    "debug dump",
    "database browser",
)


@pytest.mark.django_db
def test_model_workspace_reference_v2_layout_markers(client):
    """Model page uses command bar, stats strip, breakdown rail, main grid, inspector."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-MW-1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="model-inventory-page"' in html
    assert 'data-testid="model-workspace-toolbar"' in html
    assert "Model Readiness" in html
    assert "IFC inventory, linkability, and QTO readiness for 4D/5D" in html
    assert 'data-testid="model-open-ifc-elements"' in html
    assert "IFC Elements" in html
    assert 'data-testid="model-inventory-overview"' in html
    assert "mi-stats" in html
    assert "mi-kpi-grid" not in html
    assert 'data-testid="model-breakdown-rail"' in html
    assert 'data-testid="model-main-grid"' in html
    assert 'data-testid="model-readiness-inspector"' in html
    assert 'data-testid="mi-class-grid"' in html
    assert 'data-testid="mi-class-table"' in html
    assert 'data-testid="mi-level-grid"' in html
    assert 'data-testid="mi-level-table"' in html
    assert 'data-testid="model-inventory-by-class"' in html
    assert 'data-testid="model-inventory-by-level"' in html
    assert 'data-testid="model-inventory-missing-data"' in html
    assert 'data-testid="model-inventory-link-coverage"' in html
    assert 'data-testid="mi-classification-unavailable"' in html
    assert "Unavailable" in html
    assert 'data-testid="model-inventory-not-boq-badge"' in html
    assert "Not BOQ" in html
    assert 'class="mi-grid"' in html
    assert "prefers-reduced-motion" in html
    assert "Element link coverage %" in html
    assert html.index('data-testid="model-inventory-not-boq-badge"') < html.index(
        'data-testid="model-inventory-source-caveat"'
    )


@pytest.mark.django_db
def test_model_workspace_interaction_markers(client):
    """Selection/collapse/filter/sort markers are present for workspace JS."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", global_id="GID-S1", properties={})
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert 'data-testid="mi-class-row"' in html
    assert 'data-mi-focus="class"' in html
    assert 'data-mi-elements="' in html
    assert 'data-mi-linked="' in html
    assert 'data-mi-coverage="' in html
    assert 'data-testid="mi-level-row"' in html
    assert 'data-mi-focus="level"' in html
    assert 'data-testid="mi-selection-inspector"' in html
    assert 'data-mi-selection-ready="1"' in html
    assert "None — select a class or level row" in html
    assert 'data-testid="mi-sel-type"' in html
    assert 'data-testid="mi-collapse-class"' in html
    assert 'data-testid="mi-collapse-level"' in html
    assert 'data-mi-collapse="class"' in html
    assert 'data-testid="mi-quick-filter"' in html
    assert "Filter visible rows" in html
    assert 'data-testid="mi-sort-class-name"' in html
    assert "mi-sortable" in html


@pytest.mark.django_db
def test_model_workspace_avoids_forbidden_primary_chrome(client):
    """Primary Model chrome avoids raw/debug/dump/database/governance/trusted labels."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", global_id="GID-B1", properties={})
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()
    primary = html.split('data-testid="model-inventory-entities"', 1)[0]
    for phrase in _FORBIDDEN_PRIMARY:
        assert phrase not in primary, phrase
    assert re.search(r"\bTrusted\b", primary) is None
    assert "database" not in primary.lower()
    assert ">raw<" not in primary.lower()


@pytest.mark.django_db
def test_ifc_elements_full_page_has_grid_and_filters(client):
    """IFC Elements full page is a professional grid with filter toolbar."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-C9", properties={})
    client.force_login(project.owner)

    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="mi-entities-full-page"' in html
    assert 'data-testid="mi-entities-toolbar"' in html
    assert 'data-testid="mi-entities-page-filters"' in html
    assert 'data-testid="mi-entities-page-quick-filter"' in html
    assert 'data-testid="mi-entities-grid"' in html
    assert 'data-testid="mi-entities-table"' in html
    assert 'data-testid="mi-entities-back-to-model"' in html
    assert "IFC Class" in html
    assert "Level / Storey" in html
    assert "Link status" in html
    assert "Has IFC Qto" in html
    assert "GID-C9" not in html
    assert "Qto_" not in html or "Has IFC Qto" in html
    assert "does not dump properties" in html.lower()
