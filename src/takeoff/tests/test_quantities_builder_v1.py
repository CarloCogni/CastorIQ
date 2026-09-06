# takeoff/tests/test_quantities_builder_v1.py
"""Quantities builder Slice 2a — preparation UI contract and honesty."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import build_preparation_ui


@pytest.mark.django_db
def test_preparation_ui_builds_rows_from_class_aggregates():
    """Prep rows use real IFC class + totals; mapping fields stay blank."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.5},
    )
    quantities = ModelQuantitiesService(project).build()
    ui = build_preparation_ui(quantities)
    assert ui["schema_fields"]
    assert ui["source_mappings"]
    assert ui["basis_rules"]
    assert ui["prep_rows"]
    row = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcBeam")
    assert row["quantity_source"] == "NetVolume"
    assert row["quantity_basis"] == "NetVolume"
    assert row["unit_basis"] == "model volume units"
    assert row["total"] == 0.5
    assert row["total_display"] == 0.5
    assert row["classification_code"] == ""
    assert row["package_boq_mapping"] == ""
    assert row["work_package"] == ""
    assert ui["unresolved_register"]["missing_classification"] >= 1


@pytest.mark.django_db
def test_wall_and_slab_unresolved_by_default():
    """IfcWall / IfcSlab stay unresolved; totals are Unresolved, not raw Qto."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        global_id="GID-S1",
        properties={"Qto_SlabBaseQuantities.NetArea": 20.0},
    )
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    slab = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcSlab")
    assert wall["basis_unresolved"] is True
    assert slab["basis_unresolved"] is True
    assert wall["quantity_basis"] == ""
    assert slab["quantity_basis"] == ""
    assert wall["total"] is None
    assert slab["total"] is None
    assert wall["total_display"] == "Unresolved"
    assert slab["total_display"] == "Unresolved"

    rules = {r["model_group"]: r for r in ui["basis_rules"]}
    assert rules["IfcWall"]["quantity_basis"] == "Unresolved"
    assert rules["IfcSlab"]["quantity_basis"] == "Unresolved"
    assert rules["IfcWall"]["needs_basis_action"] is True
    assert rules["IfcSlab"]["needs_basis_action"] is True
    assert "Select a basis" in rules["IfcWall"]["note"]
    assert rules["IfcWall"]["param_name"] == "basis_IfcWall"
    assert any(opt["value"] == "NetArea" for opt in rules["IfcWall"]["basis_options"])
    assert isinstance(rules["IfcWall"]["available_indexed_measures"], list)


@pytest.mark.django_db
def test_builder_page_markers_and_disabled_modify_handoff(client):
    """Page exposes Slice 2a/3a IA; Modify handoff disabled; no Slice 1 labels."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcDoor",
        global_id="GID-D1",
        properties={},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()

    assert "Build Quantity Preparation Data Model" in html
    assert "Schema Builder" in html
    assert 'data-testid="quantities-schema-builder"' in html
    assert 'data-testid="quantities-setup-summary"' in html
    assert "Preparation Setup Summary" in html
    assert "Rows eligible for Modify handoff" in html
    assert "Rows ready for Modify handoff" not in html
    assert 'data-testid="qty-basis-rules-form"' in html
    assert 'data-testid="qty-select-basis-IfcWall"' in html
    assert 'data-testid="qty-generate-prep-model"' in html
    assert "Generate Preparation Data Model" in html
    assert "Session-only — not saved to project" in html
    assert "not editable in this slice" not in html
    assert "Source Mapping" in html
    assert 'data-testid="quantities-source-mapping"' in html
    assert "User-defined Measurement Rules" in html
    assert 'data-testid="quantities-measurement-rules"' in html
    assert "Generated Preparation Data Model" in html
    assert "Unresolved Data Register" in html
    assert "Send unresolved rows to Castor Modify" in html
    assert 'data-testid="qty-send-unresolved-to-modify"' in html
    assert "disabled" in html
    assert "Raw Indexed Quantity Inventory" in html
    assert 'data-testid="qty-raw-inventory-details"' in html

    # Wall unresolved total in prep table
    assert 'data-qty-ifc-class="IfcWall"' in html
    assert 'data-qty-basis-unresolved="1"' in html

    page = html.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    assert "Generated Preparation Table" not in page
    assert "Prepare Enrichment Proposal" not in page
    assert "Column Configuration" not in page
    assert "Quantity Basis Rules" not in page
    assert "Missing Data Summary" not in page
    assert "Model Quantity Reference" not in page
    assert "concrete" not in page.lower()
    # No Ask/RAG/upload/writeback *controls* on Quantities page (negation copy OK)
    assert "Ask chat" not in page
    assert "PDF upload" not in page
    assert "Excel upload" not in page
    assert 'type="file"' not in page
    assert "ModificationProposal" not in page
    assert "Generated 5D Table" not in page
    assert "future 5D" not in page.lower()
    assert "5D readiness" not in page.lower()
    # Handoff CTA must not link into Modify from this screen.
    cta = html.split('data-testid="qty-send-unresolved-to-modify"', 1)[1].split("</button>", 1)[0]
    assert "href=" not in cta
    assert "Castor Modify handles suggestions, review" in html
    assert "writeback, Git trace, and re-index" in html
