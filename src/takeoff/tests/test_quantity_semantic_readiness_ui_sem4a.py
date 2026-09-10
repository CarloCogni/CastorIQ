# takeoff/tests/test_quantity_semantic_readiness_ui_sem4a.py
"""SEM-4A — Quantities UI for 5D semantic source readiness panel."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
def test_quantities_page_renders_readiness_panel(client):
    """Quantities HTML includes compact 5D semantic source readiness panel."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url, {"basis_IfcBeam": "NetVolume"}).content.decode()
    assert 'data-testid="qty-semantic-source-readiness"' in html
    assert "5D semantic source readiness" in html
    assert 'data-field-key="zone"' in html
    assert "Missing in this IFC export" in html
    assert "guidance only" in html.lower()
    assert "not final approval" in html.lower()
    assert "not cost readiness" in html.lower()
