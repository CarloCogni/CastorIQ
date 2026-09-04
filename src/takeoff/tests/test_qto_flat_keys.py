# takeoff/tests/test_qto_flat_keys.py
"""Flat dotted-key IFC Qto detection for Quantities (Phase 2).

Preserves origin/main quantities._extract_quantity (nested Qto dicts) while
validating flat-key recognition via ifc_qto_flags used by ModelQuantitiesService.
"""

from __future__ import annotations

from takeoff.services.ifc_qto_flags import entity_has_ifc_quantity, iter_ifc_quantity_measures
from takeoff.services.quantities import _extract_quantity


def test_flat_dotted_net_volume_detected():
    props = {"Qto_WallBaseQuantities.NetVolume": 12.5}
    assert entity_has_ifc_quantity(props) is True
    measures = list(iter_ifc_quantity_measures(props))
    assert any(name == "NetVolume" and num == 12.5 for _pset, name, num in measures)


def test_flat_dotted_net_side_area_detected():
    props = {
        "Qto_WallBaseQuantities.NetSideArea": 40.0,
        "Qto_WallBaseQuantities.NetVolume": 12.5,
    }
    assert entity_has_ifc_quantity(props) is True
    names = {name for _pset, name, _num in iter_ifc_quantity_measures(props)}
    assert "NetSideArea" in names
    assert "NetVolume" in names


def test_flat_dotted_slab_gross_area_detected():
    props = {"Qto_SlabBaseQuantities.GrossArea": 88.0}
    assert entity_has_ifc_quantity(props) is True
    measures = list(iter_ifc_quantity_measures(props))
    assert any(name == "GrossArea" and num == 88.0 for _pset, name, num in measures)


def test_nested_qto_dict_still_works_on_main_extract():
    """origin/main compute_qto path still reads nested Qto_* dicts."""
    props = {"Qto_WallBaseQuantities": {"NetVolume": 3.25}}
    qty, unit, source = _extract_quantity("IfcWall", props)
    assert qty == 3.25
    assert unit == "m³"
    assert source == "ifc"
    assert entity_has_ifc_quantity(props) is True


def test_invalid_and_nonnumeric_ignored():
    props = {
        "Qto_WallBaseQuantities.NetVolume": "not-a-number",
        "Qto_WallBaseQuantities.NetSideArea": True,
        "Pset_WallCommon.IsExternal": True,
    }
    assert entity_has_ifc_quantity(props) is False
    qty, _unit, source = _extract_quantity("IfcWall", props)
    assert qty is None
    assert source == "estimated"


def test_id_only_qto_not_availability():
    assert entity_has_ifc_quantity({"Qto_BeamBaseQuantities.id": 12345}) is False
    assert entity_has_ifc_quantity({"Qto_BeamBaseQuantities": {"id": 99}}) is False
