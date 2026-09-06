# takeoff/tests/test_quantities_slice2b.py
"""Quantities Slice 2b — Visual Summary and Preparation Insights."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import (
    build_preparation_insights,
    build_preparation_ui,
    build_unresolved_register,
)


@pytest.mark.django_db
def test_visual_summary_from_prep_rows_not_raw_inventory():
    """Visual summary uses prep-row aggregates; helper forbids readiness claims."""
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
        ifc_type="IfcBeam",
        global_id="GID-B1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.5},
    )
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    viz = ui["visual_summary"]
    assert "Generated Preparation Data Model" in viz["helper_copy"]
    assert "Unresolved Data Register" in viz["helper_copy"]
    helper_l = viz["helper_copy"].lower()
    assert "not boq readiness" in helper_l
    assert "not 5d readiness" in helper_l
    assert "not qs readiness" in helper_l
    assert viz["rows_by_status"]
    assert any(i["label"] == "Unresolved" for i in viz["quantity_basis_distribution"])
    assert any(i["label"] == "NetVolume" for i in viz["quantity_basis_distribution"])
    assert viz["unresolved_by_field"]
    assert viz["modify_handoff_status"]
    assert viz["top_unresolved_model_groups"]
    # Must not key off raw inventory fields
    assert "entities_with_quantity" not in viz
    assert "quantity_coverage_pct" not in viz


@pytest.mark.django_db
def test_preparation_insights_are_deterministic_counts():
    """Insights cards are operational notes with counts — not AI/ML."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        global_id="GID-S1",
        properties={"Qto_SlabBaseQuantities.NetArea": 10.0},
    )
    rows = build_preparation_ui(ModelQuantitiesService(project).build())["prep_rows"]
    register = build_unresolved_register(rows)
    insights = {c["id"]: c for c in build_preparation_insights(rows, register)}
    assert "IfcSlab" in insights["measurement_rules_needed"]["body"]
    assert insights["selected_source_gaps"]["count"] >= 1
    assert "Classification" in insights["mapping_gaps"]["body"]
    assert "target context" in insights["modify_handoff_candidates"]["body"]
    assert "Raw indexed quantities" in insights["raw_quantity_warning"]["body"]
    assert insights["raw_quantity_warning"]["count"] is None
    assert insights["top_unresolved_model_groups"]["body"]
    blob = " ".join(c["body"].lower() for c in insights.values())
    assert "machine learning" not in blob
    assert "auto-select" not in blob
    assert "artificial intelligence" not in blob
    titles = " ".join(c["title"].lower() for c in insights.values())
    assert "ai insight" not in titles


@pytest.mark.django_db
def test_slice2b_page_section_order_and_honesty(client):
    """Visual Summary + Insights exist between register and Modify handoff."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()

    assert "Preparation Data Model Visual Summary" in html
    assert "Quantity Preparation Insights" in html
    assert 'data-testid="qty-viz-rows-by-status"' in html
    assert 'data-testid="qty-viz-basis-distribution"' in html
    assert 'data-testid="qty-insight-measurement_rules_needed"' in html
    assert 'data-testid="qty-insight-raw_quantity_warning"' in html

    reg = html.index('data-testid="quantities-unresolved-register"')
    viz = html.index('data-testid="quantities-visual-summary"')
    insights = html.index('data-testid="quantities-preparation-insights"')
    handoff = html.index('data-testid="quantities-modify-handoff"')
    raw = html.index('data-testid="quantities-model-reference"')
    assert reg < viz < insights < handoff < raw

    page = html.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    for phrase in (
        "proposal readiness",
        "Quantity Coverage",
        "Qto Coverage",
        "Model Quantity Readiness",
    ):
        assert phrase not in page, phrase
    page_l = page.lower()
    assert "not boq readiness" in page_l
    assert "not 5d readiness" in page_l
    assert "not qs readiness" in page_l

    assert "Ask chat" not in page
    assert "machine learning" not in page.lower()
    assert 'type="file"' not in page
    assert "ModificationProposal" not in page
    # Slice 2a still intact
    assert "Build Quantity Preparation Data Model" in html
    assert "Schema Builder" in html
    assert 'data-qty-basis-unresolved="1"' in html
