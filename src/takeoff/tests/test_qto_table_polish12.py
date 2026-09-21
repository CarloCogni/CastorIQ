# takeoff/tests/test_qto_table_polish12.py
"""QTO-TABLE-POLISH-12 — presentation dedupe without identity/value drift."""

from __future__ import annotations

from django.template.loader import render_to_string

from takeoff.services.quantity_measurement_settings import attach_selected_source_coverage


def test_partial_coverage_label_is_concise_and_sets_partial_status():
    rows = [
        {
            "level": "class",
            "ifc_class": "IfcWall",
            "node_key": "c1",
            "ifc_quantity_source": "NetArea",
            "review_status": "Resolved",
            "review_status_display": "Resolved",
            "computed_review_status": "Resolved",
        },
        {
            "level": "instance",
            "parent_key": "c1",
            "ifc_class": "IfcWall",
            "measure_inventory": {"NetArea": 1.0},
        },
        {
            "level": "instance",
            "parent_key": "c1",
            "ifc_class": "IfcWall",
            "measure_inventory": {},
        },
    ]
    attach_selected_source_coverage(rows, inventory_rows=rows)
    assert rows[0]["quantity_coverage_label"] == "1/2 · 1 missing"
    assert rows[0]["review_status"] == "Partial"
    assert rows[0]["review_status_display"] == "Partial"
    assert rows[0]["computed_review_status"] == "Partial"


def test_prep_cell_suppresses_inherited_class_and_moves_coverage_to_status():
    class_col = {"key": "ifc_class", "label": "IFC Class"}
    name_col = {"key": "name", "label": "Name"}
    qty_col = {"key": "quantity", "label": "Quantity"}
    status_col = {"key": "status", "label": "Status"}

    type_row = {
        "hierarchy": True,
        "level": "type",
        "depth": 1,
        "ifc_class": "IfcSlab",
        "primary_name": "Floor Type A",
        "display_name": "Floor Type A",
        "element_count": 12,
        "expandable": True,
        "aria_expanded": False,
        "level_label": "Type",
        "node_key": "t1",
        "total": 10.5,
        "total_display": "10.50",
        "quantity_coverage_partial": False,
        "review_status_display": "Resolved",
        "row_key": "t1",
    }
    class_row = {
        "hierarchy": True,
        "level": "class",
        "depth": 0,
        "ifc_class": "IfcSlab",
        "primary_name": "IfcSlab",
        "display_name": "IfcSlab",
        "element_count": 657,
        "type_count": 48,
        "expandable": True,
        "aria_expanded": False,
        "level_label": "Class",
        "node_key": "c1",
        "total": 16164.77,
        "total_display": "16164.77",
        "quantity_coverage_partial": True,
        "quantity_coverage_label": "649/657 · 8 missing",
        "review_status_display": "Partial",
        "row_key": "c1",
    }

    type_ifc = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": class_col, "row": type_row},
    )
    assert 'data-ifc-class-value="IfcSlab"' in type_ifc
    assert "qty-ifc-class-inherited" in type_ifc
    assert "qty-ifc-class-label" not in type_ifc

    class_name = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": name_col, "row": class_row},
    )
    assert "657 el" in class_name
    assert "48 types" in class_name
    assert 'data-testid="qty-hierarchy-element-count"' in class_name
    assert "visually-hidden" in class_name
    # Visible count line must not restate the IFC class name.
    count_snip = class_name.split('data-testid="qty-hierarchy-element-count"', 1)[1]
    assert "IfcSlab" not in count_snip

    qty = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": qty_col, "row": class_row},
    )
    assert "16164.77" in qty
    assert 'data-testid="qty-prep-quantity-coverage"' not in qty
    assert 'data-qty-coverage-label="649/657 · 8 missing"' in qty
    import re

    sans_attr = re.sub(r'\s*data-qty-coverage-label="[^"]*"', "", qty)
    assert "649/657" not in sans_attr
    assert "8 missing" not in sans_attr

    status = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": status_col, "row": class_row},
    )
    assert 'data-testid="qty-prep-status-partial"' in status
    assert "649/657 · 8 missing" in status
    assert "Resolved" not in status


def test_full_coverage_element_count_source_omits_nn_label():
    """Full coverage (including element_count) omits the repeated N/N line."""
    rows = [
        {
            "level": "class",
            "ifc_class": "IfcDoor",
            "node_key": "c1",
            "ifc_quantity_source": "element_count",
            "element_count": 11,
            "measurement_status": "resolved",
        }
    ]
    attach_selected_source_coverage(rows, inventory_rows=rows)
    assert rows[0]["quantity_coverage_label"] == ""
    assert rows[0]["quantity_coverage_partial"] is False
