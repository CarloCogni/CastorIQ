# takeoff/tests/test_model_view_recovery20a.py
"""MODEL-VIEW-RECOVERY-20A — Hub Model is the full 3D viewer; Link Analysis is Links diagnostic."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _hub_active(html: str, testid: str) -> bool:
    for tag in re.finditer(r"<a\b[^>]*>", html):
        chunk = tag.group(0)
        if f'data-testid="{testid}"' not in chunk:
            continue
        cls_m = re.search(r'class="([^"]*)"', chunk)
        cls = cls_m.group(1) if cls_m else ""
        return "active" in cls.split()
    return False


def _hub_tag(html: str, testid: str) -> str:
    for tag in re.finditer(r"<a\b[^>]*>", html):
        if f'data-testid="{testid}"' in tag.group(0):
            return tag.group(0)
    raise AssertionError(f"missing hub pill {testid}")


@pytest.mark.django_db
def test_hub_model_href_resolves_to_viewer(client):
    """Hub Model pill points at ifc_viewer:viewer, not inventory."""
    project = ProjectFactory()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()

    viewer = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    model_tag = _hub_tag(html, "hub-model")
    assert viewer in model_tag
    assert reverse("takeoff:model_inventory", kwargs={"pk": project.pk}) not in model_tag


@pytest.mark.django_db
def test_viewer_route_marks_model_active_and_shows_viewer(client):
    """Viewer route marks Model active and renders Castor Simulator chrome."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-V1")
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    assert _hub_active(html, "hub-model")
    assert not _hub_active(html, "hub-links")
    assert 'id="ifc-viewer-root"' in html
    assert "4D Link Analysis" not in html
    assert 'data-testid="link-analysis-page"' not in html
    assert "pilot.ifc" in html


@pytest.mark.django_db
def test_viewer_works_with_ifc_and_zero_schedule_tasks(client):
    """Model/viewer does not require a schedule."""
    project = ProjectFactory()
    IFCFileFactory(project=project, status="completed", name="nosched.ifc")
    client.force_login(project.owner)

    html = client.get(reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})).content.decode()
    assert 'id="ifc-viewer-root"' in html
    assert "nosched.ifc" in html
    assert "Upload and process an IFC" not in html


@pytest.mark.django_db
def test_viewer_empty_ifc_state_is_readable(client):
    """No IFC → honest empty state, not a raw crash."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("ifc_viewer:viewer", kwargs={"pk": project.pk}))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Upload and process an IFC" in html
    assert _hub_active(html, "hub-model")


@pytest.mark.django_db
def test_links_still_fourd_link_and_exposes_link_analysis(client):
    """Links workspace stays fourD_link; Link Analysis remains reachable."""
    project = ProjectFactory()
    client.force_login(project.owner)

    url = reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    html = client.get(url).content.decode()

    assert _hub_active(html, "hub-links")
    assert not _hub_active(html, "hub-model")
    inventory = reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    # Link Analysis may be exposed from Quantities ("Open Link Analysis") or hub compat;
    # route must resolve and not mark Model active when opened.
    la = client.get(inventory).content.decode()
    assert 'data-testid="link-analysis-page"' in la or "4D Link Analysis" in la
    assert not _hub_active(la, "hub-model")
    assert _hub_active(la, "hub-links")


@pytest.mark.django_db
def test_link_analysis_route_does_not_mark_model_active(client):
    """Legacy /inventory/ stays Link Analysis and uses Links hub context."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", global_id="GID-LA1")
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert "4D Link Analysis" in html
    assert 'data-testid="link-analysis-page"' in html
    assert not _hub_active(html, "hub-model")
    assert _hub_active(html, "hub-links")


@pytest.mark.django_db
def test_other_hub_routes_unchanged(client):
    """Schedule / Time View / Quantities / Controls demotion retain contracts."""
    project = ProjectFactory()
    client.force_login(project.owner)
    schedule = reverse("scheduling:schedule", kwargs={"pk": project.pk})

    sched = client.get(schedule + "?tab=data_sources").content.decode()
    assert _hub_active(sched, "hub-schedule")
    assert reverse("ifc_viewer:viewer", kwargs={"pk": project.pk}) in sched

    tv = client.get(schedule + "?tab=lookahead").content.decode()
    assert _hub_active(tv, "hub-time-view")

    qty = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert _hub_active(qty, "hub-quantities")
    assert "IFC Quantity Preparation" in qty

    # Packaging founder contract: Controls stays primary executive_controls.
    assert 'data-testid="hub-controls"' in sched
    controls_tag = next(
        t.group(0)
        for t in re.finditer(r"<a\b[^>]*>", sched)
        if 'data-testid="hub-controls"' in t.group(0)
    )
    assert reverse("scheduling:executive_controls", kwargs={"pk": project.pk}) in controls_tag
    assert "model_inventory" not in controls_tag
    assert "inventory" not in controls_tag
