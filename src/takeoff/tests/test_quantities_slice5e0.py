# takeoff/tests/test_quantities_slice5e0.py
"""Quantities Slice 5e-0 — demote legacy QTO cache export labeling (copy only)."""

from __future__ import annotations

import inspect

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff import views as takeoff_views
from takeoff.urls import urlpatterns


@pytest.mark.django_db
def test_legacy_export_label_demoted_not_preparation_model(client):
    """Advanced tools must not claim preparation-model export."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot-5e0.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-5E0-1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()

    assert "Export preparation data model" not in html
    assert "Export legacy QTO cache" in html
    assert 'data-testid="qty-advanced-export"' in html
    assert 'data-testid="qty-advanced-legacy-export-copy"' in html

    copy = html.split('data-testid="qty-advanced-legacy-export-copy"', 1)[1][:900].lower()
    # Current demoted one-liner (Export table is the prep export primary CTA).
    assert "legacy qto cache export is separate from export table" in copy
    assert "export preparation data model" not in html.lower()

    # Remains demoted under Advanced tools, not primary toolbar.
    assert html.index('data-testid="quantities-optional-estimate"') > html.index(
        'data-testid="quantities-workspace-toolbar"'
    )
    advanced = html.split('data-testid="quantities-optional-estimate"', 1)[1]
    assert "Export legacy QTO cache" in advanced
    primary = html.split('data-testid="quantities-optional-estimate"', 1)[0]
    assert "Export legacy QTO cache" not in primary

    # No new prep-export endpoint; legacy route still present.
    names = {getattr(p, "name", None) for p in urlpatterns}
    assert "qto_export" in names
    # Prep export may exist (Slice 5e+); this slice only asserts legacy route stays.

    # Behavior unchanged: QTOExportView still the openpyxl cache exporter.
    src = inspect.getsource(takeoff_views.QTOExportView)
    assert "Workbook" in src
    assert "summary_json" in src
    assert "unit_cost" in src

    page = html.lower()
    for phrase in ("boq ready", "qs approved", "5d ready", "certified takeoff"):
        assert phrase not in page
