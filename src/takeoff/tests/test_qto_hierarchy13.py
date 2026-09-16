# takeoff/tests/test_qto_hierarchy13.py
"""QTO-HIERARCHY-13 — engineering display groups from indexed type evidence."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_engineering_groups import (
    engineering_group_signature,
    filter_engineering_properties,
    has_engineering_evidence,
)
from takeoff.services.quantity_hierarchy import (
    build_quantity_hierarchy,
    flatten_visible_hierarchy_rows,
    iter_descendant_instance_rows,
)


def _props_400() -> dict:
    return {
        "Dimensions.b": 400.0,
        "Dimensions.h": 400.0,
        "Other.Breadth": 400.0,
        "Other.Height": 400.0,
        "Other.Family Name": "RectCol",
        "Pset_ColumnCommon.id": 1,
        "Identity Data.Type Name": "400x400",
    }


def _column_project_equivalent_types(*, n: int = 3):
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        file_hash="b" * 64,
        project_units={"VOLUMEUNIT": "m³"},
    )
    name = "RectCol:400x400"
    for i in range(n):
        props = _props_400()
        props["Pset_ColumnCommon.id"] = 1000 + i
        et = IFCElementTypeFactory(
            ifc_file=ifc,
            ifc_type="IfcColumnType",
            name=name,
            global_id=f"TG-EQ-{i:02d}",
            tag="TAG-1",
            properties=props,
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id=f"COL-EQ-{i:02d}",
            name=f"Column {i}",
            element_type=et,
            properties={"Qto_ColumnBaseQuantities.NetVolume": 1.25},
        )
    return project, ifc, name


@pytest.mark.django_db
def test_equivalent_technical_types_form_one_engineering_group():
    project, ifc, name = _column_project_equivalent_types(n=3)
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_elements"] == 3
    assert tree["counts"]["matching_types"] == 3
    assert tree["counts"]["matching_engineering_groups"] == 1
    class_node = tree["classes"][0]
    assert class_node["type_count"] == 1
    assert class_node["technical_type_count"] == 3
    group_key = class_node["type_keys"][0]
    group = tree["type_by_key"][group_key]
    assert group["technical_type_count"] == 3
    assert group["element_count"] == 3
    assert group["type_name"] == name
    assert abs(float(group["total"]) - 3.75) < 1e-9
    rows = flatten_visible_hierarchy_rows(tree, expanded={class_node["node_key"]})
    type_rows = [r for r in rows if r.get("level") == "type"]
    assert len(type_rows) == 1
    inst = iter_descendant_instance_rows(tree, node_key=group_key)
    assert len(inst) == 3
    assert {r["global_id"] for r in inst} == {"COL-EQ-00", "COL-EQ-01", "COL-EQ-02"}


@pytest.mark.django_db
def test_conflicting_dimensions_stay_separate():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", file_hash="c" * 64)
    name = "RectCol:SameName"
    for i, dim in enumerate((400.0, 600.0)):
        props = {
            "Dimensions.b": dim,
            "Dimensions.h": dim,
            "Other.Family Name": "RectCol",
            "Pset_ColumnCommon.id": i,
        }
        et = IFCElementTypeFactory(
            ifc_file=ifc,
            ifc_type="IfcColumnType",
            name=name,
            global_id=f"TG-CF-{i}",
            tag="TAG-X",
            properties=props,
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id=f"COL-CF-{i}",
            name=f"Col {i}",
            element_type=et,
            properties={"Qto_ColumnBaseQuantities.NetVolume": float(i + 1)},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_engineering_groups"] == 2
    assert tree["counts"]["matching_types"] == 2


@pytest.mark.django_db
def test_missing_evidence_does_not_merge_on_name_alone():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", file_hash="d" * 64)
    name = "BareName"
    for i in range(2):
        et = IFCElementTypeFactory(
            ifc_file=ifc,
            ifc_type="IfcColumnType",
            name=name,
            global_id=f"TG-BARE-{i}",
            properties={},
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id=f"COL-BARE-{i}",
            element_type=et,
            properties={},
        )
    assert not has_engineering_evidence(properties={})
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_engineering_groups"] == 2


def test_parser_object_id_excluded_engineering_id_kept():
    """Provenance-backed *.id drops; unknown ID-like keys stay in the signature."""
    props = {
        "Dimensions.b": 400.0,
        "Pset_ColumnCommon.id": 99,
        "Other.id": 1,
        "Identity Data.id": 7,
        "Identity Data.Mark": "COL-A",
        "Custom.EngineeringId": "ENG-42",
        "Custom.TypeId": "keep-me",  # leaf is TypeId, not id
    }
    filtered = filter_engineering_properties(props)
    assert "Dimensions.b" in filtered
    assert "Identity Data.Mark" in filtered
    assert "Custom.EngineeringId" in filtered
    assert "Custom.TypeId" in filtered
    assert "Pset_ColumnCommon.id" not in filtered
    assert "Other.id" not in filtered
    assert "Identity Data.id" not in filtered
    a = engineering_group_signature(
        ifc_class="IfcColumn",
        type_name="N",
        tag="T",
        properties={"Dimensions.b": 400.0, "Pset_ColumnCommon.id": 1},
    )
    b = engineering_group_signature(
        ifc_class="IfcColumn",
        type_name="N",
        tag="T",
        properties={"Dimensions.b": 400.0, "Pset_ColumnCommon.id": 2},
    )
    assert a and a == b


def test_genuine_engineering_id_prevents_merge():
    """Same dimensions + different user engineering ID must not share a signature."""
    base = {
        "Dimensions.b": 400.0,
        "Dimensions.h": 400.0,
        "Other.Family Name": "RectCol",
        "Pset_ColumnCommon.id": 1,
    }
    a = engineering_group_signature(
        ifc_class="IfcColumn",
        type_name="RectCol:400x400",
        tag="TAG-1",
        properties={**base, "Identity Data.Mark": "COL-01"},
    )
    b = engineering_group_signature(
        ifc_class="IfcColumn",
        type_name="RectCol:400x400",
        tag="TAG-1",
        properties={**base, "Identity Data.Mark": "COL-02", "Pset_ColumnCommon.id": 99},
    )
    assert a and b and a != b


def test_identity_keys_excluded_from_signature():
    """Back-compat name for HIERARCHY-13 assertion."""
    test_parser_object_id_excluded_engineering_id_kept()


@pytest.mark.django_db
def test_class_and_group_quantity_conservation():
    project, ifc, _ = _column_project_equivalent_types(n=4)
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_node = tree["classes"][0]
    group = tree["type_by_key"][class_node["type_keys"][0]]
    inst_sum = sum(
        float(i["total"])
        for i in tree["instances_by_type_key"][group["node_key"]]
        if i.get("total") is not None
    )
    assert abs(float(class_node["total"]) - inst_sum) < 1e-9
    assert abs(float(group["total"]) - inst_sum) < 1e-9
