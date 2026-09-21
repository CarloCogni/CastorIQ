# environments/tests/test_project_sidebar_collapse_persist.py
"""Shell: project sidebar collapse must persist across full page navigations."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
def test_project_shell_includes_sidebar_collapse_persistence(client):
    """project_detail shell wires sessionStorage persistence for sidebar collapse."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="SB1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    response = client.get(url, {"basis_IfcBeam": "NetVolume"})
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="project-sidebar"' in html
    assert 'data-testid="project-sidebar-toggle"' in html
    assert "castor.projectSidebar.collapsed" in html
    assert "castor-sidebar-pref-collapsed" in html
    assert "setProjectSidebarCollapsed" in html
    assert "applyProjectSidebarCollapsedState" in html
    assert "htmx:afterSettle" in html
