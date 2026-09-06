# takeoff/tests/test_quantities_slice3a.py
"""Quantities Slice 3a — interactive basis rules via GET query params."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import (
    build_preparation_ui,
    parse_basis_overrides_from_query,
)


def _wall_slab_project():
    """Project with Wall (NetArea + NetVolume) and Slab (NetVolume) for override tests."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-S3A",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        global_id="GID-S-S3A",
        properties={"Qto_SlabBaseQuantities.NetVolume": 8.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-S3A",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


@pytest.mark.django_db
def test_defaults_without_query_params_keep_wall_slab_unresolved():
    """No basis_* params: Wall/Slab stay Unresolved with Unresolved totals."""
    project = _wall_slab_project()
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    slab = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcSlab")
    assert wall["basis_unresolved"] is True
    assert slab["basis_unresolved"] is True
    assert wall["total_display"] == "Unresolved"
    assert slab["total_display"] == "Unresolved"
    assert wall["review_status"] == "Missing basis rule"
    assert ui["unresolved_register"]["missing_quantity_basis_rule"] >= 2


@pytest.mark.django_db
def test_valid_wall_net_area_override_updates_prep_and_aggregates():
    """basis_IfcWall=NetArea derives Wall total and updates register/viz/insights."""
    project = _wall_slab_project()
    quantities = ModelQuantitiesService(project).build()
    default_ui = build_preparation_ui(quantities)
    default_basis_missing = default_ui["unresolved_register"]["missing_quantity_basis_rule"]

    overrides = parse_basis_overrides_from_query({"basis_IfcWall": "NetArea"})
    assert overrides == {"IfcWall": "NetArea"}
    ui = build_preparation_ui(quantities, basis_overrides=overrides)

    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    slab = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcSlab")
    assert wall["quantity_source"] == "NetArea"
    assert wall["quantity_basis"] == "NetArea"
    assert wall["unit_basis"] == "model area units"
    assert wall["total"] == 12.5
    assert wall["total_display"] == 12.5
    assert wall["basis_unresolved"] is False
    assert wall["missing_quantity_source"] is False
    assert wall["review_status"] == "Missing classification"
    # Slab still default-unresolved without its own override
    assert slab["basis_unresolved"] is True
    assert slab["total_display"] == "Unresolved"

    assert ui["unresolved_register"]["missing_quantity_basis_rule"] == default_basis_missing - 1
    assert any(i["label"] == "NetArea" for i in ui["visual_summary"]["quantity_basis_distribution"])
    insights = {c["id"]: c for c in ui["preparation_insights"]}
    assert insights["measurement_rules_needed"]["count"] == default_basis_missing - 1
    rules = {r["model_group"]: r for r in ui["basis_rules"]}
    assert rules["IfcWall"]["quantity_basis"] == "NetArea"
    assert rules["IfcWall"]["needs_basis_action"] is False


@pytest.mark.django_db
def test_valid_slab_net_volume_override_updates_slab_row():
    """basis_IfcSlab=NetVolume updates Slab when NetVolume exists."""
    project = _wall_slab_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcSlab": "NetVolume"},
    )
    slab = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcSlab")
    assert slab["quantity_basis"] == "NetVolume"
    assert slab["unit_basis"] == "model volume units"
    assert slab["total"] == 8.0
    assert slab["basis_unresolved"] is False


@pytest.mark.django_db
def test_unavailable_basis_shows_missing_selected_source_without_invented_total():
    """Selected Length on Wall with no length → Missing selected quantity source."""
    project = _wall_slab_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "Length"},
    )
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    assert wall["quantity_source"] == "Length"
    assert wall["quantity_basis"] == "Length"
    assert wall["unit_basis"] == "model length units"
    assert wall["total"] is None
    assert wall["total_display"] == "—"
    assert wall["missing_quantity_source"] is True
    assert wall["review_status"] == "Missing selected quantity source"
    assert wall["basis_unresolved"] is False


@pytest.mark.django_db
def test_invalid_override_is_ignored_safely():
    """Invalid basis value does not crash and does not appear as selected basis."""
    project = _wall_slab_project()
    overrides = parse_basis_overrides_from_query({"basis_IfcWall": "Foo"})
    assert overrides == {}
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides=overrides,
    )
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    assert wall["basis_unresolved"] is True
    assert wall["quantity_basis"] == ""
    assert "Foo" not in str(ui["basis_rules"])
    assert all(r["quantity_basis"] != "Foo" for r in ui["basis_rules"])


@pytest.mark.django_db
def test_count_basis_uses_element_count(client):
    """Count basis uses element_count with unit basis count."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-C1",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-C2",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.5},
    )
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "Count"},
    )
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    assert wall["quantity_basis"] == "Count"
    assert wall["unit_basis"] == "count"
    assert wall["total"] == 2
    assert wall["missing_quantity_source"] is False


@pytest.mark.django_db
def test_page_get_overrides_and_boundaries(client):
    """GET basis_* regenerates page; handoff disabled; forbidden labels absent."""
    project = _wall_slab_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})

    default_html = client.get(url).content.decode()
    assert 'data-qty-ifc-class="IfcWall"' in default_html
    assert 'data-qty-basis-unresolved="1"' in default_html
    assert "Generate Preparation Data Model" in default_html
    assert 'name="basis_IfcWall"' in default_html
    assert 'name="basis_IfcSlab"' in default_html

    overridden = client.get(
        url,
        {"basis_IfcWall": "NetArea", "basis_IfcSlab": "NetVolume"},
    ).content.decode()
    # Match full prep row by ifc class attribute (unresolved flag precedes ifc class).
    wall_attr_pos = overridden.index('data-qty-ifc-class="IfcWall"')
    wall_tr_start = overridden.rfind("<tr", 0, wall_attr_pos)
    wall_chunk = overridden[wall_tr_start : overridden.index("</tr>", wall_attr_pos)]
    assert 'data-qty-basis-unresolved="0"' in wall_chunk
    assert "NetArea" in wall_chunk
    assert "12.5" in wall_chunk
    assert "Missing basis rule" not in wall_chunk
    assert "Missing classification" in wall_chunk

    slab_attr_pos = overridden.index('data-qty-ifc-class="IfcSlab"')
    slab_tr_start = overridden.rfind("<tr", 0, slab_attr_pos)
    slab_chunk = overridden[slab_tr_start : overridden.index("</tr>", slab_attr_pos)]
    assert 'data-qty-basis-unresolved="0"' in slab_chunk
    assert "NetVolume" in slab_chunk
    assert "8.0" in slab_chunk or "8" in slab_chunk

    invalid = client.get(url, {"basis_IfcWall": "Foo"}).content.decode()
    assert (
        "Foo"
        not in invalid.split('data-testid="quantities-measurement-rules"', 1)[1].split(
            'data-testid="quantities-prep-table"', 1
        )[0]
    )
    assert 'data-qty-basis-unresolved="1"' in invalid

    page = overridden.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    for phrase in (
        "With IFC Qto",
        "Missing IFC Qto",
        "Quantity Coverage",
        "Qto Coverage",
        "Model Quantity Readiness",
        "Save configuration",
    ):
        assert phrase not in page, phrase
    page_l = page.lower()
    # Negation copy is allowed; positive readiness claims are not.
    assert "not 5d readiness" in page_l
    assert "not boq readiness" in page_l
    assert "not qs readiness" in page_l
    assert "Ask chat" not in page
    assert "PDF upload" not in page
    assert "Excel upload" not in page
    assert 'type="file"' not in page
    assert "ModificationProposal" not in page
    assert "machine learning" not in page.lower()
    handoff_btn = overridden.split('data-testid="qty-send-unresolved-to-modify"', 1)[0]
    handoff_open = handoff_btn.rfind("<button")
    handoff_tag = handoff_btn[handoff_open:]
    assert "disabled" in handoff_tag
    assert "href=" not in handoff_tag
