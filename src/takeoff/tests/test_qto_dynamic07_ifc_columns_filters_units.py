# takeoff/tests/test_qto_dynamic07_ifc_columns_filters_units.py
"""QTO-DYNAMIC-07 — dynamic IFC catalogue, entity filter before agg, source units."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.ifc_semantic_fields import (
    MIXED_VALUES_LABEL,
    aggregate_property_values,
    discover_indexed_property_columns,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_editable_table import (
    COLUMNS_UI_EXCLUDED_SOURCE_PROPS,
    _capture_query_state,
)
from takeoff.services.quantity_entity_filter import (
    build_entity_predicate,
    compare_values,
    infer_value_type,
)
from takeoff.services.quantity_output_units import (
    UNKNOWN_SOURCE_UNIT_LABEL,
    discover_model_units,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_conversion import convert_quantity


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et_a = IFCElementTypeFactory(
        ifc_file=ifc, name="Rib Type", ifc_type="IfcBeamType", global_id="ET-A"
    )
    et_b = IFCElementTypeFactory(
        ifc_file=ifc, name="Column Type", ifc_type="IfcColumnType", global_id="ET-B"
    )
    # Two beams same type — uniform FireRating; mixed LoadBearing across types later.
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B1",
        element_type=et_a,
        properties={
            "Pset_BeamCommon.FireRating": "R60",
            "Pset_BeamCommon.LoadBearing": True,
            "Qto_BeamBaseQuantities.NetVolume": 1.5,
            "Qto_BeamBaseQuantities.Length": 4000.0,
            "Category": "should-hide",
            "Family": "should-hide",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B2",
        element_type=et_a,
        properties={
            "Pset_BeamCommon.FireRating": "R60",
            "Pset_BeamCommon.LoadBearing": True,
            "Qto_BeamBaseQuantities.NetVolume": 2.5,
            "Qto_BeamBaseQuantities.Length": 6000.0,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="GID-C1",
        element_type=et_b,
        properties={
            "Pset_ColumnCommon.FireRating": "R90",
            "Qto_ColumnBaseQuantities.NetVolume": 10.0,
            "Qto_ColumnBaseQuantities.Length": 3000.0,
        },
    )
    return project


@pytest.mark.django_db
def test_catalogue_includes_real_pset_and_qto_not_category_family():
    project = _pilot_like_project()
    from takeoff.services.quantity_field_catalogue import build_lazy_field_catalogue_context

    ctx = build_lazy_field_catalogue_context(project=project, query={}, mode="filter")
    # Flatten hierarchy back to field keys for assertions.
    keys: set[str] = set()
    props: set[str] = set()
    for family in ctx.get("hierarchy") or []:
        for group in family.get("groups") or []:
            for field in group.get("fields") or []:
                keys.add(str(field.get("key") or ""))
                if field.get("source_property"):
                    props.add(str(field["source_property"]))
    assert any(k.startswith("prop:") for k in keys)
    assert any("FireRating" in (p or "") for p in props)
    assert any("Qto_" in (p or "") and "NetVolume" in (p or "") for p in props)
    assert "Category" not in props
    assert "Family" not in props
    for excluded in COLUMNS_UI_EXCLUDED_SOURCE_PROPS:
        assert excluded not in props


@pytest.mark.django_db
def test_added_column_uniform_multiple_zero_and_missing():
    assert aggregate_property_values([0, 0])["display"] == "0"
    assert aggregate_property_values([None, None])["display"] == "—"
    mixed = aggregate_property_values(["R60", "R90"])
    assert mixed["display"] == MIXED_VALUES_LABEL
    assert "R60" in mixed["detail_values"]


@pytest.mark.django_db
def test_entity_filter_before_by_type_aggregation():
    project = _pilot_like_project()
    pred = build_entity_predicate(
        field_key="prop:Pset_BeamCommon.FireRating",
        op="eq",
        value="R60",
        value_type="text",
    )
    assert pred is not None
    payload = ModelQuantitiesService(project).build(entity_predicate=pred)
    type_rows = payload.get("by_type") or []
    names = {r.get("type_name") for r in type_rows}
    assert "Rib Type" in names
    assert "Column Type" not in names
    rib = next(r for r in type_rows if r["type_name"] == "Rib Type")
    assert rib["element_count"] == 2
    # Volumes 1.5 + 2.5 only (column 10 excluded)
    assert abs(float(rib.get("net_volume") or 0) - 4.0) < 1e-6


@pytest.mark.django_db
def test_numeric_comparison_not_lexicographic():
    assert compare_values(actual="10", op="gt", expected="2", value_type="numeric")
    assert not compare_values(actual="10", op="gt", expected="2", value_type="text")


@pytest.mark.django_db
def test_filter_field_change_clears_stale_via_query_parse():
    project = _pilot_like_project()
    session = SessionStore()
    # Stale beam class value must not apply when field is FireRating with empty value.
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"semantic_field": "prop:Pset_BeamCommon.FireRating", "semantic_value": ""},
    )
    sem = runtime["qty_prep"]["semantic_filters"]
    assert sem.get("filter_active") is False


@pytest.mark.django_db
def test_runtime_filter_chip_and_op_persist_capture():
    project = _pilot_like_project()
    session = SessionStore()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "semantic_field": "prop:Pset_BeamCommon.FireRating",
            "semantic_op": "eq",
            "semantic_value": "R60",
        },
    )
    sem = runtime["qty_prep"]["semantic_filters"]
    assert sem["filter_active"] is True
    assert sem["active_op"] == "eq"
    assert "FireRating" in (sem.get("active_chip") or "") or "R60" in (sem.get("active_chip") or "")
    captured = _capture_query_state(
        {
            "semantic_field": "prop:Pset_BeamCommon.FireRating",
            "semantic_op": "eq",
            "semantic_value": "R60",
            "sem_cols": "prop:Qto_BeamBaseQuantities.NetVolume",
        }
    )
    assert captured.get("semantic_op") == "eq"
    assert "NetVolume" in (captured.get("sem_cols") or "")


@pytest.mark.django_db
def test_source_units_from_project_units_not_invented():
    project = _pilot_like_project()
    units = discover_model_units(project)
    assert units["length"] == "mm"
    assert units["area"] == "m2"
    assert units["volume"] == "m3"

    bare = ProjectFactory()
    IFCFileFactory(project=bare, status="completed", project_units={})
    unknown = discover_model_units(bare)
    assert unknown["length"] == ""
    assert unknown["area"] == ""
    assert UNKNOWN_SOURCE_UNIT_LABEL


def test_unknown_unit_does_not_convert():
    bad = convert_quantity(model_total=1000, model_unit="", output_unit="m")
    assert not bad.ok
    assert bad.error == "unresolved_unit"


def test_infer_qto_numeric():
    assert infer_value_type(["1", "2"], source_property="Qto_BeamBaseQuantities.Length") == (
        "numeric"
    )


@pytest.mark.django_db
def test_discover_indexed_includes_qto():
    project = _pilot_like_project()
    cols = discover_indexed_property_columns(project=project)
    srcs = [c["source_property"] for c in cols]
    assert any(s.startswith("Qto_") for s in srcs)
    assert "Category" not in srcs
