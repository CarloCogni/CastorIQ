# takeoff/tests/test_quantities_workspace_v1.py
"""Quantities Workspace — builder layout, honesty, and reference markers."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)

_FORBIDDEN_PRIMARY = (
    "Governance",
    "Authority",
    "raw dump",
    "debug dump",
    "database browser",
    "cost estimate",
    "ERP cost",
    "Optional unit-cost estimate",
    "Estimated total by level",
    "Generated 5D Table",
)


@pytest.mark.django_db
def test_quantities_workspace_v1_layout_markers(client):
    """Builder sections + reference grids + hardened chrome."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot-qty.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-QW-1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="quantities-page"' in html
    assert 'data-testid="quantities-workspace-toolbar"' in html
    assert 'data-testid="quantities-workspace-title"' in html
    assert "Quantities" in html
    assert 'data-testid="quantities-workspace-subtitle"' in html
    assert "IFC Model Quantities" in html
    assert 'data-testid="quantities-not-boq-badge"' in html
    assert "Not BOQ" in html
    assert 'data-testid="quantities-open-model"' in html
    assert 'data-testid="quantities-open-ifc-elements"' in html
    assert "Open IFC Elements" in html

    # Builder Slice 1
    assert 'data-testid="quantities-workspaces-mode"' in html
    assert 'data-testid="quantities-column-config"' in html
    assert 'data-testid="quantities-basis-rules"' in html
    assert "Starter rules — review before using for enrichment" in html
    assert 'data-testid="quantities-generate-prep"' in html
    assert 'data-testid="quantities-prep-table"' in html
    assert "Generated Preparation Table" in html
    assert "Generated 5D Table" not in html
    assert 'data-testid="quantities-missing-summary"' in html
    assert 'data-testid="quantities-enrichment-cta"' in html
    assert 'data-testid="qty-prepare-enrichment-proposal"' in html
    assert "disabled" in html
    assert "No IFC writeback happens directly from this screen" in html
    assert 'data-testid="quantities-model-reference"' in html

    # Reference (relocated readiness UI)
    assert 'data-testid="quantities-stats-strip"' in html
    assert "mi-stats" in html
    assert 'data-testid="qty-stat-with-qto"' in html
    assert 'data-testid="qty-stat-missing"' in html
    assert "Missing IFC Qto" in html
    assert "Quantity Coverage" in html
    assert 'data-testid="quantity-breakdown-rail"' in html
    assert 'data-testid="quantity-main-grid"' in html
    assert 'data-testid="quantity-readiness-inspector"' in html
    assert 'data-testid="quantity-by-ifc-class"' in html
    assert 'data-testid="quantity-by-level"' in html
    assert 'data-testid="missing-quantities"' in html
    assert 'data-testid="quantity-readiness"' in html
    assert 'data-testid="qty-classification-unavailable"' in html
    assert "Unavailable" in html
    assert "Length (model length units)" in html
    assert "model length units" in html.lower()
    assert "m³" not in html
    assert "m²" not in html
    assert 'class="mi-grid"' in html
    assert "prefers-reduced-motion" in html
    assert 'data-testid="quantities-optional-estimate"' in html
    assert 'data-testid="quantities-boundary-copy"' in html
    assert "verified QS measurement" in html
    assert html.index('data-testid="quantities-workspace-toolbar"') < html.index(
        'data-testid="quantities-optional-estimate"'
    )
    assert "qty-advanced-recompute" in html
    assert "Recompute optional cache" in html
    assert "Export model quantities" in html
    assert "Export Excel" not in html


@pytest.mark.django_db
def test_quantities_workspace_interaction_markers(client):
    """Selection / collapse / filter / sort markers remain on Model Quantity Reference."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    storey_ent = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBuildingStorey",
        name="L1",
        global_id="GID-QS-1",
        properties={},
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc, entity=storey_ent, spatial_type="building_storey"
    )
    et = IFCElementTypeFactory(ifc_file=ifc, name="WallType-A", ifc_type="IfcWallType")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-QW-2",
        spatial_container=storey,
        element_type=et,
        properties={"Qto_WallBaseQuantities.NetVolume": 1.5},
    )
    client.force_login(project.owner)

    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()

    assert 'data-testid="qty-class-row"' in html
    assert 'data-qty-focus="class"' in html
    assert 'data-qty-elements="' in html
    assert 'data-qty-has="' in html
    assert 'data-qty-coverage="' in html
    assert 'data-testid="qty-level-row"' in html
    assert 'data-qty-focus="level"' in html
    assert 'data-testid="qty-selection-inspector"' in html
    assert 'data-qty-selection-ready="1"' in html
    assert "None — select a class, level, or type row" in html
    assert 'data-testid="qty-sel-type"' in html
    assert 'data-testid="qty-sel-net-volume"' in html
    assert 'data-testid="qty-collapse-class"' in html
    assert 'data-testid="qty-collapse-level"' in html
    assert 'data-qty-collapse="class"' in html
    assert 'data-testid="qty-quick-filter"' in html
    assert "Filter visible rows" in html
    assert 'data-testid="qty-sort-class-name"' in html
    assert "mi-sortable" in html
    assert 'data-testid="qty-type-row"' in html or 'data-testid="quantity-by-type"' in html
    assert 'data-testid="qty-prep-row"' in html


@pytest.mark.django_db
def test_quantities_workspace_avoids_forbidden_primary_chrome(client):
    """Primary Quantities chrome avoids cost/BOQ-as-goal and raw/debug labels."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-QB-1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    client.force_login(project.owner)

    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    primary = html.split('data-testid="quantities-optional-estimate"', 1)[0]
    for phrase in _FORBIDDEN_PRIMARY:
        assert phrase not in primary, phrase
    assert re.search(r"\bTrusted\b", primary) is None
    assert "database" not in primary.lower()
    assert ">raw<" not in primary.lower()
    assert "GID-QB-1" not in html
    assert "Qto_BeamBaseQuantities" not in html
    assert "total_cost_estimate" not in html
    assert "unit_cost" not in primary


@pytest.mark.django_db
def test_quantities_workspace_missing_inspect_link(client):
    """Missing IFC Qto still links to IFC Elements with has_qto=no."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-QC-1", properties={})
    client.force_login(project.owner)

    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert "Inspect IFC Elements" in html
    assert "has_qto=no" in html
    assert 'data-testid="qty-missing-open-entities"' in html
