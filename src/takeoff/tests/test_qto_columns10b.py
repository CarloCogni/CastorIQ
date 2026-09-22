# takeoff/tests/test_qto_columns10b.py
"""QTO-COLUMNS-10B — calc menu, type identity, sort order, restore fields."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import enrich_prep_rows_with_entity_semantics
from takeoff.services.quantity_column_calc import (
    attach_column_header_menus,
    compute_parent_calc,
    compute_parent_none,
    eligible_calc_ops,
)
from takeoff.services.quantity_hierarchy import (
    build_quantity_hierarchy,
    flatten_visible_hierarchy_rows,
)
from takeoff.services.quantity_hierarchy_sort import apply_hierarchy_sort_to_tree
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


@pytest.mark.django_db
def test_same_name_types_stay_independent_for_all_ops():
    """Two types share display name; parent_key identity keeps values separate."""
    project = ProjectFactory()
    user = get_user_model().objects.create_user(username="c10b_same", password="x")
    ifc = IFCFileFactory(project=project, status="completed")
    et_a = IFCElementTypeFactory(ifc_file=ifc, name="SharedName", global_id="TGIDSAMEAAAA01")
    et_b = IFCElementTypeFactory(ifc_file=ifc, name="SharedName", global_id="TGIDSAMEBBBB01")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        name="A1",
        global_id="GIDSAMEA000001",
        element_type=et_a,
        properties={"Pset_Demo.Load": 10.0, "Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        name="A2",
        global_id="GIDSAMEA000002",
        element_type=et_a,
        properties={"Pset_Demo.Load": 30.0, "Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        name="B1",
        global_id="GIDSAMEB000001",
        element_type=et_b,
        properties={"Pset_Demo.Load": 100.0, "Qto_BeamBaseQuantities.NetVolume": 5.0},
    )
    col = "prop:Pset_Demo.Load"
    session = SessionStore()
    session.create()

    # Expand class so both same-name type rows are visible.
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    type_keys = list(tree["classes"][0].get("type_keys") or [])
    assert len(type_keys) >= 2
    # Identify which type_key is A vs B via instances
    type_a = type_b = None
    for tk in type_keys:
        insts = (tree.get("instances_by_type_key") or {}).get(tk) or []
        gids = {str(i.get("global_id") or "") for i in insts}
        if "GIDSAMEA000001" in gids:
            type_a = tk
        if "GIDSAMEB000001" in gids:
            type_b = tk
    assert type_a and type_b and type_a != type_b

    for op, expect_a, expect_b in (
        ("none", "Multiple values", "100"),
        ("sum", "40", "100"),
        ("avg", "20", "100"),
        ("min", "10", "100"),
        ("max", "30", "100"),
    ):
        query = {
            "table_layout": "v2",
            "col_order": f"ifc_class,name,status,actions,{col}",
            "col_calc": f"{col}:{op}",
            "semantic_classes": "IfcBeam",
            "hierarchy_expanded": class_key,
        }
        runtime = build_qty_prep_session_ui(
            project=project, user=user, session=session, query=query, ifc_file=ifc
        )
        type_rows = {
            str(r.get("node_key")): r
            for r in runtime["qty_prep"]["prep_rows"]
            if r.get("level") == "type" and not r.get("is_load_more")
        }
        assert type_a in type_rows and type_b in type_rows, (op, list(type_rows))
        cell_a = (type_rows[type_a].get("prop_column_by_key") or {}).get(col) or {}
        cell_b = (type_rows[type_b].get("prop_column_by_key") or {}).get(col) or {}
        disp_a = str(cell_a.get("display") or type_rows[type_a].get(col) or "")
        disp_b = str(cell_b.get("display") or type_rows[type_b].get(col) or "")
        assert disp_a.startswith(expect_a) or disp_a == expect_a, (op, disp_a, expect_a)
        assert disp_b.startswith(expect_b) or disp_b == expect_b, (op, disp_b, expect_b)


def test_weighted_average_unequal_child_counts():
    """Average = sum(leaves)/n, not average of child averages."""
    values = [Decimal("10"), Decimal("10"), Decimal("40")]
    result = compute_parent_calc(values, op="avg", total_leaves=3)
    assert result["display"].startswith("20")
    assert "25" not in result["display"]


def test_none_all_missing_not_multiple():
    result = compute_parent_none([None, None, "—", ""])
    assert result["status"] == "missing"
    assert result["display"] == "—"
    common = compute_parent_none(["12", "12", None])
    assert common["display"] == "12"
    mixed = compute_parent_none(["12", "13"])
    assert mixed["display"] == "Multiple values"


def test_zero_preserved_in_sum():
    zero = compute_parent_calc([Decimal("0"), Decimal("0")], op="sum", total_leaves=2)
    assert zero["display"] in {"0", "0.0", "0.00"}
    partial = compute_parent_calc([Decimal("0"), None, Decimal("5")], op="sum", total_leaves=3)
    assert partial["display"].startswith("5")
    assert "2 of 3" in partial["display"]


@pytest.mark.django_db
def test_numeric_sort_order_2_10_100():
    """Ascending/descending prove numeric order, not lexicographic."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for gid_suffix, vol in (("T002", 2.0), ("T010", 10.0), ("T100", 100.0)):
        et = IFCElementTypeFactory(
            ifc_file=ifc, name=f"Type{gid_suffix}", global_id=f"TGIDSORT{gid_suffix}01"
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            name=f"C{gid_suffix}",
            global_id=f"GIDSORT{gid_suffix}01",
            element_type=et,
            properties={"Qto_ColumnBaseQuantities.NetVolume": vol},
        )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_node = tree["classes"][0]
    col = "prop:Qto_ColumnBaseQuantities.NetVolume"
    lookup = {}
    for tk in class_node.get("type_keys") or []:
        insts = (tree.get("instances_by_type_key") or {}).get(tk) or []
        vol = None
        for inst in insts:
            inv = inst.get("measure_inventory") or {}
            if inv.get("NetVolume") is not None:
                vol = float(inv["NetVolume"])
                break
        lookup[tk] = {
            "node_key": tk,
            "prop_column_by_key": {
                col: {"display": str(vol), "numeric_value": vol, "status": "ok"}
            },
        }
    apply_hierarchy_sort_to_tree(tree, column_key=col, direction="asc", row_lookup=lookup)
    asc_vols = [
        lookup[tk]["prop_column_by_key"][col]["numeric_value"]
        for tk in (class_node.get("type_keys") or [])
    ]
    assert asc_vols == [2.0, 10.0, 100.0], asc_vols

    apply_hierarchy_sort_to_tree(tree, column_key=col, direction="desc", row_lookup=lookup)
    desc_vols = [
        lookup[tk]["prop_column_by_key"][col]["numeric_value"]
        for tk in (class_node.get("type_keys") or [])
    ]
    assert desc_vols == [100.0, 10.0, 2.0], desc_vols

    for tk in class_node.get("type_keys") or []:
        assert (tree.get("instances_by_type_key") or {}).get(tk)


@pytest.mark.django_db
def test_header_menu_exposes_calc_without_url_editing():
    project = ProjectFactory()
    user = get_user_model().objects.create_user(username="c10b_menu", password="x")
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="MenuType", global_id="TGIDMENU000001")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GIDMENU00000001",
        element_type=et,
        properties={"Pset_Demo.Width": 2.5},
    )
    col = "prop:Pset_Demo.Width"
    session = SessionStore()
    session.create()
    query = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,status,actions,{col}",
        "semantic_classes": "IfcWall",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    columns = runtime["qty_prep"]["table_columns"]
    target = next(c for c in columns if c.get("key") == col)
    assert target.get("calc_eligible") is True
    ops = {o["op"] for o in target.get("calc_options") or []}
    assert ops == {"none", "sum", "avg", "min", "max"}
    assert all(o.get("href", "").startswith("?") for o in target["calc_options"])
    qty_col = next((c for c in columns if c.get("key") == "quantity"), None)
    if qty_col is not None:
        assert qty_col.get("calc_eligible") is False or not qty_col.get("calc_options")
    name_col = next(c for c in columns if c.get("key") == "name")
    assert name_col.get("calc_eligible") is False
    assert name_col.get("sort_asc_href")
    assert name_col.get("sort_desc_href")
    assert name_col.get("sort_clear_href")


def test_quantity_and_text_not_calc_eligible():
    assert eligible_calc_ops(column_key="quantity", value_type="numeric") == ["none"]
    assert eligible_calc_ops(column_key="name", value_type="text") == ["none"]
    assert "sum" in eligible_calc_ops(
        column_key="prop:Pset.X", value_type="numeric", source_property="Pset.X"
    )


@pytest.mark.django_db
def test_enrich_same_name_types_via_parent_key():
    """Enrichment No-calculation path must not mix same-name types."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et_a = IFCElementTypeFactory(ifc_file=ifc, name="Twin", global_id="TGIDTWINAAAA01")
    et_b = IFCElementTypeFactory(ifc_file=ifc, name="Twin", global_id="TGIDTWINBBBB01")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GIDTWINA000001",
        element_type=et_a,
        properties={"Pset_Demo.Tag": "alpha"},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GIDTWINB000001",
        element_type=et_b,
        properties={"Pset_Demo.Tag": "beta"},
    )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    expanded = {c["node_key"] for c in tree["classes"]} | set(
        (tree["classes"][0].get("type_keys") or [])
    )
    visible = flatten_visible_hierarchy_rows(tree, expanded=expanded)
    col = "prop:Pset_Demo.Tag"
    enrich_prep_rows_with_entity_semantics(
        project, visible, selected_prop_columns=[col], ifc_file=ifc
    )
    type_rows = [r for r in visible if r.get("level") == "type"]
    displays = {str(r.get("node_key")): r.get(col) for r in type_rows}
    assert len(displays) == 2
    vals = set(displays.values())
    assert vals == {"alpha", "beta"}
    assert "Multiple values" not in vals


@pytest.mark.django_db
def test_attach_menus_marks_selected_calc():
    qty_prep = {
        "table_columns": [
            {"key": "prop:Pset.Width", "label": "Width", "removable": True},
            {"key": "name", "label": "Name", "removable": False},
        ],
        "col_calc_ops": {"prop:Pset.Width": "avg"},
        "hierarchy_sort": {"key": "prop:Pset.Width", "dir": "desc"},
        "semantic_filters": {
            "selected_prop_column_meta": [
                {
                    "key": "prop:Pset.Width",
                    "value_type": "numeric",
                    "source_property": "Pset.Width",
                }
            ]
        },
    }
    attach_column_header_menus(qty_prep, query={"table_layout": "v2"})
    width = qty_prep["table_columns"][0]
    assert width["calc_op"] == "avg"
    assert width["calc_label"] == "Average"
    assert width["sort_active"] is True
    assert width["sort_dir"] == "desc"
    selected = [o for o in width["calc_options"] if o["selected"]]
    assert len(selected) == 1 and selected[0]["op"] == "avg"
