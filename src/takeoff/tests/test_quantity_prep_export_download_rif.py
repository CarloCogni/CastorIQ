# takeoff/tests/test_quantity_prep_export_download_rif.py
"""R5D-REPAIR-01 — Export session is an explicit download; RIF must not lock it."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.template.loader import get_template
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="export-rif.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-EXPORT-RIF",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    return project


@pytest.mark.django_db
def test_qty_prep_export_link_renders_as_explicit_download_control(client):
    """Visible Export session anchor must declare Castor download opt-out."""
    project = _project_with_ifc()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    idx = html.find('data-testid="qty-prep-export"')
    assert idx >= 0
    snippet = html[max(0, idx - 320) : idx + 160]
    assert "/prep-export/" in snippet
    assert 'data-castor-download="1"' in snippet
    assert 'data-testid="qty-prep-export"' in snippet


@pytest.mark.django_db
def test_qty_prep_export_endpoint_still_returns_attachment_zip(client):
    """Export contract unchanged: 200 application/zip with attachment disposition."""
    project = _project_with_ifc()
    client.force_login(project.owner)
    resp = client.get(reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}))
    assert resp.status_code == 200
    ctype = resp.get("Content-Type", "")
    assert "application/zip" in ctype or "application/octet-stream" in ctype
    cd = resp.get("Content-Disposition", "")
    assert "attachment" in cd.lower()
    assert resp.content[:2] == b"PK"


def test_castor_rif_click_handler_skips_explicit_download_controls():
    """Global RIF must skip download and data-castor-download anchors before lock()."""
    base = Path(__file__).resolve().parents[2] / "core" / "templates" / "core" / "base.html"
    src = base.read_text(encoding="utf-8")
    assert "hasAttribute('download')" in src
    assert "data-castor-download" in src
    click_idx = src.find("Plain link navigation")
    assert click_idx >= 0
    region = src[click_idx : click_idx + 2200]
    skip_idx = region.find("data-castor-download")
    lock_idx = region.find("lock(a)")
    assert skip_idx >= 0
    assert lock_idx > skip_idx
    assert "return;" in region[skip_idx:lock_idx]
    # Do not introduce a fetch/blob override in the RIF click region.
    assert "createObjectURL" not in region
    assert "fetch(" not in region


def test_prep_table_template_export_anchor_declares_castor_download():
    """Source template marks Export session with data-castor-download."""
    tpl = get_template("takeoff/components/quantities_prep_table.html")
    path = Path(tpl.origin.name)
    text = path.read_text(encoding="utf-8")
    start = text.find('data-testid="qty-prep-export"')
    assert start >= 0
    block = text[max(0, start - 400) : start + 200]
    assert 'data-castor-download="1"' in block
