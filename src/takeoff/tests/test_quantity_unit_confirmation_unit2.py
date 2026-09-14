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
    """UNIT-03: IFC model units are the default output (identity conversion)."""
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
    assert beam.get("output_unit") == "m3" or beam["unit_basis"] == "m3"
    assert beam.get("output_unit_label") == "m³" or beam.get("unit_basis_display") == "m³"
    assert pipe.get("output_unit") == "mm" or pipe["unit_basis"] == "mm"
    assert runtime["qty_prep"]["output_units"]["any_override"] is False


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
    assert beam.get("output_unit") == "m3" or beam["unit_basis"] == "m3"
    assert beam.get("output_unit_label") == "m³" or beam.get("unit_basis_display") == "m³"
    panel = runtime["qty_prep"]["unit_confirmation"]
    assert panel["any_confirmed"] is False
    assert panel["all_available_confirmed"] is False
    by_family = {r["family"]: r for r in panel["rows"]}
    assert by_family[FAMILY_VOLUME]["status"] == "available"


@pytest.mark.django_db
def test_unit_readiness_ignores_stale_payload_and_tracks_overrides():
    """Readiness confirmation requires normalized confirm/override — not bool(blob)."""
    from takeoff.services.quantity_output_units import (
        QuantityOutputUnitsService,
        _compat_unit_confirmation_panel,
    )
    from takeoff.services.quantity_unit_confirmation import (
        _normalize_stored,
        session_key_for_project,
    )

    project = _pilot_like_project()
    session: dict = {}

    # Stale / invalid session blobs must not survive get_confirmation().
    session[session_key_for_project(project.pk)] = {
        FAMILY_VOLUME: {"status": "pending", "token": "m3"},
        FAMILY_AREA: {"status": "confirmed", "token": ""},
        FAMILY_LENGTH: "m",
        "bogus": {"status": "confirmed", "token": "mm"},
    }
    assert QuantityUnitConfirmationService(project, project.owner, session).get_confirmation() == {}
    assert _normalize_stored(session[session_key_for_project(project.pk)]) == {}

    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"basis_IfcBeam": "NetVolume", "basis_IfcPipeSegment": "Length"},
    )
    panel = runtime["qty_prep"]["unit_confirmation"]
    assert panel["any_confirmed"] is False
    assert all(r["status"] in {"available", "unresolved"} for r in panel["rows"])
    assert panel["all_available_confirmed"] is False

    # Explicit confirm → confirmed; flags agree with per-family statuses.
    QuantityUnitConfirmationService(project, project.owner, session).confirm_families(
        [FAMILY_VOLUME, FAMILY_AREA, FAMILY_LENGTH, FAMILY_COUNT]
    )
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"basis_IfcBeam": "NetVolume", "basis_IfcPipeSegment": "Length"},
    )
    panel = runtime["qty_prep"]["unit_confirmation"]
    assert panel["any_confirmed"] is True
    assert panel["all_available_confirmed"] is True
    assert all((not r["available"]) or r["status"] == "confirmed" for r in panel["rows"])

    # Clear confirmation; class-scoped output override alone marks that family confirmed.
    QuantityUnitConfirmationService(project, project.owner, session).clear_families()
    QuantityOutputUnitsService(project, project.owner, session).apply_class_output_unit(
        ifc_class="IfcBeam",
        family=FAMILY_VOLUME,
        output_unit="cm3",
    )
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"basis_IfcBeam": "NetVolume", "basis_IfcPipeSegment": "Length"},
    )
    panel = runtime["qty_prep"]["unit_confirmation"]
    by_family = {r["family"]: r for r in panel["rows"]}
    assert by_family[FAMILY_VOLUME]["status"] == "confirmed"
    assert by_family[FAMILY_LENGTH]["status"] == "available"
    assert panel["any_confirmed"] is True
    assert panel["all_available_confirmed"] is False
    assert panel["any_confirmed"] == any(r["status"] == "confirmed" for r in panel["rows"])

    # Raw non-normalized Mapping must not count as confirmed inside the bridge.
    units_panel = runtime["qty_prep"]["output_units"]
    bridged_garbage_only = _compat_unit_confirmation_panel(
        {
            **units_panel,
            "class_units": {},
            "rows": [{**r, "overridden": False} for r in units_panel["rows"]],
        },
        confirmation={FAMILY_VOLUME: {"status": "garbage", "token": "m3"}},
    )
    assert all(r["status"] != "confirmed" for r in bridged_garbage_only["rows"])
    assert bridged_garbage_only["any_confirmed"] is False
    bridged_missing_token = _compat_unit_confirmation_panel(
        {
            **units_panel,
            "class_units": {},
            "rows": [{**r, "overridden": False} for r in units_panel["rows"]],
        },
        confirmation={FAMILY_LENGTH: {"status": "confirmed"}},  # missing token
    )
    assert all(r["status"] != "confirmed" for r in bridged_missing_token["rows"])
    assert bridged_missing_token["any_confirmed"] is False


@pytest.mark.django_db
def test_quantities_page_shows_unit_panel(client):
    """Units live in Measurement settings (Units modal redirects there)."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    response = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,measurement,ifc_source,unit,status,actions",
        },
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    # Obsolete inline confirmation panel is not restored.
    assert 'data-testid="qty-unit-confirmation"' not in html
    assert 'data-testid="qty-units-modal"' in html
    assert 'data-testid="qty-units-toolbar-link"' in html
    assert 'data-testid="qty-measurement-settings-modal"' in html
    assert 'data-testid="qty-measurement-settings-open"' in html
    assert 'data-testid="qty-units-open-measurement-settings"' in html
    assert "Measurement settings" in html
    assert "BOQ-ready" not in html
    assert "unit rate" not in html.lower()
    # Source/output provenance surfaces inside Measurement settings.
    assert 'data-testid="qty-measurement-settings-inherited-units"' in html
    assert "Source" in html and "Output" in html


@pytest.mark.django_db
def test_prep_table_keeps_total_unit_basis_separate(client):
    """Quantity, Measurement, IFC Source, and Unit are distinct columns."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "basis_IfcBeam": "NetVolume",
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,measurement,ifc_source,unit,status,actions",
        },
    ).content.decode("utf-8")
    assert 'data-testid="qty-prep-col-quantity"' in html
    assert 'data-testid="qty-prep-col-measurement"' in html
    assert 'data-testid="qty-prep-col-ifc_source"' in html
    assert 'data-testid="qty-prep-col-unit"' in html
    assert "3,409.55 m³" not in html


@pytest.mark.django_db
def test_post_confirm_units_updates_session_and_display(client):
    """POST confirm stores session confirmation; Measurement settings still usable."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_unit_confirm", kwargs={"pk": project.pk})
    return_q = (
        "table_layout=v2&col_order=ifc_class,name,quantity,unit,status,actions"
        "&basis_IfcBeam=NetVolume&basis_IfcPipeSegment=Length"
    )
    response = client.post(
        url,
        {
            "action": "confirm",
            "families": "volume,length,area,count",
            "return_query": return_q,
        },
    )
    assert response.status_code in {200, 302, 204}
    page = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "basis_IfcBeam": "NetVolume",
            "basis_IfcPipeSegment": "Length",
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status,actions",
        },
    )
    html = page.content.decode("utf-8")
    assert page.status_code == 200
    assert 'data-testid="qty-measurement-settings-modal"' in html
    assert "m³" in html or "m3" in html or "Model units" in html
    # Session confirmation is live for SEM-4A readiness consumers.
    from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui

    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query={
            "basis_IfcBeam": "NetVolume",
            "basis_IfcPipeSegment": "Length",
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status,actions",
        },
    )
    assert runtime["qty_prep"]["unit_confirmation"]["any_confirmed"] is True


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
