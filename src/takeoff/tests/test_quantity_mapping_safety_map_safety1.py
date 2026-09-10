# takeoff/tests/test_quantity_mapping_safety_map_safety1.py
"""MAP-SAFETY-1 — mixed selection safety analysis."""

from __future__ import annotations

import pytest

from takeoff.services.quantity_mapping_safety import (
    CONSISTENT_MESSAGE,
    MIXED_MESSAGE,
    analyze_batch_selection_safety,
    collect_dimension_values,
)


@pytest.mark.django_db
def test_all_same_ifc_class_returns_consistent():
    """Homogeneous IFC class / basis / unit → consistent status."""
    rows = [
        {
            "ifc_class": "IfcWall",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "m³",
            "semantic_category": "Walls",
        },
        {
            "ifc_class": "IfcWall",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "m³",
            "semantic_category": "Walls",
        },
    ]
    out = analyze_batch_selection_safety(rows)
    assert out["status"] == "consistent"
    assert out["selected_count"] == 2
    assert out["warnings"] == []
    assert out["message"] == CONSISTENT_MESSAGE
    assert out["apply_label"] == "Apply to session"


@pytest.mark.django_db
def test_mixed_ifc_class_returns_warning():
    """Mixed IFC classes produce a warning with samples."""
    rows = [
        {"ifc_class": "IfcWall", "quantity_basis": "NetVolume", "unit_basis_display": "m³"},
        {"ifc_class": "IfcBeam", "quantity_basis": "NetVolume", "unit_basis_display": "m³"},
    ]
    out = analyze_batch_selection_safety(rows)
    assert out["status"] == "mixed"
    assert out["message"] == MIXED_MESSAGE
    assert out["apply_label"] == "Apply anyway to selected rows"
    warn = next(w for w in out["warnings"] if w["field"] == "ifc_class")
    assert warn["value_count"] == 2
    assert warn["sample_values"] == ["IfcBeam", "IfcWall"]


@pytest.mark.django_db
def test_mixed_basis_and_unit_return_warnings():
    """Measurement Basis and Unit are separate warning dimensions."""
    rows = [
        {"ifc_class": "IfcWall", "quantity_basis": "NetVolume", "unit_basis_display": "m³"},
        {"ifc_class": "IfcWall", "quantity_basis": "Count", "unit_basis_display": "count"},
    ]
    out = analyze_batch_selection_safety(rows)
    fields = {w["field"] for w in out["warnings"]}
    assert "quantity_basis" in fields
    assert "unit_basis_display" in fields
    assert out["status"] == "mixed"


@pytest.mark.django_db
def test_empty_and_unresolved_values_ignored():
    """Empty/unresolved placeholders do not create false mixed warnings."""
    rows = [
        {
            "ifc_class": "IfcWall",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "Unit not resolved",
            "semantic_category": "",
        },
        {
            "ifc_class": "IfcWall",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "",
            "semantic_category": "—",
        },
    ]
    out = analyze_batch_selection_safety(rows)
    assert out["status"] == "consistent"
    assert out["warnings"] == []


@pytest.mark.django_db
def test_samples_capped_and_no_raw_json_message():
    """Sample values capped; message is human prose."""
    rows = [{"ifc_class": f"IfcThing{i}", "quantity_basis": "Count"} for i in range(6)]
    out = analyze_batch_selection_safety(rows, max_samples=4)
    warn = next(w for w in out["warnings"] if w["field"] == "ifc_class")
    assert len(warn["sample_values"]) == 4
    assert warn["samples_capped"] is True
    assert "{" not in out["message"]
    assert "sample_values" not in out["message"]


@pytest.mark.django_db
def test_unit_falls_back_to_unit_basis_token():
    """When display missing, unit_basis token is used for Unit dimension."""
    rows = [
        {"ifc_class": "IfcWall", "quantity_basis": "NetVolume", "unit_basis": "m³"},
        {"ifc_class": "IfcWall", "quantity_basis": "NetVolume", "unit_basis": "m²"},
    ]
    values = collect_dimension_values(rows, field="unit_basis_display", fallback="unit_basis")
    assert values == ["m²", "m³"]
    out = analyze_batch_selection_safety(rows)
    assert any(w["field"] == "unit_basis_display" for w in out["warnings"])


@pytest.mark.django_db
def test_type_name_not_a_warning_dimension():
    """Different type names alone do not trigger mixed status."""
    rows = [
        {
            "ifc_class": "IfcWall",
            "type_name": "Basic Wall A",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "m³",
        },
        {
            "ifc_class": "IfcWall",
            "type_name": "Basic Wall B",
            "quantity_basis": "NetVolume",
            "unit_basis_display": "m³",
        },
    ]
    out = analyze_batch_selection_safety(rows)
    assert out["status"] == "consistent"
    assert not any(w["field"] == "type_name" for w in out["warnings"])
