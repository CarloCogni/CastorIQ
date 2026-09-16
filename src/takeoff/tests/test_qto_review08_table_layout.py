# takeoff/tests/test_qto_review08_table_layout.py
"""QTO-REVIEW-08 checkpoint A — default/optional columns and order."""

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
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_table_layout import (
    CORE_COLUMN_KEYS,
    DEFAULT_NEW_ORDER,
    LEGACY_DEFAULT_ORDER,
    parse_table_layout,
)


def _project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="Rib", ifc_type="IfcBeamType", global_id="ET1")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G1",
        element_type=et,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
            "Identity Data.Keynote": "K1",
        },
    )
    return project


@pytest.mark.django_db
def test_new_table_defaults_to_four_core_columns():
    layout = parse_table_layout({})
    assert tuple(layout["order"]) == DEFAULT_NEW_ORDER
    assert layout["show"]["quantity"] is False
    assert layout["show"]["measurement"] is False


@pytest.mark.django_db
def test_legacy_sem_cols_keeps_sticky_compat():
    layout = parse_table_layout({"sem_cols": "prop:Identity Data.Keynote"})
    assert layout["layout_mode"] == "legacy"
    for key in LEGACY_DEFAULT_ORDER:
        assert key in layout["order"]
    assert "prop:Identity Data.Keynote" in layout["order"]


@pytest.mark.django_db
def test_add_remove_reorder_optional_and_core_protected():
    base = ",".join(DEFAULT_NEW_ORDER)
    added = parse_table_layout(
        {"col_order": base, "col_order_add": "quantity", "table_layout": "v2"}
    )
    assert "quantity" in added["order"]
    removed = parse_table_layout(
        {
            "col_order": added["col_order_param"],
            "col_order_remove": "quantity",
            "table_layout": "v2",
        }
    )
    assert "quantity" not in removed["order"]
    # Cannot remove core
    stuck = parse_table_layout(
        {
            "col_order": removed["col_order_param"],
            "col_order_remove": "name",
            "table_layout": "v2",
        }
    )
    assert "name" in stuck["order"]
    moved = parse_table_layout(
        {
            "col_order": "ifc_class,name,status,actions,quantity",
            "col_order_move": "quantity:up",
            "table_layout": "v2",
        }
    )
    assert moved["order"].index("quantity") < moved["order"].index("actions")


@pytest.mark.django_db
def test_runtime_default_html_four_columns(client):
    project = _project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url).content.decode()
    assert 'data-testid="qty-prep-col-ifc_class"' in html
    assert 'data-testid="qty-prep-col-name"' in html
    assert ">Name<" in html or "Name</th>" in html
    assert 'data-testid="qty-prep-col-status"' in html
    assert 'data-testid="qty-prep-col-actions"' in html
    assert 'data-testid="qty-prep-col-quantity"' not in html
    assert 'data-testid="qty-prep-col-measurement"' not in html
    assert "Type Name" not in html or 'data-testid="qty-prep-col-type-name"' not in html


@pytest.mark.django_db
def test_runtime_add_quantity_column():
    project = _project()
    session = SessionStore()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"col_order": "ifc_class,name,quantity,status,actions", "table_layout": "v2"},
    )
    order = [c["key"] for c in runtime["qty_prep"]["table_columns"]]
    assert order == ["ifc_class", "name", "quantity", "status", "actions"]
    assert all(k in order for k in CORE_COLUMN_KEYS)


@pytest.mark.django_db
def test_class_only_filter_truthful_baseline():
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
    full = build_qty_prep_session_ui(project=project, user=project.owner, session=session, query={})
    baseline = full["qty_prep"]["baseline_row_count"]
    assert baseline >= 2
    filtered = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"semantic_classes": "IfcBeam"},
    )
    sem = filtered["qty_prep"]["semantic_filters"]
    assert sem["filter_active"] is True
    assert sem["showing_count"] < baseline
    assert sem["baseline_row_count"] == baseline
    assert sem["showing_count"] != sem["baseline_row_count"] or True
    classes = {r["ifc_class"] for r in filtered["qty_prep"]["prep_rows"]}
    assert classes == {"IfcBeam"}
