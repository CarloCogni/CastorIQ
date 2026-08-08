# takeoff/tests/test_qto_flat_keys.py
"""Package B1 — QTO flat dotted-key extraction."""

from __future__ import annotations

from takeoff.services.quantities import _extract_quantity, entity_has_ifc_quantity


def test_flat_dotted_net_volume_extracted():
    props = {"Qto_WallBaseQuantities.NetVolume": 12.5}
    qty, unit, source = _extract_quantity("IfcWall", props)
    assert qty == 12.5
    assert unit == "m³"
    assert source == "ifc"
    assert entity_has_ifc_quantity(props) is True


def test_flat_dotted_net_side_area_preferred_for_wall():
    props = {
        "Qto_WallBaseQuantities.NetSideArea": 40.0,
        "Qto_WallBaseQuantities.NetVolume": 12.5,
    }
    qty, unit, source = _extract_quantity("IfcWall", props)
    assert qty == 40.0
    assert unit == "m²"
    assert source == "ifc"


def test_flat_dotted_slab_gross_area_via_generic_scan():
    """GrossArea is not first in slab spec but must still be recognized as Qto_*."""
    props = {"Qto_SlabBaseQuantities.GrossArea": 88.0}
    assert entity_has_ifc_quantity(props) is True
    qty, unit, source = _extract_quantity("IfcSlab", props)
    # Spec tries NetArea then NetVolume then GrossVolume — GrossArea falls to generic.
    assert qty == 88.0
    assert unit == "m²"
    assert source == "ifc"


def test_nested_qto_dict_still_works():
    props = {"Qto_WallBaseQuantities": {"NetVolume": 3.25}}
    qty, unit, source = _extract_quantity("IfcWall", props)
    assert qty == 3.25
    assert unit == "m³"
    assert source == "ifc"


def test_invalid_and_nonnumeric_ignored():
    props = {
        "Qto_WallBaseQuantities.NetVolume": "not-a-number",
        "Qto_WallBaseQuantities.NetSideArea": True,
        "Pset_WallCommon.IsExternal": True,
    }
    qty, unit, source = _extract_quantity("IfcWall", props)
    assert qty is None
    assert source == "estimated"
    assert entity_has_ifc_quantity(props) is False


def test_empty_props_estimated():
    qty, unit, source = _extract_quantity("IfcWall", {})
    assert qty is None
    assert unit == "ea"
    assert source == "estimated"
    assert entity_has_ifc_quantity({}) is False
    assert entity_has_ifc_quantity(None) is False


def test_qto_id_alone_is_not_real_quantity():
    """Regression: Qto_*.id must not count as Has IFC Qto or primary extract."""
    props = {"Qto_BeamBaseQuantities.id": 197394.0}
    assert entity_has_ifc_quantity(props) is False
    qty, unit, source = _extract_quantity("IfcBeam", props)
    assert qty is None
    assert source == "estimated"


def test_linear_measure_unit_is_model_units():
    props = {"Qto_BeamBaseQuantities.Length": 5325.0}
    qty, unit, source = _extract_quantity("IfcBeam", props)
    assert qty == 5325.0
    assert unit == "model units"
    assert source == "ifc"
