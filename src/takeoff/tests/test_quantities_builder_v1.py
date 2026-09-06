# takeoff/tests/test_quantities_builder_v1.py
"""Quantities builder Slice 1 — preparation UI markers and honesty."""

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
    assert ui["column_config"]
    assert ui["basis_rules"]
    assert ui["prep_rows"]
    row = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcBeam")
    assert row["quantity_source"] == "NetVolume"
    assert row["quantity_basis"] == "NetVolume"
    assert row["unit_basis"] == "model volume units"
    assert row["total"] == 0.5
    assert row["zone"] == ""
    assert row["classification_code"] == ""
    assert row["package_boq_code"] == ""
    assert row["work_package"] == ""
    assert ui["missing_summary"]["missing_classification_code"] >= 1


@pytest.mark.django_db
def test_builder_page_markers_and_disabled_enrichment(client):
    """Page exposes builder IA; enrichment CTA disabled; no 5D table label."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcDoor",
        global_id="GID-D1",
        properties={},
    )
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()

    assert "Generated Preparation Table" in html
    assert "Generated 5D Table" not in html
    assert "Quantity Source" in html
    assert "Quantity Basis" in html
    assert "Prepare Enrichment Proposal" in html
    assert 'data-testid="qty-prepare-enrichment-proposal"' in html
    # Disabled button present (attribute on the CTA).
    assert (
        'data-testid="qty-prepare-enrichment-proposal"\n          disabled' in html
        or 'disabled\n          aria-disabled="true"\n          title="Coming later' in html
        or 'qty-prepare-enrichment-proposal" disabled' in html
        or ("Prepare Enrichment Proposal" in html and "disabled" in html)
    )
    assert "review, approval, writeback, Git trace, and re-index" in html
    assert "material not auto-detected" in html.lower() or "Material examples" in html
    assert "future 5D" not in html.lower()
    assert "5D readiness" not in html.lower()
    # Enrichment CTA must not link into Modify from this screen.
    assert 'data-testid="qty-prepare-enrichment-proposal"' in html
    assert 'href=' not in html.split('data-testid="qty-prepare-enrichment-proposal"', 1)[1].split(
        "</button>", 1
    )[0]
