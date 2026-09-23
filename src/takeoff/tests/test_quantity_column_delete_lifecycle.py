# takeoff/tests/test_quantity_column_delete_lifecycle.py
"""Column delete lifecycle — canonical col_order, no resurrect, save/export."""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_column_calc import attach_column_header_menus
from takeoff.services.quantity_editable_table import (
    QuantityEditableTableService,
    _capture_query_state,
)
from takeoff.services.quantity_prep_export import csv_headers_from_layout
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_table_layout import (
    CORE_COLUMN_KEYS,
    DEFAULT_NEW_ORDER,
    layout_query_pairs,
    order_after_remove,
    parse_table_layout,
)

FIELD_A = "quantity"
FIELD_B = "measurement"
CORES = list(CORE_COLUMN_KEYS)


def _project():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project, status="completed", name="col-delete.ifc", file_hash="d" * 64
    )
    et = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamT", ifc_type="IfcBeamType", global_id="ET-CD1"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-CD-1",
        element_type=et,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 2.0,
            "Qto_BeamBaseQuantities.Length": 1000.0,
        },
    )
    return project, ifc


def _order_from_href(href: str) -> list[str]:
    qs = parse_qs(href.lstrip("?"))
    raw = (qs.get("col_order") or [""])[0]
    return [p for p in raw.split(",") if p]


def _assert_no_mutators(href: str) -> None:
    qs = parse_qs(href.lstrip("?"))
    assert "col_order_remove" not in qs
    assert "col_order_add" not in qs
    assert "col_order_move" not in qs


def _runtime(project, query: dict):
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=query
    )
    attach_column_header_menus(runtime["qty_prep"], query=query)
    return runtime


def _visible_keys(runtime) -> list[str]:
    return [c["key"] for c in runtime["qty_prep"]["table_columns"]]


@pytest.mark.django_db
def test_add_b_add_a_modal_delete_b_header_delete_a_cores_only():
    """Add B → Add A → delete B → delete A → cores only (no resurrect)."""
    project, _ifc = _project()
    L0 = parse_table_layout({})
    L1 = parse_table_layout(
        {"table_layout": "v2", "col_order": L0["col_order_param"], "col_order_add": FIELD_B}
    )
    L2 = parse_table_layout(
        {"table_layout": "v2", "col_order": L1["col_order_param"], "col_order_add": FIELD_A}
    )
    q3 = {
        "table_layout": "v2",
        "col_order": ",".join(order_after_remove(L2["order"], FIELD_B)),
    }
    r3 = _runtime(project, q3)
    assert FIELD_B not in _visible_keys(r3)
    assert FIELD_A in _visible_keys(r3)
    href_a = next(c["remove_href"] for c in r3["qty_prep"]["table_columns"] if c["key"] == FIELD_A)
    _assert_no_mutators(href_a)
    assert FIELD_B not in _order_from_href(href_a)
    assert FIELD_A not in _order_from_href(href_a)

    q4 = {k: v[0] for k, v in parse_qs(href_a.lstrip("?")).items()}
    r4 = _runtime(project, q4)
    assert _visible_keys(r4) == CORES
    assert FIELD_A not in _visible_keys(r4)
    assert FIELD_B not in _visible_keys(r4)


@pytest.mark.django_db
def test_reverse_delete_order_cores_only():
    """Add B → Add A → delete A → delete B → cores only."""
    project, _ifc = _project()
    L0 = parse_table_layout({})
    L1 = parse_table_layout(
        {"table_layout": "v2", "col_order": L0["col_order_param"], "col_order_add": FIELD_B}
    )
    L2 = parse_table_layout(
        {"table_layout": "v2", "col_order": L1["col_order_param"], "col_order_add": FIELD_A}
    )
    q3 = {
        "table_layout": "v2",
        "col_order": ",".join(order_after_remove(L2["order"], FIELD_A)),
    }
    r3 = _runtime(project, q3)
    href_b = next(c["remove_href"] for c in r3["qty_prep"]["table_columns"] if c["key"] == FIELD_B)
    q4 = {k: v[0] for k, v in parse_qs(href_b.lstrip("?")).items()}
    assert _visible_keys(_runtime(project, q4)) == CORES


@pytest.mark.django_db
def test_hrefs_and_form_col_order_exclude_deleted_keys():
    """After deleting B, every control serializes col_order without B."""
    project, _ifc = _project()
    both = parse_table_layout(
        {
            "table_layout": "v2",
            "col_order": ",".join(CORES),
            "col_order_add": FIELD_B,
        }
    )
    with_a = parse_table_layout(
        {
            "table_layout": "v2",
            "col_order": both["col_order_param"],
            "col_order_add": FIELD_A,
        }
    )
    stale = {
        "table_layout": "v2",
        "col_order": with_a["col_order_param"],
        "col_order_remove": FIELD_B,
    }
    r = _runtime(project, stale)
    assert FIELD_B not in _visible_keys(r)
    layout = r["qty_prep"]["table_layout"]
    assert FIELD_B not in layout["col_order_param"].split(",")

    for col in r["qty_prep"]["table_columns"]:
        for href_key in ("remove_href", "sort_asc_href", "sort_desc_href", "sort_clear_href"):
            href = col.get(href_key) or ""
            if not href:
                continue
            order = _order_from_href(href)
            assert FIELD_B not in order
            _assert_no_mutators(href)
        for opt in col.get("calc_options") or []:
            href = opt.get("href") or ""
            if href:
                assert FIELD_B not in _order_from_href(href)
                _assert_no_mutators(href)

    pairs = layout_query_pairs(stale, layout)
    joined = ",".join(v for k, v in pairs if k == "col_order")
    assert FIELD_B not in joined.split(",")


@pytest.mark.django_db
def test_delete_then_sort_keeps_deleted_absent():
    """Delete B then sort Name — B stays absent."""
    project, _ifc = _project()
    q = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,{FIELD_B},{FIELD_A},status,actions",
        "col_order_remove": FIELD_B,
    }
    r = _runtime(project, q)
    name_col = next(c for c in r["qty_prep"]["table_columns"] if c["key"] == "name")
    sort_q = {k: v[0] for k, v in parse_qs(name_col["sort_asc_href"].lstrip("?")).items()}
    assert FIELD_B not in sort_q.get("col_order", "").split(",")
    r2 = _runtime(project, sort_q)
    assert FIELD_B not in _visible_keys(r2)
    assert FIELD_A in _visible_keys(r2)


@pytest.mark.django_db
def test_delete_then_calc_menu_keeps_deleted_absent():
    """Delete B then follow a calc menu href — B stays absent."""
    project, _ifc = _project()
    col = "prop:Qto_BeamBaseQuantities.NetVolume"
    q = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,{FIELD_B},{col},status,actions",
        "col_order_remove": FIELD_B,
    }
    r = _runtime(project, q)
    prop = next((c for c in r["qty_prep"]["table_columns"] if c["key"] == col), None)
    assert prop is not None
    opts = prop.get("calc_options") or []
    assert opts, "expected calc options on numeric prop column"
    calc_q = {k: v[0] for k, v in parse_qs(opts[0]["href"].lstrip("?")).items()}
    assert FIELD_B not in calc_q.get("col_order", "").split(",")
    r2 = _runtime(project, calc_q)
    assert FIELD_B not in _visible_keys(r2)


@pytest.mark.django_db
def test_add_delete_add_same_field_exactly_one():
    """Add → delete → add same field yields one column instance."""
    base = ",".join(DEFAULT_NEW_ORDER)
    added = parse_table_layout({"table_layout": "v2", "col_order": base, "col_order_add": FIELD_A})
    removed = parse_table_layout(
        {
            "table_layout": "v2",
            "col_order": ",".join(order_after_remove(added["order"], FIELD_A)),
        }
    )
    assert FIELD_A not in removed["order"]
    again = parse_table_layout(
        {
            "table_layout": "v2",
            "col_order": removed["col_order_param"],
            "col_order_add": FIELD_A,
        }
    )
    assert again["order"].count(FIELD_A) == 1


@pytest.mark.django_db
def test_refresh_preserves_effective_unsaved_query_layout(client):
    """Refreshing the same cleaned query keeps deleted columns absent."""
    project, _ifc = _project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    params = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,{FIELD_A},status,actions",
    }
    html1 = client.get(url, params).content.decode()
    html2 = client.get(url, params).content.decode()
    assert f'data-testid="qty-prep-col-{FIELD_A}"' in html1
    assert f'data-testid="qty-prep-col-{FIELD_B}"' not in html1
    assert f'data-testid="qty-prep-col-{FIELD_A}"' in html2
    assert f'data-testid="qty-prep-col-{FIELD_B}"' not in html2


@pytest.mark.django_db
def test_save_table_after_deletes_stores_cores_only():
    """Save Table after deletes persists cores-only col_order."""
    project, _ifc = _project()
    session: dict = {}
    dirty = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,{FIELD_B},{FIELD_A},status,actions",
        "col_order_remove": FIELD_B,
    }
    cleaned = {
        "table_layout": "v2",
        "col_order": ",".join(order_after_remove(parse_table_layout(dirty)["order"], FIELD_A)),
    }
    assert parse_table_layout(cleaned)["order"] == CORES
    snap = _capture_query_state(cleaned)
    assert snap["col_order"] == ",".join(CORES)
    assert FIELD_A not in snap["col_order"]
    assert FIELD_B not in snap["col_order"]

    svc = QuantityEditableTableService(project, project.owner)
    out = svc.save_new(name="Col Delete Cores", session=session, query=cleaned)
    assert out["error"] is None
    table = out["result"]
    assert table.state["query"]["col_order"] == ",".join(CORES)


@pytest.mark.django_db
def test_reopen_saved_table_deleted_columns_remain_absent(client):
    """Reopen saved table — deleted optionals stay absent."""
    project, _ifc = _project()
    client.force_login(project.owner)
    session = client.session
    svc = QuantityEditableTableService(project, project.owner)
    table = svc.save_new(
        name="No Optionals",
        session=session,
        query={"table_layout": "v2", "col_order": ",".join(CORES)},
    )["result"]
    session.save()

    open_url = reverse("takeoff:qty_editable_table_open", kwargs={"pk": project.pk})
    assert client.post(open_url, {"table_id": str(table.pk)}).status_code in {302, 204}
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert f'data-testid="qty-prep-col-{FIELD_A}"' not in html
    assert f'data-testid="qty-prep-col-{FIELD_B}"' not in html
    for core in CORES:
        assert f'data-testid="qty-prep-col-{core}"' in html


@pytest.mark.django_db
def test_export_after_save_contains_no_deleted_columns():
    """Export CSV headers follow saved cores-only layout (no deleted cols)."""
    project, _ifc = _project()
    session = SessionStore()
    session.create()
    query = {"table_layout": "v2", "col_order": ",".join(CORES)}
    svc = QuantityEditableTableService(project, project.owner)
    svc.save_new(name="Export Cores", session=session, query=query)
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=query
    )
    qty_prep = runtime["qty_prep"]
    order = list((qty_prep.get("table_layout") or {}).get("order") or [])
    assert FIELD_A not in order
    assert FIELD_B not in order
    for core in CORES:
        assert core in order
    headers = csv_headers_from_layout(
        qty_prep.get("table_columns"), show=qty_prep.get("show") or {}
    )
    col_keys = [c["key"] for c in qty_prep.get("table_columns") or []]
    assert FIELD_A not in col_keys
    assert FIELD_B not in col_keys
    assert "Measurement" not in headers
    assert "Quantity" not in headers


@pytest.mark.django_db
def test_mandatory_cores_cannot_be_deleted():
    """Core columns stay present even when remove is requested."""
    stuck = parse_table_layout(
        {
            "table_layout": "v2",
            "col_order": ",".join(CORES),
            "col_order_remove": "name",
        }
    )
    assert "name" in stuck["order"]
    assert set(CORES).issubset(set(stuck["order"]))

    project, _ifc = _project()
    r = _runtime(project, {"table_layout": "v2", "col_order": ",".join(CORES)})
    for col in r["qty_prep"]["table_columns"]:
        if col["key"] in CORES:
            assert not col.get("can_remove")
            assert not col.get("remove_href")


@pytest.mark.django_db
def test_filters_smoke_still_green():
    """Class filter still narrows rows (Filters surface untouched)."""
    project, ifc = _project()
    et_c = IFCElementTypeFactory(
        ifc_file=ifc, name="ColT", ifc_type="IfcColumnType", global_id="ET-CD-C"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="GID-CD-C1",
        element_type=et_c,
        properties={"Qto_ColumnBaseQuantities.NetVolume": 3.0},
    )
    session = SessionStore()
    session.create()
    full = build_qty_prep_session_ui(project=project, user=project.owner, session=session, query={})
    filtered = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"semantic_classes": "IfcBeam"},
    )
    assert filtered["qty_prep"]["semantic_filters"]["filter_active"] is True
    assert (
        filtered["qty_prep"]["semantic_filters"]["showing_count"]
        < full["qty_prep"]["baseline_row_count"]
    )


@pytest.mark.django_db
def test_measurement_settings_smoke_still_green():
    """Measurement settings panel still builds with quantity column present."""
    project, _ifc = _project()
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": f"ifc_class,name,{FIELD_A},status,actions",
        },
    )
    assert runtime["qty_prep"]["table_layout"]["show"]["quantity"] is True
    assert "measurement_settings" in runtime["qty_prep"]


@pytest.mark.django_db
def test_legacy_tombstone_url_then_header_delete_no_resurrect():
    """Regression: stale col_order+remove URL + header delete must not resurrect."""
    project, _ifc = _project()
    q = {
        "table_layout": "v2",
        "col_order": f"ifc_class,name,{FIELD_B},{FIELD_A},status,actions",
        "col_order_remove": FIELD_B,
    }
    r = _runtime(project, q)
    assert FIELD_B not in _visible_keys(r)
    href = next(c["remove_href"] for c in r["qty_prep"]["table_columns"] if c["key"] == FIELD_A)
    assert FIELD_B not in _order_from_href(href)
    q2 = {k: v[0] for k, v in parse_qs(href.lstrip("?")).items()}
    assert _visible_keys(_runtime(project, q2)) == CORES
