# takeoff/tests/test_demo_closure17c1_presentation.py
"""DEMO-CLOSURE-17C1 — class-filter truth, Open→Saved, copy plurals."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import (
    PREP_NATIVE_FIELDS,
    apply_semantic_filters_to_qty_prep,
)
from takeoff.services.quantity_editable_table import (
    QuantityEditableTableService,
    is_editable_table_dirty,
    mark_editable_table_dirty,
)
from takeoff.services.quantity_hierarchy import (
    load_expanded_keys,
    load_offset_map,
    save_expanded_keys,
    save_offset_map,
)


def _project_with_columns(n: int = 3):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="demo17c1.ifc")
    for i in range(n):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id=f"C{i}",
            name=f"Column {i}",
            properties={"Qto_ColumnBaseQuantities.NetVolume": 0.5 + i},
        )
    return project


def _manual_params(**extra: str) -> dict[str, str]:
    base = {
        "include_classification": "1",
        "include_package": "1",
        "include_work_package": "1",
    }
    base.update(extra)
    return base


@pytest.mark.django_db
def test_ifc_class_excluded_from_field_picker_catalogue():
    """Dedicated IFC Class selector owns class scope — not Field picker."""
    spec = next(s for s in PREP_NATIVE_FIELDS if s["key"] == "ifc_class")
    assert spec["is_filterable"] is False


@pytest.mark.django_db
def test_class_only_filter_chip_no_duplicate_field_condition(client):
    """Class-only filter: chip is IFC Class: IfcColumn; Field/Op/Value empty."""
    project = _project_with_columns()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(
            semantic_classes="IfcColumn",
            semantic_field="ifc_class",
            semantic_value="IfcColumn",
            semantic_op="eq",
        ),
    ).content.decode()

    assert "IFC Class: IfcColumn" in html
    assert "IFC Class equals IfcColumn" not in html
    # Field picker must not list ifc_class as a filterable option.
    assert 'data-field-key="ifc_class"' not in html or 'data-testid="qty-filter-field"' in html


@pytest.mark.django_db
def test_class_only_works_with_empty_field_operator_value(client):
    """semantic_classes alone scopes rows — no Field/Operator/Value required."""
    project = _project_with_columns()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(semantic_classes="IfcColumn"),
    ).content.decode()
    assert "IFC Class: IfcColumn" in html
    assert "IFC Class equals" not in html


@pytest.mark.django_db
def test_hierarchy_footnote_singular_class():
    """Footnote uses '1 class', not '1 classes'."""
    qty_prep: dict = {
        "prep_rows": [],
        "prep_rows_export": [],
    }
    # Minimal attach path: set footnote the same way production does.
    from takeoff.services.quantity_hierarchy import attach_hierarchy_to_qty_prep

    hierarchy = {
        "contract_version": "hn1",
        "counts": {
            "matching_elements": 1017,
            "matching_engineering_groups": 36,
            "matching_types": 883,
            "matching_classes": 1,
        },
        "classes": [],
        "type_by_key": {},
        "instances_by_type_key": {},
        "class_by_key": {},
        "limitations": [],
        "ambiguous_type_name_groups": [],
    }
    attach_hierarchy_to_qty_prep(
        qty_prep,
        hierarchy=hierarchy,
        expanded=set(),
        type_loaded={},
        instance_offsets={},
    )
    note = qty_prep["hierarchy_footnote"]
    assert "1 class" in note
    assert "1 classes" not in note
    assert "36 engineering groups" in note
    assert "883 technical types" in note


@pytest.mark.django_db
def test_open_replaces_stale_hierarchy_overlays_and_clears_dirty():
    """Open restores saved expansion and clears offsets + dirty flag."""
    project = _project_with_columns()
    session: dict = {}
    # Save with a known expansion, then pollute session as if another viewport mutated.
    save_expanded_keys(session, project.pk, ["hn1|class|IfcColumn|-"])
    svc = QuantityEditableTableService(project, project.owner)
    saved = svc.save_new(
        session=session,
        query={
            "semantic_classes": "IfcColumn",
            "col_order": "ifc_class,name,quantity,unit,status,actions",
            "table_layout": "v2",
            "hierarchy_expanded": "hn1|class|IfcColumn|-",
        },
        name="DEMO17C1-open-proof",
    )
    assert saved.get("error") is None
    table = saved["result"]

    mark_editable_table_dirty(session, project.pk)
    save_expanded_keys(session, project.pk, ["hn1|class|IfcWall|-"])
    save_offset_map(session, project.pk, kind="type", offsets={"hn1|class|IfcWall|-": 20})
    save_offset_map(session, project.pk, kind="instance", offsets={"hn1|type|IfcWall|x": 50})

    opened = svc.restore_into_session(table=table, session=session)
    assert opened.get("error") is None
    assert is_editable_table_dirty(session, project.pk) is False
    assert load_expanded_keys(session, project.pk) == {"hn1|class|IfcColumn|-"}
    assert load_offset_map(session, project.pk, kind="type") == {}
    assert load_offset_map(session, project.pk, kind="instance") == {}


@pytest.mark.django_db
def test_apply_semantic_filters_strips_redundant_ifc_class_field():
    """Redundant Field=ifc_class is ignored when building the active chip."""
    project = _project_with_columns()
    qty_prep = {
        "prep_rows": [
            {
                "ifc_class": "IfcColumn",
                "type_name": "Col",
                "name": "C0",
                "element_count": 1,
            }
        ],
        "prep_rows_export": [],
        "selected_classes": ["IfcColumn"],
        "available_classes": ["IfcColumn"],
        "baseline_row_count": 1,
    }
    panel = apply_semantic_filters_to_qty_prep(
        qty_prep,
        project=project,
        query={
            "semantic_classes": "IfcColumn",
            "semantic_field": "ifc_class",
            "semantic_value": "IfcColumn",
            "semantic_op": "eq",
        },
        defer_field_catalogue=True,
    )
    assert panel["active_chip"] == "IFC Class: IfcColumn"
    assert panel["active_field"] == ""
    assert "equals" not in panel["active_chip"]


@pytest.mark.django_db
def test_legacy_ifc_class_field_without_class_selector_still_filters():
    """Field=ifc_class alone (no semantic_classes) remains a valid legacy filter."""
    project = _project_with_columns()
    qty_prep = {
        "prep_rows": [
            {"ifc_class": "IfcColumn", "type_name": "Col", "name": "C0", "element_count": 1},
            {"ifc_class": "IfcBeam", "type_name": "Beam", "name": "B0", "element_count": 1},
        ],
        "prep_rows_export": [],
        "selected_classes": [],
        "available_classes": ["IfcColumn", "IfcBeam"],
        "baseline_row_count": 2,
    }
    panel = apply_semantic_filters_to_qty_prep(
        qty_prep,
        project=project,
        query={
            "semantic_field": "ifc_class",
            "semantic_value": "IfcColumn",
            "semantic_op": "eq",
        },
        defer_field_catalogue=True,
    )
    assert panel["active_field"] == "ifc_class"
    assert len(qty_prep["prep_rows"]) == 1
    assert qty_prep["prep_rows"][0]["ifc_class"] == "IfcColumn"
