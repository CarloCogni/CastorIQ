# takeoff/tests/test_qto_measurement_output_family.py
"""Measurement settings: output-unit family hydration + mismatch rejection."""

from __future__ import annotations

import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_measurement_settings import (
    apply_class_settings,
    build_class_settings_rows,
)
from takeoff.services.quantity_output_units import QuantityOutputUnitsService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _user(project, tag: str):
    user = get_user_model().objects.create_user(
        username=f"mof_{tag}_{uuid.uuid4().hex[:8]}",
        password="x",
        email=f"mof_{tag}_{uuid.uuid4().hex[:8]}@example.com",
    )
    project.owner = user
    project.save(update_fields=["owner"])
    return user


def _column_project(*, tag: str = "a", n: int = 3):
    project = ProjectFactory()
    user = _user(project, tag)
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et = IFCElementTypeFactory(ifc_file=ifc, name="ColType", global_id="TGIDMOFCOL00001")
    for i in range(1, n + 1):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            name=f"C{i}",
            global_id=f"GIDMOFCOL{i:04d}",
            element_type=et,
            properties={
                "Qto_ColumnBaseQuantities.NetVolume": 1.0 + i * 0.1,
                "Qto_ColumnBaseQuantities.Height": 3000.0,
                "Qto_ColumnBaseQuantities.NetSideArea": 2.5,
            },
        )
    # Second class so class-scoped prefs must not leak.
    et_w = IFCElementTypeFactory(ifc_file=ifc, name="WallType", global_id="TGIDMOFWALL0001")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="W1",
        global_id="GIDMOFWALL0001",
        element_type=et_w,
        properties={"Qto_WallBaseQuantities.NetVolume": 9.0},
    )
    return project, user, ifc


def _runtime(project, user, ifc, session, *, classes: str = "IfcColumn"):
    return build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": classes,
        },
        ifc_file=ifc,
    )


@pytest.mark.django_db
def test_apply_rejects_volume_unit_when_measurement_is_count():
    """Backend must reject mismatched measurement/output even if JS fails."""
    project, user, ifc = _column_project(tag="rej", n=2)
    session = SessionStore()
    session.create()
    runtime = _runtime(project, user, ifc, session)
    export = list(runtime["qty_prep"].get("prep_rows_export") or [])
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    result = apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="count",
        selected_source="element_count",
        output_unit="m3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    assert result.get("ok") is False
    assert "not" in (result.get("error") or "").lower()


@pytest.mark.django_db
def test_apply_rejects_length_unit_when_measurement_is_volume():
    project, user, ifc = _column_project(tag="rej2", n=2)
    session = SessionStore()
    session.create()
    runtime = _runtime(project, user, ifc, session)
    export = list(runtime["qty_prep"].get("prep_rows_export") or [])
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    result = apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    assert result.get("ok") is False
    assert "not" in (result.get("error") or "").lower()


@pytest.mark.django_db
def test_count_apply_preserves_volume_pref_and_other_class_units():
    """Count must not erase this class's volume preference or other classes' units."""
    project, user, ifc = _column_project(tag="keep", n=2)
    session = SessionStore()
    session.create()
    runtime = _runtime(project, user, ifc, session, classes="IfcColumn,IfcWall")
    export = list(runtime["qty_prep"].get("prep_rows_export") or [])
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    assert apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )["ok"]
    assert apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcWall",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="cm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )["ok"]

    count_result = apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="count",
        selected_source="element_count",
        output_unit="count",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    assert count_result["ok"] is True

    class_units = QuantityOutputUnitsService(project, user, session).get_class_units()
    assert class_units.get("IfcColumn", {}).get("volume") == "mm3"
    assert class_units.get("IfcWall", {}).get("volume") == "cm3"


@pytest.mark.django_db
def test_returning_to_volume_restores_class_volume_preference():
    project, user, ifc = _column_project(tag="rest", n=2)
    session = SessionStore()
    session.create()
    runtime = _runtime(project, user, ifc, session)
    export = list(runtime["qty_prep"].get("prep_rows_export") or [])
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    assert apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )["ok"]
    assert apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="count",
        selected_source="element_count",
        output_unit="count",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )["ok"]
    assert apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )["ok"]

    r2 = _runtime(project, user, ifc, session)
    class_row = next(r for r in r2["qty_prep"]["prep_rows"] if r.get("level") == "class")
    assert class_row.get("measurement_type") == "volume"
    assert class_row.get("output_unit") == "mm3"


@pytest.mark.django_db
def test_settings_rows_expose_output_choices_and_prefs_per_measurement():
    """Modal hydration data must include family choices + preferred tokens."""
    project, user, ifc = _column_project(tag="ui", n=2)
    session = SessionStore()
    session.create()
    runtime = _runtime(project, user, ifc, session)
    export = list(runtime["qty_prep"].get("prep_rows_export") or [])
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="cm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    r2 = _runtime(project, user, ifc, session)
    units_svc = QuantityOutputUnitsService(project, user, session)
    rows = build_class_settings_rows(
        prep_rows=list(r2["qty_prep"].get("prep_rows") or []),
        inventory_rows=list(r2["qty_prep"].get("prep_rows_export") or []),
        units_panel=r2["qty_prep"].get("output_units") or {},
        class_units=units_svc.get_class_units(),
        global_effective=units_svc.effective_output_units(),
        project=project,
    )
    crow = next(r for r in rows if r["ifc_class"] == "IfcColumn")
    by_mt = crow.get("output_choices_by_measurement") or {}
    prefs = crow.get("preferred_output_by_measurement") or {}
    assert [c["token"] for c in by_mt.get("volume") or []] == ["mm3", "cm3", "m3"]
    assert [c["token"] for c in by_mt.get("length") or []] == ["mm", "cm", "m"]
    assert [c["token"] for c in by_mt.get("area") or []] == ["mm2", "cm2", "m2"]
    assert by_mt.get("count") == []
    assert prefs.get("volume") == "cm3"
    assert prefs.get("count") == "count"
    # JSON attrs for the modal must round-trip.
    assert "cm3" in (crow.get("preferred_output_by_mt_json") or "")
    parsed = json.loads(crow.get("output_choices_by_mt_json") or "{}")
    assert "m3" in {c["token"] for c in parsed.get("volume") or []}
