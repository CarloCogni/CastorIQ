# ifc_viewer/tests/test_viewer_scale_figure.py
"""Scale Figure — template/control presence (no GLB, no DB schema, no Follow)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCFileFactory

_HELP_REQUIRED = "Scale Figure adds a simple non-human height reference to help judge model scale."
_FORBIDDEN_UI = (
    "Follow Figure",
    "Site Walker",
    "Walk Proxy",
    "scale-figure-follow",
)


@pytest.mark.django_db
def test_viewer_page_includes_scale_figure_controls(client):
    """Full viewer route renders Show / Hide / Reset Scale Figure only."""
    project = ProjectFactory()
    IFCFileFactory(project=project, status="completed")
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()

    assert 'data-testid="scale-figure-toggle"' in html
    assert 'data-testid="scale-figure-reset"' in html
    assert "Show Scale Figure" in html
    assert "Reset Scale Figure" in html
    assert "Hide Scale Figure" in html  # label used when toggled (in JS)
    assert "_initScaleFigure" in html
    assert "castorScaleFigure" in html
    assert "SCALE_FIGURE_HEIGHT" in html
    assert "1.75 model units" in html

    for term in _FORBIDDEN_UI:
        assert term not in html


@pytest.mark.django_db
def test_simulator_help_modal_mentions_scale_figure(client):
    """Help modal explains Scale Figure as height reference only."""
    project = ProjectFactory()
    IFCFileFactory(project=project, status="completed")
    client.force_login(project.owner)

    url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
    html = client.get(url).content.decode()

    assert 'data-testid="scale-figure-help"' in html
    assert _HELP_REQUIRED in html
    assert (
        "not a worker, avatar, safety simulation, walkthrough recording, or navigation log" in html
    )
    assert "Height is approximate and based on model units" in html

    # Forbidden product-claim phrasing in help / UI
    assert "Site Walker" not in html
    assert "Follow Figure" not in html
    assert "worker tracking" not in html.lower()
    # Negation list may include these words — ensure they appear only as disclaimers
    help_block = html[
        html.find('data-testid="scale-figure-help"') : html.find('data-testid="scale-figure-help"')
        + 600
    ]
    assert (
        "It is not a worker, avatar, safety simulation, walkthrough recording, or navigation log"
        in help_block
    )


def test_scale_figure_has_no_glb_gltf_dependency():
    """Uses Three primitives only — no GLB/GLTF asset paths in viewer template."""
    viewer = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer.html"
    text = viewer.read_text(encoding="utf-8")
    assert "GLTFLoader" not in text
    assert ".glb" not in text.lower()
    assert ".gltf" not in text.lower()
    assert "CylinderGeometry" in text
    assert "Follow Figure" not in text
    assert "Site Walker" not in text
    assert "scale-figure-follow" not in text


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
    assert 'data-testid="scale-figure-help"' in html
    assert "Follow Figure" not in html
