# takeoff/tests/test_model_workspace_grid_v1.py
"""Model hub content is now 4D Link Analysis — layout markers updated."""

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
def test_model_hub_serves_link_analysis_layout(client):
    """Model inventory route renders 4D Link Analysis chrome."""
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
    assert 'data-testid="link-analysis-page"' in html
    assert "4D Link Analysis" in html
    assert "CASTOR ENGINE / 4D LINKING" not in html
    assert "Validate Links" not in html
    assert 'data-testid="link-analysis-kpis"' in html
    assert 'data-testid="link-analysis-charts"' in html
    assert 'data-testid="link-analysis-table"' in html
    assert "NetVolume" not in html
    assert "Model Readiness" not in html
    assert "Not BOQ" not in html


@pytest.mark.django_db
def test_link_analysis_interaction_markers(client):
    """Tabs, search, expand markers present."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", global_id="GID-S1", properties={})
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert 'data-testid="la-tab-tasks"' in html
    assert 'data-testid="la-tab-elements"' in html
    assert 'data-testid="la-tab-grouped"' in html
    assert 'data-testid="la-search"' in html
    assert 'data-la-tab="tasks"' in html


@pytest.mark.django_db
def test_model_workspace_avoids_forbidden_primary_chrome(client):
    """Primary Model chrome avoids raw/debug/dump/database/governance labels."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", global_id="GID-B1", properties={})
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()
    for phrase in _FORBIDDEN_PRIMARY:
        assert phrase not in html, phrase
    assert re.search(r"\bTrusted\b", html) is None
    assert "database browser" not in html.lower()


@pytest.mark.django_db
def test_ifc_elements_full_page_has_grid_and_filters(client):
    """IFC Elements full page remains available as a secondary drill-in."""
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
    assert 'data-testid="mi-entities-back-to-model"' in html
    assert "GID-C9" not in html
