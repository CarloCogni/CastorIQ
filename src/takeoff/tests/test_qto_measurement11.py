# takeoff/tests/test_qto_measurement11.py
"""QTO-MEASUREMENT-11 — class source discovery and hierarchy propagation."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_hierarchy import build_quantity_hierarchy
from takeoff.services.quantity_measurement_settings import (
    apply_class_settings,
    build_class_settings_rows,
    discover_class_source_coverage,
)
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _slab_project(*, with_volume: bool = True, unloaded_extra: bool = False, tag: str = "a"):
    import uuid

    project = ProjectFactory()
    user = get_user_model().objects.create_user(
        username=f"m11_{tag}_{uuid.uuid4().hex[:8]}", password="x"
    )
    project.owner = user
    project.save(update_fields=["owner"])
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et_a = IFCElementTypeFactory(ifc_file=ifc, name="SlabA", global_id="TGIDSLABA00001")
    et_b = IFCElementTypeFactory(ifc_file=ifc, name="SlabA", global_id="TGIDSLABB00001")
    props_a = {"Qto_SlabBaseQuantities.NetVolume": 10.0} if with_volume else {}
    props_b = {"Qto_SlabBaseQuantities.GrossVolume": 4.0} if with_volume else {}
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        name="S1",
        global_id="GIDSLAB00000001",
        element_type=et_a,
        properties=props_a,
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        name="S2",
        global_id="GIDSLAB00000002",
        element_type=et_b,
        properties=props_b,
    )
    if unloaded_extra:
        et_c = IFCElementTypeFactory(ifc_file=ifc, name="SlabC", global_id="TGIDSLABC00001")
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcSlab",
            name="S3",
            global_id="GIDSLAB00000003",
            element_type=et_c,
            properties={"Qto_SlabBaseQuantities.NetVolume": 7.0},
        )
    return project, user, ifc


@pytest.mark.django_db
def test_volume_sources_exist_when_legacy_basis_unresolved():
    project, user, ifc = _slab_project(with_volume=True)
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,status,actions",
            "semantic_classes": "IfcSlab",
        },
        ifc_file=ifc,
    )
    ms = runtime["qty_prep"]["measurement_settings"]
    slab = next(r for r in ms["class_rows"] if r["ifc_class"] == "IfcSlab")
    assert "NetVolume" in slab["compatible_sources"]["volume"]
    assert "GrossVolume" in slab["compatible_sources"]["volume"]
    assert slab["element_count"] >= 2


@pytest.mark.django_db
def test_sources_include_unloaded_descendants():
    project, user, ifc = _slab_project(with_volume=True, unloaded_extra=True)
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,status,actions",
            "semantic_classes": "IfcSlab",
            "hierarchy_expand": "none",
        },
        ifc_file=ifc,
    )
    visible = [
        r
        for r in runtime["qty_prep"]["prep_rows"]
        if r.get("ifc_class") == "IfcSlab" and not r.get("is_load_more")
    ]
    assert len(visible) == 1 and visible[0].get("level") == "class"
    slab = next(
        r
        for r in runtime["qty_prep"]["measurement_settings"]["class_rows"]
        if r["ifc_class"] == "IfcSlab"
    )
    assert slab["element_count"] == 3
    assert "NetVolume" in slab["compatible_sources"]["volume"]
    cov = slab["source_coverage"]["volume"]["NetVolume"]
    assert cov["present"] >= 2


@pytest.mark.django_db
def test_same_name_types_stay_identity_separated_on_apply():
    project, user, ifc = _slab_project(with_volume=True)
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query={}, ifc_file=ifc
    )
    tree = runtime["qty_prep"].get("_hierarchy_tree") or build_quantity_hierarchy(
        project=project, ifc_file=ifc
    )
    result = apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=list(runtime["qty_prep"]["prep_rows"] or []),
        inventory_rows=list(runtime["qty_prep"].get("prep_rows_export") or []),
        hierarchy_tree=tree,
    )
    assert result["ok"] is True
    choices = QuantityPrepRowMeasurementService(project, user, session).get_choices()
    type_keys = [
        k
        for k in choices
        if k.startswith("mt1|type|IfcSlab|") and choices[k]["selected_source"] == "NetVolume"
    ]
    assert len(type_keys) >= 2


def test_partial_coverage_and_zero_vs_missing():
    rows = [
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "global_id": "A",
            "element_count": 1,
            "measure_inventory": {"NetVolume": 0.0, "GrossVolume": None},
        },
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "global_id": "B",
            "element_count": 1,
            "measure_inventory": {"NetVolume": None, "GrossVolume": 2.0},
        },
    ]
    cov = discover_class_source_coverage(rows)
    assert cov["volume"]["sources"]["NetVolume"]["present"] == 1
    assert cov["volume"]["sources"]["GrossVolume"]["present"] == 1
    assert cov["volume"]["sources"]["NetVolume"]["partial"] is True


def test_no_compatible_source_state():
    rows = [
        {
            "level": "instance",
            "ifc_class": "IfcSlab",
            "global_id": "A",
            "element_count": 1,
            "measure_inventory": {"Length": 1.0},
        }
    ]
    built = build_class_settings_rows(prep_rows=list(rows), inventory_rows=list(rows))
    slab = built[0]
    assert slab["compatible_sources"]["volume"] == []
    assert "Length" in slab["compatible_sources"]["length"]


@pytest.mark.django_db
def test_apply_disabled_path_rejects_missing_source():
    project, user, ifc = _slab_project(with_volume=False)
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query={}, ifc_file=ifc
    )
    result = apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=list(runtime["qty_prep"]["prep_rows"] or []),
        inventory_rows=list(runtime["qty_prep"].get("prep_rows_export") or []),
        hierarchy_tree=runtime["qty_prep"].get("_hierarchy_tree"),
    )
    assert result["ok"] is False
    assert "No compatible" in (result.get("error") or "")


@pytest.mark.django_db
def test_generic_count_length_area_volume_catalogue():
    project = ProjectFactory()
    user = get_user_model().objects.create_user(username="m11g", password="x")
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et = IFCElementTypeFactory(ifc_file=ifc, name="WallT", global_id="TGIDWALL000001")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GIDWALL00000001",
        element_type=et,
        properties={
            "Qto_WallBaseQuantities.Length": 5.0,
            "Qto_WallBaseQuantities.NetArea": 12.0,
            "Qto_WallBaseQuantities.GrossArea": 13.0,
            "Qto_WallBaseQuantities.NetVolume": 1.5,
            "Qto_WallBaseQuantities.GrossVolume": 1.6,
        },
    )
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query={}, ifc_file=ifc
    )
    wall = next(
        r
        for r in runtime["qty_prep"]["measurement_settings"]["class_rows"]
        if r["ifc_class"] == "IfcWall"
    )
    assert wall["compatible_sources"]["count"] == ["element_count"]
    assert "Length" in wall["compatible_sources"]["length"]
    assert "NetArea" in wall["compatible_sources"]["area"]
    assert "NetVolume" in wall["compatible_sources"]["volume"]


@pytest.mark.django_db
def test_apply_conserves_class_total_from_instances():
    project, user, ifc = _slab_project(with_volume=True)
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query={}, ifc_file=ifc
    )
    tree = runtime["qty_prep"].get("_hierarchy_tree")
    apply_class_settings(
        project=project,
        user=user,
        session=session,
        ifc_class="IfcSlab",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=list(runtime["qty_prep"]["prep_rows"] or []),
        inventory_rows=list(runtime["qty_prep"].get("prep_rows_export") or []),
        hierarchy_tree=tree,
    )
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={"hierarchy_expand": "all", "semantic_classes": "IfcSlab"},
        ifc_file=ifc,
    )
    class_row = next(
        r
        for r in runtime2["qty_prep"]["prep_rows"]
        if r.get("level") == "class" and r.get("ifc_class") == "IfcSlab"
    )
    assert class_row.get("selected_source") == "NetVolume"
    assert class_row.get("total") == pytest.approx(10.0)
    export = runtime2["qty_prep"].get("prep_rows_export") or []
    by_gid = {r.get("global_id"): r for r in export}
    assert by_gid["GIDSLAB00000001"]["total"] == pytest.approx(10.0)
