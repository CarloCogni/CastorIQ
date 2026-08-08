# takeoff/tests/test_visual_polish_s12_b21.py
"""S1.2 / B2.1 visual polish — entities shell + trusted wording + cost labels."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
def test_entities_browser_get_returns_full_shell(client):
    """Normal browser GET is styled app shell, not a raw partial-only page."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-W1", properties={})
    client.force_login(project.owner)

    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    response = client.get(url + "?has_qto=no")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="mi-entities-full-page"' in html
    assert "IFC Elements" in html
    assert 'data-testid="mi-entities-back-to-model"' in html
    assert "Back to Model" in html
    assert 'data-testid="hub-model"' in html  # Castor hub chrome
    assert 'data-testid="mi-entities-results"' in html
    assert "Qto_WallBaseQuantities" not in html
    assert "GID-W1" not in html
    assert "properties" not in html.lower() or "does not dump properties" in html.lower()


@pytest.mark.django_db
def test_entities_htmx_get_still_returns_partial(client):
    """HTMX GET keeps returning the lazy list partial only."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)

    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    response = client.get(url, HTTP_HX_REQUEST="true")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="mi-entities-results"' in html
    assert 'data-testid="mi-entities-full-page"' not in html
    assert 'data-testid="hub-model"' not in html
    assert "Back to Model" not in html
    assert "GID-W2" not in html


@pytest.mark.django_db
def test_walked_pages_use_applied_confirmed_not_trusted_labels(client):
    """Time View / Controls show Applied / Confirmed, not trusted product wording."""
    project = ProjectFactory()
    client.force_login(project.owner)

    lookahead = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert "applied / confirmed links" in lookahead
    assert "trusted links" not in lookahead.lower()

    controls = client.get(
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
    ).content.decode()
    assert "Applied / Confirmed only" in controls
    assert "Trusted-linked only" not in controls
    assert "trusted-only" not in controls.lower()
    assert "trusted binding" not in controls.lower()


@pytest.mark.django_db
def test_schedule_readiness_assignment_cost_labels(client):
    """Data readiness uses Planned assignment cost / Assignment actual cost indicator."""
    from scheduling.tests.factories import TaskFactory

    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()

    assert "Planned assignment cost" in html
    assert "Assignment actual cost indicator" in html
    assert "Planned cost —" not in html
    assert "Actual cost — not imported" not in html
    assert "Assignment actual cost indicator — not imported" in html
