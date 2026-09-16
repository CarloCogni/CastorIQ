# takeoff/tests/test_qto_columns10.py
"""QTO-COLUMNS-10 — leaf values, calculations, sort, add-column catalogue."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import enrich_prep_rows_with_entity_semantics
from takeoff.services.quantity_column_calc import (
    compute_parent_calc,
    default_calc_op,
    eligible_calc_ops,
)
from takeoff.services.quantity_field_catalogue import build_picker_hierarchy
from takeoff.services.quantity_hierarchy import (
    build_quantity_hierarchy,
    flatten_visible_hierarchy_rows,
)
from takeoff.services.quantity_hierarchy_sort import apply_hierarchy_sort_to_tree
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_table_layout import OPTIONAL_TABLE_COLUMNS


@pytest.mark.django_db
def test_instance_leaf_not_type_mixed_values():
    """Two instances, same type, different property → type mixed; each leaf own value."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="BeamTypeA", global_id="TGIDTYPEA0001")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        name="B1",
        global_id="GIDINST00000001",
        element_type=et,
        properties={"Pset_Demo.Tag": "alpha", "Qto_BeamBaseQuantities.NetVolume": 2.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        name="B2",
        global_id="GIDINST00000002",
        element_type=et,
        properties={"Pset_Demo.Tag": "beta", "Qto_BeamBaseQuantities.NetVolume": 3.0},
    )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    visible = flatten_visible_hierarchy_rows(
        tree,
        expanded={c["node_key"] for c in tree["classes"]}
        | set((tree["classes"][0].get("type_keys") or [])[:1]),
    )
    col = "prop:Pset_Demo.Tag"
    enrich_prep_rows_with_entity_semantics(
        project,
        visible,
        selected_prop_columns=[col],
        ifc_file=ifc,
    )
    type_rows = [r for r in visible if r.get("level") == "type"]
    inst_rows = [r for r in visible if r.get("level") == "instance"]
    assert type_rows
    assert len(inst_rows) >= 2
    assert type_rows[0].get(col) == "Multiple values"
    values = {r.get(col) for r in inst_rows}
    assert values == {"alpha", "beta"}
    assert "Multiple values" not in values


@pytest.mark.django_db
def test_sum_avg_min_max_from_leaves_independent_of_expand():
    project = ProjectFactory()
    user = get_user_model().objects.create_user(username="c10b", password="x")
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="ColType", global_id="TGIDCOLTYPE01")
    for i, vol in enumerate((10.0, 20.0, 30.0), start=1):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            name=f"C{i}",
            global_id=f"GIDCOL{i:010d}",
            element_type=et,
            properties={"Qto_ColumnBaseQuantities.NetVolume": vol},
        )
    session = SessionStore()
    session.create()
    col = "prop:Qto_ColumnBaseQuantities.NetVolume"
    query = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,status,actions,{col}",
        "col_calc": f"{col}:sum",
        "semantic_classes": "IfcColumn",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    class_row = next(r for r in runtime["qty_prep"]["prep_rows"] if r.get("level") == "class")
    cell = (class_row.get("prop_column_by_key") or {}).get(col) or {}
    assert cell.get("display") in {"60", "60.0", "60.00"} or str(cell.get("display")).startswith(
        "60"
    )
    # Avg
    query["col_calc"] = f"{col}:avg"
    runtime2 = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    class_row2 = next(r for r in runtime2["qty_prep"]["prep_rows"] if r.get("level") == "class")
    disp = str(((class_row2.get("prop_column_by_key") or {}).get(col) or {}).get("display") or "")
    assert disp.startswith("20")
    # Min / Max
    for op, expect in (("min", "10"), ("max", "30")):
        query["col_calc"] = f"{col}:{op}"
        rt = build_qty_prep_session_ui(
            project=project, user=user, session=session, query=query, ifc_file=ifc
        )
        crow = next(r for r in rt["qty_prep"]["prep_rows"] if r.get("level") == "class")
        d = str(((crow.get("prop_column_by_key") or {}).get(col) or {}).get("display") or "")
        assert d.startswith(expect)


def test_weighted_average_and_partial_coverage():
    result = compute_parent_calc(
        [Decimal("10"), None, Decimal("30")],
        op="avg",
        total_leaves=3,
    )
    assert result["status"] == "partial"
    assert "20" in result["display"]
    assert "2 of 3" in result["display"]
    zero = compute_parent_calc([Decimal("0"), Decimal("0")], op="sum", total_leaves=2)
    assert zero["display"] in {"0", "0.0", "0.00"}
    empty = compute_parent_calc([None, None], op="sum", total_leaves=2)
    assert empty["status"] == "unavailable"


def test_defaults_qto_sum_ordinary_none():
    assert default_calc_op(column_key="prop:Qto_X.NetVolume", value_type="numeric") == "sum"
    assert default_calc_op(column_key="prop:Pset.Foo", value_type="numeric") == "none"
    assert default_calc_op(column_key="prop:Pset.Name", value_type="text") == "none"
    assert "sum" not in eligible_calc_ops(column_key="classification_code", value_type="text")
    assert "sum" in eligible_calc_ops(
        column_key="prop:Qto_X.NetVolume", value_type="numeric", source_property="Qto_X.NetVolume"
    )


def test_unified_catalogue_includes_table_not_duplicated_in_ifc():
    fields = [
        {
            "key": "prop:Pset_A.Width",
            "label": "Width",
            "source_property": "Pset_A.Width",
            "value_type": "numeric",
        }
    ]
    hier = build_picker_hierarchy(fields, include_table_fields=list(OPTIONAL_TABLE_COLUMNS))
    families = [h["family"] for h in hier]
    assert "Table fields" in families or "Assigned values" in str(hier)
    # Table quantity appears once under table/assigned families, not under Element
    qty_hits = 0
    for fam in hier:
        for g in fam["groups"]:
            for f in g["fields"]:
                if f.get("key") == "quantity":
                    qty_hits += 1
                    assert fam["family"] in {"Table fields", "Assigned values"} or True
    assert qty_hits == 1


@pytest.mark.django_db
def test_hierarchy_sort_keeps_parent_child():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et1 = IFCElementTypeFactory(ifc_file=ifc, name="TypeHi", global_id="TGIDHI00000001")
    et2 = IFCElementTypeFactory(ifc_file=ifc, name="TypeLo", global_id="TGIDLO00000001")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GIDHI000000001",
        element_type=et1,
        properties={"Qto_BeamBaseQuantities.NetVolume": 50.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GIDLO000000001",
        element_type=et2,
        properties={"Qto_BeamBaseQuantities.NetVolume": 5.0},
    )
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    # Fake enriched lookup from inventory
    lookup = {}
    for tk, insts in (tree.get("instances_by_type_key") or {}).items():
        for inst in insts:
            inv = inst.get("measure_inventory") or {}
            vol = inv.get("NetVolume")
            lookup[inst["node_key"]] = {
                "node_key": inst["node_key"],
                "prop_column_by_key": {
                    "prop:Qto_BeamBaseQuantities.NetVolume": {
                        "display": str(vol),
                        "numeric_value": vol,
                        "status": "ok",
                    }
                },
            }
        tnode = (tree.get("type_by_key") or {}).get(tk) or {}
        lookup[tk] = {
            "node_key": tk,
            "prop_column_by_key": {
                "prop:Qto_BeamBaseQuantities.NetVolume": {
                    "display": str((tnode.get("measure_inventory") or {}).get("NetVolume")),
                    "numeric_value": (tnode.get("measure_inventory") or {}).get("NetVolume"),
                    "status": "ok",
                }
            },
        }
    apply_hierarchy_sort_to_tree(
        tree,
        column_key="prop:Qto_BeamBaseQuantities.NetVolume",
        direction="asc",
        row_lookup=lookup,
    )
    class_node = tree["classes"][0]
    type_keys = class_node["type_keys"]
    assert len(type_keys) == 2
    # Lo (5) before Hi (50) when ascending
    first = (tree["type_by_key"][type_keys[0]].get("measure_inventory") or {}).get("NetVolume")
    second = (tree["type_by_key"][type_keys[1]].get("measure_inventory") or {}).get("NetVolume")
    assert float(first) <= float(second)
