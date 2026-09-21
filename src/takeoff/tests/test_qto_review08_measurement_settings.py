# takeoff/tests/test_qto_review08_measurement_settings.py
"""QTO-REVIEW-08 checkpoint C — combined Measurement settings."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_measurement_settings import (
    apply_class_settings,
    build_class_settings_rows,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService


def _project_with_beam():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="Rib", ifc_type="IfcBeamType", global_id="ET1")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G1",
        element_type=et,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 2.5,
            "Qto_BeamBaseQuantities.GrossVolume": 3.0,
        },
    )
    return project


@pytest.mark.django_db
def test_measurement_settings_panel_and_display_cells(client):
    project = _project_with_beam()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,measurement,ifc_source,unit,status,actions",
        },
    ).content.decode()
    assert 'data-testid="qty-measurement-settings-open"' in html
    assert 'data-testid="qty-measurement-settings-modal"' in html
    assert 'data-testid="qty-measurement-class-card"' in html
    assert 'data-testid="qty-row-measurement-select"' not in html
    assert 'data-testid="qty-row-source-select"' not in html
    assert 'data-testid="qty-prep-measurement-cell"' in html
    assert 'data-testid="qty-prep-ifc-source-cell"' in html
    assert 'data-testid="qty-prep-measurement-value"' in html or "—" in html


@pytest.mark.django_db
def test_apply_class_settings_sets_targets_without_touching_other_classes():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et_b = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamT", ifc_type="IfcBeamType", global_id="ETB"
    )
    et_c = IFCElementTypeFactory(
        ifc_file=ifc, name="ColT", ifc_type="IfcColumnType", global_id="ETC"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        element_type=et_b,
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="C1",
        element_type=et_c,
        properties={"Qto_ColumnBaseQuantities.NetVolume": 2.0},
    )
    session = SessionStore()
    session.save()
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={}
    )
    rows = list(runtime["qty_prep"]["prep_rows"] or [])
    class_rows = build_class_settings_rows(prep_rows=rows)
    assert {r["ifc_class"] for r in class_rows} >= {"IfcBeam", "IfcColumn"}

    known = {
        str(r.get("measurement_target_key") or "") for r in rows if r.get("measurement_target_key")
    }
    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=rows,
        known_target_keys=known,
    )
    assert result["ok"] is True
    assert result["affected_targets"] >= 1

    choices = QuantityPrepRowMeasurementService(project, project.owner, session).get_choices()
    beam_keys = {
        str(r["measurement_target_key"])
        for r in rows
        if r.get("ifc_class") == "IfcBeam" and r.get("measurement_target_key")
    }
    col_keys = {
        str(r["measurement_target_key"])
        for r in rows
        if r.get("ifc_class") == "IfcColumn" and r.get("measurement_target_key")
    }
    for key in beam_keys:
        assert choices[key]["measurement_type"] == "volume"
        assert choices[key]["selected_source"] == "NetVolume"
    for key in col_keys:
        assert key not in choices


@pytest.mark.django_db
def test_core_columns_are_reorderable():
    from takeoff.services.quantity_table_layout import parse_table_layout

    moved = parse_table_layout(
        {
            "col_order": "name,ifc_class,status,actions",
            "col_order_move": "status:up",
            "table_layout": "v2",
        }
    )
    assert moved["order"][0] == "name"
    assert "ifc_class" in moved["order"]
    assert "status" in moved["order"]
    # Move status up past ifc_class
    assert moved["order"].index("status") < moved["order"].index("ifc_class")
