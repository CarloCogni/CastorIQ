# takeoff/tests/test_quantities_workspace_v1.py
"""Quantities Workspace — Slice 2a layout, honesty, and inventory markers."""

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
    "Generated Preparation Table",
    "Prepare Enrichment Proposal",
    "Column Configuration",
    "With IFC Qto",
    "Missing IFC Qto",
    "Quantity Coverage",
    "Model Quantity Readiness",
    "Model Quantity Reference",
)


@pytest.mark.django_db
def test_quantities_workspace_v1_layout_markers(client):
    """Builder sections + raw inventory + hardened chrome."""
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
    assert "Build Quantity Preparation Data Model" in html
    assert 'data-testid="quantities-not-boq-badge"' in html
    assert "Not BOQ" in html
    assert "Model quantities only" in html
    assert 'data-testid="quantities-open-model"' in html
    assert 'data-testid="quantities-open-ifc-elements"' in html
    assert "Open IFC Elements" in html

    # Slice 2a
    assert 'data-testid="quantities-schema-builder"' in html
    assert "Schema Builder" in html
    assert 'data-testid="quantities-source-mapping"' in html
    assert "Source Mapping" in html
    assert 'data-testid="quantities-measurement-rules"' in html
    assert "User-defined Measurement Rules" in html
    assert 'data-testid="quantities-prep-table"' in html
    assert "Generated Preparation Data Model" in html
    assert "Generated Preparation Table" not in html
    assert 'data-testid="quantities-unresolved-register"' in html
    assert "Unresolved Data Register" in html
    assert 'data-testid="quantities-modify-handoff"' in html
    assert 'data-testid="qty-send-unresolved-to-modify"' in html
    assert "Send unresolved rows to Castor Modify" in html
    assert "disabled" in html
    assert (
        "No IFC modification proposals directly" in html
        or "does not create IFC modification" in html
    )
    assert 'data-testid="quantities-model-reference"' in html
    assert "Raw Indexed Quantity Inventory" in html

    # Reference inventory (relocated readiness UI, relabeled)
    assert 'data-testid="quantities-stats-strip"' in html
    assert "mi-stats" in html
    assert "Elements with indexed quantity values" in html
    assert "Elements without indexed quantity values" in html
    assert "Raw indexed quantity availability" in html
    assert "With IFC Qto" not in html
    assert "Missing IFC Qto" not in html
    assert "with IFC Qto" not in html
    assert "Quantity Coverage" not in html
    assert "Model Quantity Readiness" not in html
    assert "elements with indexed quantity values" in html.lower()
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
    assert "Export preparation data model" in html or "Export indexed quantities" in html
    assert "Export Excel" not in html
    # Slice 2b — after Unresolved Data Register, before Modify handoff
    assert 'data-testid="quantities-visual-summary"' in html
    assert "Preparation Data Model Visual Summary" in html
    assert 'data-testid="quantities-preparation-insights"' in html
    assert "Quantity Preparation Insights" in html
    assert "Generated Preparation Data Model and Unresolved Data Register" in html
    assert html.index('data-testid="quantities-unresolved-register"') < html.index(
        'data-testid="quantities-visual-summary"'
    )
    assert html.index('data-testid="quantities-visual-summary"') < html.index(
        'data-testid="quantities-preparation-insights"'
    )
    assert html.index('data-testid="quantities-preparation-insights"') < html.index(
        'data-testid="quantities-modify-handoff"'
    )
    # Forbidden as positive claims; negation copy in Visual Summary helper is OK.
    assert "proposal readiness" not in html.lower()
    assert "Quantity Coverage" not in html
    assert "Qto Coverage" not in html
    assert "Model Quantity Readiness" not in html
    assert "Deterministic — not AI" in html


@pytest.mark.django_db
def test_quantities_workspace_interaction_markers(client):
    """Selection / collapse / filter / sort markers remain on raw inventory."""
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
    assert 'data-qty-basis-unresolved="1"' in html


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
    assert "EVM" not in primary
    assert "rates" not in primary.lower() or "not commercial" in primary.lower()


@pytest.mark.django_db
def test_quantities_workspace_missing_inspect_link(client):
    """Elements without indexed quantities still link to IFC Elements with has_qto=no."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-QC-1", properties={})
    client.force_login(project.owner)

    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert "Inspect IFC Elements" in html
    assert "has_qto=no" in html
    assert 'data-testid="qty-missing-open-entities"' in html
