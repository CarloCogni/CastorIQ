# ifc_viewer/tests/test_viewer_scale_figure.py
"""V1 Scale Figure — template/control presence (no GLB, no DB schema)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCFileFactory


@pytest.mark.django_db
def test_viewer_page_includes_scale_figure_controls(client):
    """Full viewer route renders Show / Reset / Follow Scale Figure controls."""
    project = ProjectFactory()
    IFCFileFactory(project=project, status="completed")
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()

    assert 'data-testid="scale-figure-toggle"' in html
    assert 'data-testid="scale-figure-reset"' in html
    assert 'data-testid="scale-figure-follow"' in html
    assert "Show Scale Figure" in html
    assert "Reset Scale Figure" in html
    assert "Follow Figure" in html
    assert "_initScaleFigure" in html
    assert "castorScaleFigure" in html
    assert "SCALE_FIGURE_HEIGHT" in html


@pytest.mark.django_db
def test_simulator_help_modal_mentions_scale_figure(client):
    """Help modal explains Scale Figure as non-human scale reference only."""
    project = ProjectFactory()
    IFCFileFactory(project=project, status="completed")
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    html = client.get(url).content.decode()

    assert 'data-testid="scale-figure-help"' in html
    assert "Scale Figure shows a simple non-human reference figure" in html
    assert "not a person, worker assignment, safety simulation, or navigation record" in html


def test_scale_figure_has_no_glb_gltf_dependency():
    """V1 uses Three primitives only — no GLB/GLTF asset paths in viewer template."""
    viewer = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer.html"
    text = viewer.read_text(encoding="utf-8")
    assert "GLTFLoader" not in text
    assert ".glb" not in text.lower()
    assert ".gltf" not in text.lower()
    assert "CapsuleGeometry" in text or "CylinderGeometry" in text
    assert "SphereGeometry" in text


@pytest.mark.django_db
def test_viewer_route_still_renders_without_ifc(client):
    """Viewer route remains reachable when no completed IFC is present."""
    project = ProjectFactory()
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "No processed IFC file" in html
    # Help modal is always in DOM even without IFC
    assert 'data-testid="scale-figure-help"' in html
