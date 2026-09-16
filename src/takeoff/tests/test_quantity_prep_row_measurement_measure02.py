# takeoff/tests/test_quantity_prep_row_measurement_measure02.py
"""R5D-QTO-MEASURE-02 — wire measurement into prep table / session."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _project_with_beam_and_stair():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et_rib = IFCElementTypeFactory(
        ifc_file=ifc, name="RibBeam", ifc_type="IfcBeamType", global_id="ET-RIB"
    )
    et_bu = IFCElementTypeFactory(
        ifc_file=ifc, name="BUBeam", ifc_type="IfcBeamType", global_id="ET-BU"
    )
    et_stair = IFCElementTypeFactory(
        ifc_file=ifc, name="StairX", ifc_type="IfcStairType", global_id="ET-ST"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-RIB",
        element_type=et_rib,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 960.29,
            "Qto_BeamBaseQuantities.GrossVolume": 472.24,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-BU",
        element_type=et_bu,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 5.43,
            "Qto_BeamBaseQuantities.GrossVolume": 5.39,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcStair",
        global_id="GID-ST",
        element_type=et_stair,
        properties={},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        global_id="GID-SL",
        element_type=IFCElementTypeFactory(
            ifc_file=ifc, name="FloorA", ifc_type="IfcSlabType", global_id="ET-SL"
        ),
        properties={"Qto_SlabBaseQuantities.NetArea": 12.5},
    )
    return project


def _beam_query(**extra: str) -> dict[str, str]:
    """Hierarchy with classes expanded so type rows are measurable in prep_rows."""
    q = {"basis_IfcBeam": "NetVolume", "hierarchy_expand": "all"}
    q.update({k: str(v) for k, v in extra.items()})
    return q


def _all_rows(runtime: dict) -> list:
    """Visible prep rows (types/classes) plus export instances when needed."""
    visible = list(runtime.get("qty_prep", {}).get("prep_rows") or [])
    export = list(runtime.get("qty_prep", {}).get("prep_rows_export") or [])
    # Prefer visible (has measurement fields); fall back to export.
    return visible or export


def _row_by_type(runtime: dict, type_name: str) -> dict:
    rows = [r for r in _all_rows(runtime) if r.get("type_name") == type_name]
    for level in ("type", "instance", "class"):
        hit = next((r for r in rows if r.get("level") == level), None)
        if hit and "measurement_type" in hit:
            return hit
    for hit in rows:
        if "measurement_type" in hit:
            return hit
    raise StopIteration(type_name)


def _row_by_mt(runtime: dict, mt_key: str) -> dict:
    return next(r for r in _all_rows(runtime) if r.get("measurement_target_key") == mt_key)


def _row_by_key(runtime: dict, row_key: str) -> dict:
    return next(r for r in _all_rows(runtime) if r.get("row_key") == row_key)


@pytest.mark.django_db
def test_measurement_and_source_render_separately(client):
    """Prep table exposes Measurement + IFC Source columns (not dual identical basis)."""
    project = _project_with_beam_and_stair()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "basis_IfcBeam": "NetVolume",
            "hierarchy_expand": "all",
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,measurement,ifc_source,unit,status,actions",
        },
    ).content.decode()
    assert 'data-testid="qty-prep-col-measurement"' in html
    assert 'data-testid="qty-prep-col-ifc_source"' in html
    assert 'data-testid="qty-prep-col-unit"' in html
    assert 'data-testid="qty-prep-col-measurement-basis"' not in html


@pytest.mark.django_db
def test_count_auto_resolves_and_volume_choice_required():
    """Count auto-resolves; Volume with Net+Gross requires choice until selected."""
    project = _project_with_beam_and_stair()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib = _row_by_type(runtime, "RibBeam")
    assert rib["measurement_type"] == "volume"
    assert rib["measurement_status"] == "resolved"
    assert rib["ifc_quantity_source"] == "NetVolume"
    assert rib["total"] == 960.29
    assert rib["model_unit_label"] == "m³"

    svc = QuantityPrepRowMeasurementService(project, project.owner, session)
    svc.apply_choice(
        measurement_target_key=rib["measurement_target_key"],
        measurement_type="volume",
        selected_source="",
        known_target_keys={rib["measurement_target_key"]},
    )
    runtime2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib2 = _row_by_mt(runtime2, rib["measurement_target_key"])
    assert rib2["measurement_status"] == "choice_required"
    assert rib2["total"] is None
    assert "NetVolume" in rib2["compatible_sources"]
    assert "GrossVolume" in rib2["compatible_sources"]

    svc.apply_choice(
        measurement_target_key=rib["measurement_target_key"],
        measurement_type="count",
        known_target_keys={rib["measurement_target_key"]},
    )
    runtime3 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib3 = _row_by_mt(runtime3, rib["measurement_target_key"])
    assert rib3["measurement_status"] == "resolved"
    assert rib3["total"] == 1
    assert rib3["model_unit_label"] == "count"


@pytest.mark.django_db
def test_explicit_net_and_gross_update_totals():
    """Explicit Net/Gross volume selections update totals correctly."""
    project = _project_with_beam_and_stair()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib = _row_by_type(runtime, "RibBeam")
    key = rib["measurement_target_key"]
    row_key_before = rib["row_key"]
    svc = QuantityPrepRowMeasurementService(project, project.owner, session)
    svc.apply_choice(
        measurement_target_key=key,
        measurement_type="volume",
        selected_source="GrossVolume",
        known_target_keys={key},
    )
    r2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib2 = _row_by_mt(r2, key)
    assert rib2["total"] == 472.24
    assert rib2["ifc_quantity_source"] == "GrossVolume"
    assert rib2["row_key"] == row_key_before

    svc.apply_choice(
        measurement_target_key=key,
        measurement_type="volume",
        selected_source="NetVolume",
        known_target_keys={key},
    )
    r3 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib3 = _row_by_mt(r3, key)
    assert rib3["total"] == 960.29


@pytest.mark.django_db
def test_area_and_missing_and_zero():
    """Area resolves; missing shows unavailable not zero; zero preserved via resolver path."""
    project = _project_with_beam_and_stair()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={"hierarchy_expand": "all"}
    )
    slab = _row_by_type(runtime, "FloorA")
    stair = _row_by_type(runtime, "StairX")
    svc = QuantityPrepRowMeasurementService(project, project.owner, session)
    svc.apply_choice(
        measurement_target_key=slab["measurement_target_key"],
        measurement_type="area",
        selected_source="NetArea",
        known_target_keys={slab["measurement_target_key"]},
    )
    svc.apply_choice(
        measurement_target_key=stair["measurement_target_key"],
        measurement_type="volume",
        known_target_keys={stair["measurement_target_key"]},
    )
    r2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={"hierarchy_expand": "all"}
    )
    slab2 = _row_by_mt(r2, slab["measurement_target_key"])
    stair2 = _row_by_mt(r2, stair["measurement_target_key"])
    assert slab2["total"] == 12.5
    assert slab2["model_unit_label"] == "m²"
    assert stair2["measurement_status"] == "unavailable"
    assert stair2["total"] is None
    assert stair2["total_display"] == "Not available"


@pytest.mark.django_db
def test_session_survives_rebuild_and_independent_beams():
    """Session choice survives rebuild; two IfcBeam rows stay independent."""
    project = _project_with_beam_and_stair()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib = _row_by_type(runtime, "RibBeam")
    bu = _row_by_type(runtime, "BUBeam")
    assert rib["measurement_target_key"] != bu["measurement_target_key"]
    svc = QuantityPrepRowMeasurementService(project, project.owner, session)
    svc.apply_choice(
        measurement_target_key=rib["measurement_target_key"],
        measurement_type="volume",
        selected_source="GrossVolume",
        known_target_keys={rib["measurement_target_key"], bu["measurement_target_key"]},
    )
    svc.apply_choice(
        measurement_target_key=bu["measurement_target_key"],
        measurement_type="count",
        known_target_keys={rib["measurement_target_key"], bu["measurement_target_key"]},
    )
    r2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=_beam_query()
    )
    rib2 = _row_by_mt(r2, rib["measurement_target_key"])
    bu2 = _row_by_mt(r2, bu["measurement_target_key"])
    assert rib2["total"] == 472.24
    assert bu2["total"] == 1
    assert bu2["measurement_type"] == "count"


@pytest.mark.django_db
def test_mapping_survives_measurement_change():
    """Assign Values mapping keyed by legacy row_key survives measurement change."""
    project = _project_with_beam_and_stair()
    session: dict = {}
    query = _beam_query(source_classification_code="manual_field")
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=query
    )
    rib = _row_by_type(runtime, "RibBeam")
    row_key = rib["row_key"]
    map_svc = QuantityPrepRowMappingService(project, project.owner, session)
    result = map_svc.apply_values(
        row_key=row_key,
        values={"classification_code": "CLS-1"},
        eligible_keys={"classification_code"},
        known_row_keys={row_key},
    )
    assert result.get("error") is None
    m_svc = QuantityPrepRowMeasurementService(project, project.owner, session)
    m_svc.apply_choice(
        measurement_target_key=rib["measurement_target_key"],
        measurement_type="volume",
        selected_source="GrossVolume",
        known_target_keys={rib["measurement_target_key"]},
    )
    r2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=query
    )
    rib2 = _row_by_key(r2, row_key)
    assert rib2["classification_code"] == "CLS-1"
    assert rib2["total"] == 472.24
    assert rib2["row_key"] == row_key


@pytest.mark.django_db
def test_measurement_post_endpoint(client):
    """HTMX/POST measurement endpoint updates session and redirects."""
    project = _project_with_beam_and_stair()
    client.force_login(project.owner)
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query=_beam_query(),
    )
    rib = _row_by_type(runtime, "RibBeam")
    url = reverse("takeoff:qty_prep_row_measurement", kwargs={"pk": project.pk})
    resp = client.post(
        url,
        {
            "action": "apply",
            "measurement_target_key": rib["measurement_target_key"],
            "measurement_type": "volume",
            "selected_source": "GrossVolume",
            "return_query": "basis_IfcBeam=NetVolume&hierarchy_expand=all",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 204
    assert "HX-Redirect" in resp
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query=_beam_query(),
    )
    rib2 = _row_by_mt(runtime2, rib["measurement_target_key"])
    assert rib2["total"] == 472.24
