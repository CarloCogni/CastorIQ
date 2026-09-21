# takeoff/tests/test_quantities_discover_advanced_overlap_r5d_repair03.py
"""R5D-REPAIR-03 + TABLE-04 — table/filter must not overlap Advanced (UX-OVERLAP-001)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="overlap-repair.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-OVERLAP-R03",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.5},
    )
    return project


def _c5d_styles() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "takeoff"
        / "components"
        / "quantities_c5d_styles.html"
    ).read_text(encoding="utf-8")


def test_workspace_layout_contract_avoids_viewport_height_clip():
    """Workspace CSS must content-size (not flex:1 + min-height:0 viewport cap)."""
    css = _c5d_styles()
    assert "c5d-workspace-flow" in css
    assert "UX-OVERLAP-001" in css
    assert "flex: 0 0 auto" in css
    assert "min-height: auto" in css
    assert "max-height: none" in css
    assert "overflow: visible" in css
    workspace_block = css.split(".qty-ws .c5d-workspace", 1)[1].split(".qty-ws .c5d-main", 1)[0]
    assert "flex: 1 1 auto" not in workspace_block
    assert "min-height: 0" not in workspace_block


def test_advanced_section_has_document_flow_spacing():
    """Advanced keeps non-absolute document-flow spacing after workspace."""
    css = _c5d_styles()
    adv = css.split(".qty-ws .c5d-advanced {", 1)[1].split("}", 1)[0]
    assert "position: absolute" not in adv
    assert "margin:" in adv
    assert "0.5rem" in adv


@pytest.mark.django_db
def test_quantities_page_renders_table_filter_and_advanced_flow(client):
    """One-table filter bar and Advanced remain present with flow class."""
    project = _project_with_ifc()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
        },
    ).content.decode()

    assert 'data-testid="qty-c5d-workspace"' in html
    assert "c5d-workspace-flow" in html
    assert 'data-testid="qty-table-filter-bar"' in html
    assert 'data-testid="quantities-optional-estimate"' in html
    assert "Advanced &amp; reference" in html or "Advanced & reference" in html
    assert "Select visible rows" in html

    filter_idx = html.find('data-testid="qty-table-filter-bar"')
    advanced_idx = html.find('data-testid="quantities-optional-estimate"')
    assert filter_idx >= 0
    assert advanced_idx > filter_idx
