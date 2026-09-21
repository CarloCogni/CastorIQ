# takeoff/tests/test_quantities_workflow_rail_layout.py
"""TABLE-04 — workflow rail removed; table-first chrome stays flex-stable."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
def test_quantities_workflow_rail_removed_and_header_flex_stable(client):
    """Quantities no longer renders the six-stage rail; header/table chrome remain."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="WR1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"basis_IfcBeam": "NetVolume"},
    ).content.decode()
    assert 'data-testid="qty-c5d-workflow-rail"' not in html
    assert 'data-testid="qty-c5d-workflow-rail-removed"' in html
    assert 'data-testid="qty-prep-primary-toolbar"' in html
    assert "flex-shrink: 0" in html
    assert 'data-testid="quantities-prep-table"' in html
