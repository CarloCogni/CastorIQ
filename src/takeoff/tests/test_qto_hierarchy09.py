# takeoff/tests/test_qto_hierarchy09.py
"""QTO-HIERARCHY-09 — Class→Type→Instance quantity conservation and identity."""

from __future__ import annotations

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import prop_column_key
from takeoff.services.quantity_hierarchy import (
    DEFAULT_TYPE_PAGE,
    UNTYPED_TYPE_LABEL,
    apply_additive_qto_hierarchy_cells,
    apply_hierarchy_offset_query,
    build_export_instance_rows,
    build_quantity_hierarchy,
    expand_selection_to_instance_rows,
    flatten_visible_hierarchy_rows,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _attach_session(request):
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
def test_hierarchy_100_instances_20_types_conservation():
    """Deterministic fixture: 100 beams / 20 types / known volumes."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    types = []
    for i in range(20):
        types.append(
            IFCElementTypeFactory(
                ifc_file=ifc,
                ifc_type="IfcBeamType",
                name=f"BeamType-{i:02d}",
                global_id=f"TYPE-{i:02d}",
            )
        )
    for i in range(100):
        t = types[i % 20]
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"BEAM-{i:03d}",
            name=f"Beam {i:03d}",
            element_type=t,
            properties={"Qto_BeamBaseQuantities.NetVolume": 1.5},
        )

    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_elements"] == 100
    assert tree["counts"]["matching_types"] == 20
    assert tree["counts"]["matching_classes"] == 1

    class_node = tree["classes"][0]
    assert class_node["element_count"] == 100
    inv = class_node["measure_inventory"]
    assert inv["NetVolume"] == pytest.approx(150.0)

    type_vols = []
    for tkey in class_node["type_keys"]:
        tnode = tree["type_by_key"][tkey]
        assert tnode["element_count"] == 5
        type_vols.append(tnode["measure_inventory"]["NetVolume"])
        inst = tree["instances_by_type_key"][tkey]
        assert len(inst) == 5
        assert sum(i["measure_inventory"]["NetVolume"] or 0 for i in inst) == pytest.approx(
            tnode["measure_inventory"]["NetVolume"]
        )

    assert sum(type_vols) == pytest.approx(class_node["measure_inventory"]["NetVolume"])

    # Expand/collapse does not change parent totals
    collapsed = flatten_visible_hierarchy_rows(tree, expanded=set())
    expanded = flatten_visible_hierarchy_rows(tree, expanded={class_node["node_key"]})
    assert collapsed[0]["total"] == expanded[0]["total"]
    assert collapsed[0]["element_count"] == 100


@pytest.mark.django_db
def test_distinct_type_ids_same_name_not_merged():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    t1 = IFCElementTypeFactory(
        ifc_file=ifc, ifc_type="IfcBeamType", name="SameName", global_id="T-A"
    )
    t2 = IFCElementTypeFactory(
        ifc_file=ifc, ifc_type="IfcBeamType", name="SameName", global_id="T-B"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        element_type=t1,
        properties={"Qto_BeamBaseQuantities.NetVolume": 2.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B2",
        element_type=t2,
        properties={"Qto_BeamBaseQuantities.NetVolume": 3.0},
    )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_types"] == 2
    assert tree["ambiguous_type_name_groups"]
    tokens = sorted(t["type_token"] for t in tree["type_by_key"].values())
    assert tokens == ["tgid:T-A", "tgid:T-B"]
    assert all("tgid:" in k for k in tree["type_by_key"])
    vols = sorted(t["measure_inventory"]["NetVolume"] for t in tree["type_by_key"].values())
    assert vols == [2.0, 3.0]


@pytest.mark.django_db
def test_untyped_instances_under_explicit_bucket():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="C-U",
        element_type=None,
        properties={"Qto_ColumnBaseQuantities.NetVolume": 4.0},
    )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    assert tree["counts"]["matching_types"] == 1
    tnode = next(iter(tree["type_by_key"].values()))
    assert tnode["is_untyped"] is True
    assert tnode["type_name"] == UNTYPED_TYPE_LABEL


@pytest.mark.django_db
def test_selection_expands_class_to_instances():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    t = IFCElementTypeFactory(ifc_file=ifc, name="T1", global_id="T1")
    for i in range(3):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"B{i}",
            element_type=t,
            properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    rows = expand_selection_to_instance_rows(
        tree, selected_row_keys=[], selected_node_keys=[class_key]
    )
    assert len(rows) == 3
    assert len({r["row_key"] for r in rows}) == 3


@pytest.mark.django_db
def test_runtime_hierarchy_default_collapsed(client):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    t = IFCElementTypeFactory(ifc_file=ifc, name="T1", global_id="T1")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B0",
        element_type=t,
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    rf = RequestFactory()
    request = _attach_session(rf.get("/"))
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=request.session,
        query={"table_layout": "v2", "col_order": "ifc_class,name,status,actions"},
        ifc_file=ifc,
    )
    qty = runtime["qty_prep"]
    assert qty.get("hierarchy", {}).get("enabled") is True
    levels = {r.get("level") for r in qty.get("prep_rows") or []}
    assert "class" in levels
    assert "instance" not in levels  # collapsed by default
    assert qty.get("prep_rows_export")
    assert len(qty["prep_rows_export"]) == 1


@pytest.mark.django_db
def test_export_instances_once_no_parent_duplicates():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    t = IFCElementTypeFactory(ifc_file=ifc, name="T1", global_id="T1")
    for i in range(4):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"B{i}",
            element_type=t,
            properties={"Qto_BeamBaseQuantities.NetVolume": 2.0},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    export_rows = build_export_instance_rows(tree)
    assert len(export_rows) == 4
    assert all(r["level"] == "instance" for r in export_rows)
    assert len({r["global_id"] for r in export_rows}) == 4


@pytest.mark.django_db
def test_type_load_more_pages_beyond_default_without_duplicates():
    """Prove type paging past DEFAULT_TYPE_PAGE with stable parent totals."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    n_types = DEFAULT_TYPE_PAGE + 25
    for i in range(n_types):
        t = IFCElementTypeFactory(
            ifc_file=ifc,
            ifc_type="IfcBeamType",
            name=f"T-{i:03d}",
            global_id=f"TG-{i:03d}",
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"B-{i:03d}",
            element_type=t,
            properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    assert tree["counts"]["matching_types"] == n_types

    page1 = flatten_visible_hierarchy_rows(
        tree, expanded={class_key}, type_loaded={}, instance_offsets={}
    )
    type_rows_1 = [r for r in page1 if r.get("level") == "type"]
    load_more = [r for r in page1 if r.get("load_more_kind") == "type"]
    assert len(type_rows_1) == DEFAULT_TYPE_PAGE
    assert load_more and load_more[0]["loaded_count"] == DEFAULT_TYPE_PAGE
    assert page1[0]["element_count"] == n_types
    assert page1[0]["measure_inventory"]["NetVolume"] == pytest.approx(float(n_types))

    session: dict = {}
    type_off, _ = apply_hierarchy_offset_query(
        session, project.pk, {"hierarchy_type_more": class_key}
    )
    assert type_off[class_key] == DEFAULT_TYPE_PAGE * 2
    page2 = flatten_visible_hierarchy_rows(
        tree, expanded={class_key}, type_loaded=type_off, instance_offsets={}
    )
    type_rows_2 = [r for r in page2 if r.get("level") == "type"]
    assert len(type_rows_2) == n_types
    assert not any(r.get("load_more_kind") == "type" for r in page2)
    gids = [r.get("element_type_global_id") for r in type_rows_2]
    assert len(gids) == len(set(gids)) == n_types
    assert page2[0]["measure_inventory"]["NetVolume"] == pytest.approx(float(n_types))


@pytest.mark.django_db
def test_additive_qto_column_sums_on_hierarchy_parents():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    t = IFCElementTypeFactory(ifc_file=ifc, name="T1", global_id="T1")
    for i in range(3):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"B{i}",
            element_type=t,
            properties={"Qto_BeamBaseQuantities.NetVolume": 2.0},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    type_key = tree["classes"][0]["type_keys"][0]
    rows = flatten_visible_hierarchy_rows(
        tree, expanded={class_key, type_key}, type_loaded={}, instance_offsets={}
    )
    col = prop_column_key("Qto_BeamBaseQuantities.NetVolume")
    qty_prep = {
        "prep_rows": rows,
        "prep_rows_export": build_export_instance_rows(tree),
    }
    for row in qty_prep["prep_rows"] + qty_prep["prep_rows_export"]:
        row["hierarchy"] = True
        row["prop_column_by_key"] = {col: {"key": col, "display": "—", "status": "missing"}}
    apply_additive_qto_hierarchy_cells(qty_prep)
    class_row = next(r for r in qty_prep["prep_rows"] if r["level"] == "class")
    type_row = next(r for r in qty_prep["prep_rows"] if r["level"] == "type")
    assert class_row["prop_column_by_key"][col]["display"] == "6.0"
    assert type_row["prop_column_by_key"][col]["display"] == "6.0"
    assert class_row["prop_column_by_key"][col].get("additive") is True
    export_sum = sum(
        float(r["prop_column_by_key"][col]["display"]) for r in qty_prep["prep_rows_export"]
    )
    assert export_sum == pytest.approx(6.0)
