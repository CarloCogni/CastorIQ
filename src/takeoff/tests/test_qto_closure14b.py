# takeoff/tests/test_qto_closure14b.py
"""QTO-CLOSURE-14B — restore/rebuild must keep formatted Quantity + Unit after sort."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.models import QuantityEditableTable
from takeoff.services.quantity_editable_table import (
    QuantityEditableTableService,
    query_dict_from_saved,
)
from takeoff.services.quantity_measurement_settings import apply_class_settings
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _column_project():
    user = UserFactory()
    project = ProjectFactory(owner=user)
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        file_hash="a" * 64,
        project_units={"VOLUMEUNIT": "m³"},
    )
    name = "RectCol:400x400"
    props = {
        "Dimensions.b": 400.0,
        "Dimensions.h": 400.0,
        "Other.Family Name": "RectCol",
        "Pset_ColumnCommon.id": 1,
    }
    et = IFCElementTypeFactory(
        ifc_file=ifc,
        ifc_type="IfcColumnType",
        name=name,
        global_id="TG-C14B-01",
        tag="TAG-1",
        properties=props,
    )
    # Three instances → group total 0.345 → display 0.35 m³
    for i, vol in enumerate((0.115, 0.115, 0.115)):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id=f"COL-C14B-{i:02d}",
            name=f"Column {i}",
            element_type=et,
            properties={"Qto_ColumnBaseQuantities.NetVolume": vol},
        )
    return project, user, ifc, name


def _focus_group(rows: list[dict], *, name_substr: str = "400x400") -> dict:
    for row in rows:
        if row.get("level") != "type":
            continue
        blob = f"{row.get('type_name') or ''} {row.get('primary_name') or ''}"
        if name_substr in blob:
            return row
    for row in rows:
        if row.get("level") == "class":
            return row
    raise AssertionError("no class/type row")


@pytest.mark.django_db
def test_hierarchy_sort_keeps_formatted_quantity_and_unit():
    """Failure boundary: sort re-flatten must not drop measurement before units."""
    project, user, ifc, _name = _column_project()
    session = SessionStore()
    session.create()
    base_q = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,quantity,unit,status",
        "semantic_classes": "IfcColumn",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=base_q, ifc_file=ifc
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
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        hierarchy_tree=runtime["qty_prep"].get("_hierarchy_tree"),
        known_target_keys=keys,
    )
    sorted_q = {
        **base_q,
        "hierarchy_sort": "quantity",
        "hierarchy_sort_dir": "asc",
    }
    after = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=sorted_q, ifc_file=ifc
    )
    row = _focus_group(list(after["qty_prep"].get("prep_rows") or []))
    assert row.get("measurement_type") == "volume"
    assert row.get("ifc_quantity_source") == "NetVolume"
    assert abs(float(row["model_total"]) - 0.345) < 1e-9
    assert row["total_display"] == "0.35"
    assert row.get("output_unit_label") == "m³" or row.get("model_unit_label") == "m³"
    assert str(row.get("output_unit_label") or "") != "—"
    assert str(row.get("total_display")) != str(row.get("model_total"))


@pytest.mark.django_db
def test_save_open_preserves_quantity_unit_assignment_with_sort():
    """Same saved table before/after Open: raw, display, unit, assignment agree."""
    project, user, ifc, _name = _column_project()
    session = SessionStore()
    session.create()
    base_q = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,quantity,unit,status,classification_code",
        "semantic_classes": "IfcColumn",
        "hierarchy_sort": "quantity",
        "hierarchy_sort_dir": "asc",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=base_q, ifc_file=ifc
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
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="",
        prep_rows=runtime["qty_prep"]["prep_rows"],
        inventory_rows=export,
        hierarchy_tree=runtime["qty_prep"].get("_hierarchy_tree"),
        known_target_keys=keys,
    )
    # Assign classification on one instance via mapping session payload shape.
    from takeoff.services.quantity_prep_row_mapping import (
        CONTRACT_VERSION_V1 as MAPPING_CONTRACT,
    )
    from takeoff.services.quantity_prep_row_mapping import save_payload as save_mapping

    inst = next(
        r
        for r in (runtime["qty_prep"].get("prep_rows_export") or [])
        if r.get("level") == "instance" and r.get("global_id") == "COL-C14B-00"
    )
    save_mapping(
        session,
        str(project.pk),
        {
            "contract_version": MAPPING_CONTRACT,
            "annotations": {
                str(inst["row_key"]): {
                    "classification_code": {
                        "value": "EL-DEMO-COLUMN",
                        "label": "Column elements",
                        "origin": "manual_session_schema_node",
                    }
                }
            },
        },
    )
    before = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=base_q, ifc_file=ifc
    )
    before_group = _focus_group(list(before["qty_prep"].get("prep_rows") or []))

    svc = QuantityEditableTableService(project, user)
    saved = svc.save_new(
        session=session,
        name="C14B-test",
        query=base_q,
    )
    assert saved.get("error") is None
    table: QuantityEditableTable = saved["result"]

    fresh = SessionStore()
    fresh.create()
    restored = svc.restore_into_session(table=table, session=fresh)
    assert restored.get("error") is None
    query = query_dict_from_saved((table.state or {}).get("query") or {})
    after = build_qty_prep_session_ui(
        project=project, user=user, session=fresh, query=query, ifc_file=ifc
    )
    after_group = _focus_group(list(after["qty_prep"].get("prep_rows") or []))

    assert after_group.get("measurement_type") == before_group.get("measurement_type") == "volume"
    assert after_group.get("ifc_quantity_source") == "NetVolume"
    assert abs(float(after_group["model_total"]) - float(before_group["model_total"])) < 1e-12
    assert after_group["total_display"] == before_group["total_display"] == "0.35"
    assert (after_group.get("output_unit_label") or after_group.get("model_unit_label")) == "m³"
    assert (before_group.get("output_unit_label") or before_group.get("model_unit_label")) == "m³"

    # Output conversion + reset must not double-convert raw model_total.
    apply_class_settings(
        project=project,
        user=user,
        session=fresh,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="mm3",
        prep_rows=after["qty_prep"]["prep_rows"],
        inventory_rows=list(after["qty_prep"].get("prep_rows_export") or []),
        hierarchy_tree=after["qty_prep"].get("_hierarchy_tree"),
        known_target_keys=keys,
    )
    conv = build_qty_prep_session_ui(
        project=project, user=user, session=fresh, query=query, ifc_file=ifc
    )
    conv_group = _focus_group(list(conv["qty_prep"].get("prep_rows") or []))
    assert conv_group.get("unit_converted") is True
    assert abs(float(conv_group["model_total"]) - 0.345) < 1e-9

    from takeoff.services.quantity_output_units import QuantityOutputUnitsService

    QuantityOutputUnitsService(project, user, fresh).reset_class_output_units(ifc_class="IfcColumn")
    reset = build_qty_prep_session_ui(
        project=project, user=user, session=fresh, query=query, ifc_file=ifc
    )
    reset_group = _focus_group(list(reset["qty_prep"].get("prep_rows") or []))
    assert reset_group["total_display"] == "0.35"
    assert (reset_group.get("output_unit_label") or reset_group.get("model_unit_label")) == "m³"
    assert abs(float(reset_group["model_total"]) - 0.345) < 1e-9
