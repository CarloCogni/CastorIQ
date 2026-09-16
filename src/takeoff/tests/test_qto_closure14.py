# takeoff/tests/test_qto_closure14.py
"""QTO-CLOSURE-14 — provenance grouping safety + coverage Status-only."""

from __future__ import annotations

from django.template.loader import render_to_string

from takeoff.services.quantity_engineering_groups import (
    ENGINEERING_GROUP_CONTRACT,
    filter_engineering_properties,
    is_parser_object_identity_key,
)


def test_engineering_contract_is_eg2():
    assert ENGINEERING_GROUP_CONTRACT == "eg2"


def test_unknown_id_suffix_not_stripped():
    assert not is_parser_object_identity_key("Identity Data.Mark")
    assert not is_parser_object_identity_key("Custom.EngineeringId")
    assert not is_parser_object_identity_key("Pset_ColumnCommon.Reference")
    assert is_parser_object_identity_key("Pset_ColumnCommon.id")
    assert is_parser_object_identity_key("Dimensions.id")
    filtered = filter_engineering_properties(
        {
            "Custom.SomethingId": "keep",
            "Other.id": 3,
            "Dimensions.b": 400,
        }
    )
    assert "Custom.SomethingId" in filtered
    assert "Other.id" not in filtered
    assert "Dimensions.b" in filtered


def test_coverage_visible_only_in_status_cell():
    row = {
        "level": "class",
        "ifc_class": "IfcColumn",
        "total": 10.57,
        "total_display": "10.57",
        "measurement_status": "resolved",
        "quantity_coverage_partial": True,
        "quantity_coverage_label": "120/121 · 1 missing",
        "review_status_display": "Partial",
    }
    qty = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": {"key": "quantity"}, "row": row},
    )
    status = render_to_string(
        "takeoff/components/quantities_prep_cell.html",
        {"col": {"key": "status"}, "row": row},
    )
    assert "10.57" in qty
    assert 'data-testid="qty-prep-quantity-coverage"' not in qty
    assert 'data-qty-coverage-label="120/121 · 1 missing"' in qty
    import re

    sans_attr = re.sub(r'\s*data-qty-coverage-label="[^"]*"', "", qty)
    assert "120/121" not in sans_attr
    assert "1 missing" not in sans_attr
    assert "120/121 · 1 missing" in status
    assert 'data-testid="qty-prep-status-coverage"' in status
