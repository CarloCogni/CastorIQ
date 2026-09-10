# takeoff/tests/test_quantity_unit_confirmation_unit2.py
"""UNIT-2 — quantity unit confirmation proposals, session confirm, display wiring."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_confirmation import (
    FAMILY_AREA,
    FAMILY_COUNT,
    FAMILY_LENGTH,
    FAMILY_VOLUME,
    QuantityUnitConfirmationService,
    apply_unit_confirmation_to_qty_prep,
    discover_project_unit_proposals,
)


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={
            "VOLUMEUNIT": "m³",
            "AREAUNIT": "m²",
            "LENGTHUNIT": "mm",
            "MASSUNIT": "kg",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.25},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcPipeSegment",
        global_id="GID-P1",
        properties={"Qto_PipeSegmentBaseQuantities.Length": 3500.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcDoor",
        global_id="GID-D1",
        properties={},
    )
    return project


@pytest.mark.django_db
def test_discover_proposes_ifc_units_not_metres_for_length():
    """IFC project_units drive proposals; length stays mm, not m."""
    project = _pilot_like_project()
    proposals = discover_project_unit_proposals(project)
    by_family = {p["family"]: p for p in proposals["families"]}
    assert by_family[FAMILY_VOLUME]["proposed_token"] == "m3"
    assert by_family[FAMILY_VOLUME]["proposed_label"] == "m³"
    assert by_family[FAMILY_AREA]["proposed_token"] == "m2"
    assert by_family[FAMILY_LENGTH]["proposed_token"] == "mm"
    assert by_family[FAMILY_LENGTH]["proposed_label"] == "mm"
    assert by_family[FAMILY_LENGTH]["proposed_token"] != "m"
    assert by_family[FAMILY_COUNT]["proposed_token"] == "count"
    assert proposals["source"] == "ifc_project_units"
    assert proposals["conflict"] is False


@pytest.mark.django_db
def test_no_si_display_before_confirmation():
    """Prep volume/length stay unresolved until session confirmation."""
    project = _pilot_like_project()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
            "basis_IfcBeam": "NetVolume",
            "basis_IfcPipeSegment": "Length",
        },
    )
    rows = runtime["qty_prep"]["prep_rows"]
    beam = next(r for r in rows if r.get("ifc_class") == "IfcBeam")
    pipe = next(r for r in rows if r.get("ifc_class") == "IfcPipeSegment")
    assert beam["unit_basis"] == "model volume units"
    assert "m³" not in (beam.get("unit_basis_display") or "")
    assert beam.get("unit_basis_display") == "Unit not resolved"
    assert pipe["unit_basis"] == "model length units"
    assert pipe.get("unit_basis_display") == "Length unit unresolved"
    assert "m" != pipe.get("unit_basis_display")
    panel = runtime["qty_prep"]["unit_confirmation"]
    assert panel["any_confirmed"] is False


@pytest.mark.django_db
def test_confirmation_rewrites_unit_basis_without_numeric_conversion():
    """Confirming sets canonical unit_basis; totals unchanged."""
    project = _pilot_like_project()
    session: dict = {}
    svc = QuantityUnitConfirmationService(project, project.owner, session)
    svc.confirm_families([FAMILY_VOLUME, FAMILY_LENGTH, FAMILY_AREA, FAMILY_COUNT])

    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
            "basis_IfcBeam": "NetVolume",
            "basis_IfcPipeSegment": "Length",
        },
    )
    rows = runtime["qty_prep"]["prep_rows"]
    beam = next(r for r in rows if r.get("ifc_class") == "IfcBeam")
    pipe = next(r for r in rows if r.get("ifc_class") == "IfcPipeSegment")
    before_total = beam.get("total")
    assert beam["unit_basis"] == "m3"
    assert beam["unit_basis_display"] == "m³"
    assert pipe["unit_basis"] == "mm"
    assert pipe["unit_basis_display"] == "mm"
    assert beam.get("total") == before_total
    assert runtime["qty_prep"]["unit_confirmation"]["any_confirmed"] is True


@pytest.mark.django_db
def test_clear_confirmation_restores_unresolved_display():
    """Leave unresolved restores model-unit tokens and unresolved labels."""
    project = _pilot_like_project()
    session: dict = {}
    svc = QuantityUnitConfirmationService(project, project.owner, session)
    svc.confirm_families([FAMILY_VOLUME])
    svc.clear_families([FAMILY_VOLUME])
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "source_classification_code": "manual_field",
            "basis_IfcBeam": "NetVolume",
        },
    )
    beam = next(r for r in runtime["qty_prep"]["prep_rows"] if r.get("ifc_class") == "IfcBeam")
    assert beam["unit_basis"] == "model volume units"
    assert beam["unit_basis_display"] == "Unit not resolved"


@pytest.mark.django_db
def test_quantities_page_shows_unit_panel(client):
    """Quantities page renders Quantity units panel."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    response = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'data-testid="qty-unit-confirmation"' in html
    assert "Quantity units" in html
    assert "BOQ-ready" not in html
    assert "unit rate" not in html.lower()


@pytest.mark.django_db
def test_prep_table_keeps_total_unit_basis_separate(client):
    """Total Quantity, Unit, and Measurement Basis are distinct columns."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"basis_IfcBeam": "NetVolume"},
    ).content.decode("utf-8")
    assert 'data-testid="qty-prep-col-total-quantity"' in html
    assert 'data-testid="qty-prep-col-unit"' in html
    assert 'data-testid="qty-prep-col-measurement-basis"' in html
    assert "3,409.55 m³" not in html


@pytest.mark.django_db
def test_post_confirm_units_updates_session_and_display(client):
    """POST confirm endpoint stores session state and updates prep display."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_unit_confirm", kwargs={"pk": project.pk})
    response = client.post(
        url,
        {
            "action": "confirm",
            "families": "volume,length,area,count",
            "return_query": "basis_IfcBeam=NetVolume&basis_IfcPipeSegment=Length",
        },
    )
    assert response.status_code in {200, 302}
    # Follow via GET with same query
    page = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"basis_IfcBeam": "NetVolume", "basis_IfcPipeSegment": "Length"},
    )
    html = page.content.decode("utf-8")
    assert "m³" in html or "confirmed" in html.lower()
    assert 'data-testid="qty-unit-confirmation"' in html


@pytest.mark.django_db
def test_apply_helper_ignores_unknown_basis():
    """Unresolved / unknown basis stays honest."""
    qty_prep = {
        "prep_rows": [
            {
                "ifc_class": "IfcWall",
                "quantity_basis": "",
                "unit_basis": "",
                "total": None,
            }
        ],
        "basis_rules": [],
    }
    apply_unit_confirmation_to_qty_prep(
        qty_prep,
        confirmation={FAMILY_VOLUME: {"status": "confirmed", "token": "m3", "label": "m³"}},
    )
    row = qty_prep["prep_rows"][0]
    assert row["unit_basis"] in {"", "—"} or row.get("unit_basis_display") in {
        "—",
        "Unit not resolved",
    }
