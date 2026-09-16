# takeoff/tests/test_qto_measurement11b.py
"""QTO-MEASUREMENT-11B — IFC-native units, conservation, partial coverage."""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.measurement_resolver import (
    _coerce_present_number,
    inventory_from_aggregate_row,
)
from takeoff.services.quantity_measurement_settings import (
    apply_class_settings,
    attach_selected_source_coverage,
    build_class_settings_rows,
)
from takeoff.services.quantity_output_units import (
    QuantityOutputUnitsService,
    apply_output_conversion_to_row,
    resolve_original_unit,
)
from takeoff.services.quantity_prep_row_measurement import apply_resolution_fields
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _user(project, tag: str):
    user = get_user_model().objects.create_user(
        username=f"m11b_{tag}_{uuid.uuid4().hex[:8]}",
        password="x",
        email=f"m11b_{tag}_{uuid.uuid4().hex[:8]}@example.com",
    )
    project.owner = user
    project.save(update_fields=["owner"])
    return user


def _slab_project(*, project_units: dict | None = None, tag: str = "a"):
    project = ProjectFactory()
    user = _user(project, tag)
    units = (
        project_units
        if project_units is not None
        else {"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"}
    )
    ifc = IFCFileFactory(project=project, status="completed", project_units=units)
    et = IFCElementTypeFactory(ifc_file=ifc, name="SlabA", global_id="TGIDM11BA00001")
    # Full-precision values that drift if rounded per-instance then summed.
    vals = [1.114, 2.224, 3.334]  # sum=6.672; sum(round2)=6.67; round(sum,2)=6.67
    # Use values where sum(round(v,2)) != round(sum,2)
    vals = [0.115, 0.115, 0.115]  # sum=0.345 → round 0.35; sum of round2=0.36
    for i, v in enumerate(vals, start=1):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcSlab",
            name=f"S{i}",
            global_id=f"GIDM11BSLAB{i:04d}",
            element_type=et,
            properties={"Qto_SlabBaseQuantities.NetVolume": v},
        )
    # Fourth missing NetVolume
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        name="Smiss",
        global_id="GIDM11BSLABMISS",
        element_type=et,
        properties={},
    )
    return project, user, ifc, vals


@pytest.mark.django_db
def test_project_unit_fallback_and_no_invention():
    """Declared VOLUMEUNIT → m³; empty project_units → unknown, no invented m³."""
    project, user, ifc, _vals = _slab_project(tag="pu")
    resolved = resolve_original_unit(project=project, family="volume")
    assert resolved["known"] is True
    assert resolved["label"] == "m³"
    assert resolved["source"] == "ifc_project_units"

    bare = ProjectFactory()
    user_b = get_user_model().objects.create_user(
        username=f"m11b_bare_{uuid.uuid4().hex[:8]}",
        password="x",
        email=f"m11b_bare_{uuid.uuid4().hex[:8]}@example.com",
    )
    bare.owner = user_b
    bare.save(update_fields=["owner"])
    IFCFileFactory(project=bare, status="completed", project_units={})
    unk = resolve_original_unit(project=bare, family="volume")
    assert unk["known"] is False
    assert "Unknown" in unk["label"]

    row = {
        "measurement_type": "volume",
        "model_unit_family": "volume",
        "measurement_status": "resolved",
        "total": 1.0,
        "model_unit_label": "m³",  # stale invented label must not win
    }
    apply_output_conversion_to_row(row, model_units={"volume": ""}, output_units={})
    assert row["source_unit_known"] is False
    assert "Unknown" in row["model_unit_label"]
    assert row.get("output_total") is None


@pytest.mark.django_db
def test_measurement_settings_unit_matches_table_via_shared_path():
    """Modal source unit for Volume uses project units — same as table after apply."""
    project, user, ifc, _vals = _slab_project(tag="match")
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    cards = build_class_settings_rows(
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=runtime["qty_prep"].get("prep_rows_export"),
        units_panel=runtime["qty_prep"].get("output_units") or {},
        project=project,
    )
    slab = next(c for c in cards if c["ifc_class"] == "IfcSlab")
    vol_unit = slab["source_units_by_mt"]["volume"]
    assert vol_unit["known"] is True
    assert vol_unit["label"] == "m³"

    apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=runtime["qty_prep"].get("prep_rows_export"),
        known_target_keys={
            str(r.get("measurement_target_key") or "")
            for r in (runtime["qty_prep"].get("prep_rows_export") or [])
            if r.get("measurement_target_key")
        },
    )
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    class_row = next(r for r in runtime2["qty_prep"]["prep_rows"] if r.get("level") == "class")
    assert class_row["model_unit_label"] == "m³"
    assert class_row["source_unit_known"] is True
    assert class_row.get("quantity_coverage_label")
    assert "3/4" in class_row["quantity_coverage_label"]
    assert "1 missing" in class_row["quantity_coverage_label"]
    assert "elements" not in class_row["quantity_coverage_label"]
    assert class_row.get("review_status") == "Partial"
    assert class_row.get("review_status_display") == "Partial"


@pytest.mark.django_db
def test_full_precision_conservation_not_sum_of_rounded():
    """Class total = round(sum(raw)), not sum(round(instance))."""
    project, user, ifc, vals = _slab_project(tag="prec")
    full = sum(vals)
    sum_rounded = sum(round(v, 2) for v in vals)
    assert round(full, 2) != round(sum_rounded, 2)

    session = SessionStore()
    session.create()
    apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=[],
        inventory_rows=[],
        hierarchy_tree=None,
        known_target_keys=set(),
    )
    # Apply needs real rows — use runtime
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    export = [r for r in (runtime["qty_prep"].get("prep_rows_export") or []) if isinstance(r, dict)]
    keys = {
        str(r.get("measurement_target_key") or "")
        for r in export
        if r.get("measurement_target_key")
    }
    apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    class_row = next(r for r in runtime2["qty_prep"]["prep_rows"] if r.get("level") == "class")
    # model_total keeps full precision; display rounds once
    assert class_row.get("model_total") is not None
    assert abs(float(class_row["model_total"]) - full) < 1e-9
    assert class_row["total_display"] == "0.35"  # round(0.345, 2)
    # Coerce no longer rounds
    assert _coerce_present_number(0.115) == 0.115


@pytest.mark.django_db
def test_output_conversion_and_reset_restore_original():
    project, user, ifc, vals = _slab_project(tag="conv")
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
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
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm3",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        known_target_keys=keys,
    )
    r2 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    class_row = next(r for r in r2["qty_prep"]["prep_rows"] if r.get("level") == "class")
    assert class_row["unit_converted"] is True
    assert class_row["output_unit"] == "mm3"
    assert abs(float(class_row["model_total"]) - sum(vals)) < 1e-9

    QuantityOutputUnitsService(project, user, session).reset_class_output_units(ifc_class="IfcSlab")
    r3 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,unit,status",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    class_row3 = next(r for r in r3["qty_prep"]["prep_rows"] if r.get("level") == "class")
    assert class_row3["output_unit"] == "m3"
    assert class_row3["unit_converted"] is False
    assert class_row3["total_display"] == "0.35"


@pytest.mark.django_db
def test_coverage_attach_partial_and_zero_vs_missing():
    rows = [
        {
            "level": "class",
            "ifc_class": "IfcSlab",
            "node_key": "c1",
            "element_count": 3,
            "selected_source": "NetVolume",
            "measurement_status": "resolved",
            "measure_inventory": {"NetVolume": 5.0},
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "parent_key": "t1",
            "measure_inventory": {"NetVolume": 0.0},
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "parent_key": "t1",
            "measure_inventory": {"NetVolume": 2.0},
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "parent_key": "t1",
            "measure_inventory": {},
        },
    ]
    attach_selected_source_coverage(rows, inventory_rows=rows)
    assert rows[0]["quantity_coverage_present"] == 2
    assert rows[0]["quantity_coverage_missing"] == 1
    assert rows[0]["quantity_coverage_partial"] is True
    assert "2/3" in rows[0]["quantity_coverage_label"]
    assert "1 missing" in rows[0]["quantity_coverage_label"]
    assert rows[0].get("review_status") == "Partial"
    # Zero is present
    assert inventory_from_aggregate_row(rows[1]).get("NetVolume") == 0.0


@pytest.mark.django_db
def test_full_coverage_omits_nn_label():
    rows = [
        {
            "level": "class",
            "ifc_class": "IfcSlab",
            "node_key": "c1",
            "ifc_quantity_source": "NetVolume",
            "review_status": "Resolved",
            "review_status_display": "Resolved",
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "parent_key": "c1",
            "measure_inventory": {"NetVolume": 1.0},
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "parent_key": "c1",
            "measure_inventory": {"NetVolume": 2.0},
        },
    ]
    attach_selected_source_coverage(rows, inventory_rows=rows)
    assert rows[0]["quantity_coverage_partial"] is False
    assert rows[0]["quantity_coverage_label"] == ""
    assert rows[0]["review_status"] == "Resolved"


@pytest.mark.django_db
def test_apply_resolution_does_not_invent_unit_label():
    row = {
        "element_count": 1,
        "measure_inventory": {"NetVolume": 1.5},
        "basis_unresolved": False,
    }
    apply_resolution_fields(row, measurement_type="volume", selected_source="NetVolume")
    assert row["model_unit_family"] == "volume"
    assert row["model_unit_label"] == "—"
    assert row["total"] == 1.5
