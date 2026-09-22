# ifc_processor/tests/test_description_builder.py
"""Tests for the RAG description builder.

IfcOpenShell graph walks are mocked at the boundary (element_util /
classification_util); property/quantity assembly runs on plain dicts.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ifc_processor.services.description_builder import DescriptionBuilder


class FakeElement:
    """Minimal stand-in for an ifcopenshell entity instance."""

    def __init__(self, ifc_class="IfcWallStandardCase", name="W-01", **attrs):
        self._ifc_class = ifc_class
        self.Name = name
        for key, value in attrs.items():
            setattr(self, key, value)

    def is_a(self, ifc_class=None):
        if ifc_class is None:
            return self._ifc_class
        return ifc_class == self._ifc_class


class FakeMaterial:
    def __init__(self, name):
        self.Name = name

    def is_a(self, ifc_class=None):
        if ifc_class is None:
            return "IfcMaterial"
        return ifc_class == "IfcMaterial"


class FakeLayer:
    def __init__(self, material_name, thickness):
        self.Material = FakeMaterial(material_name)
        self.LayerThickness = thickness


class FakeLayerSet:
    def __init__(self, layers):
        self.MaterialLayers = layers

    def is_a(self, ifc_class=None):
        if ifc_class is None:
            return "IfcMaterialLayerSet"
        return ifc_class == "IfcMaterialLayerSet"


@pytest.fixture
def builder():
    return DescriptionBuilder({"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"})


@pytest.fixture(autouse=True)
def _no_graph_walks():
    """Default every IFC graph walk to empty; tests override per case."""
    with (
        patch(
            "ifc_processor.services.description_builder.element_util.get_material",
            return_value=None,
        ),
        patch(
            "ifc_processor.services.description_builder.element_util.get_type",
            return_value=None,
        ),
        patch(
            "ifc_processor.services.description_builder.classification_util.get_references",
            return_value=[],
        ),
    ):
        yield


def test_identity_includes_type_object_name(builder):
    """The defining type name ('Basic Wall: Interior') is finally searchable."""
    text = builder.build(FakeElement(), {}, type_name="Basic Wall: Interior")
    assert "named 'W-01'" in text
    assert "of type 'Basic Wall: Interior'" in text


def test_identity_includes_long_name_for_spaces(builder):
    """IfcSpace LongName ('Meeting Room') matters more than the number."""
    element = FakeElement(ifc_class="IfcSpace", name="101", LongName="Meeting Room")
    text = builder.build(element, {})
    assert "also known as 'Meeting Room'" in text


def test_material_layer_set_lists_layers_with_thickness(builder):
    """Wall build-up questions need layer names AND thicknesses with units."""
    layer_set = FakeLayerSet([FakeLayer("Brick", 102.0), FakeLayer("Insulation", 50.0)])
    with patch(
        "ifc_processor.services.description_builder.element_util.get_material",
        return_value=layer_set,
    ):
        text = builder.build(FakeElement(), {})
    assert "Material layers: Brick (102 mm), Insulation (50 mm)" in text


def test_single_material_sentence(builder):
    """A direct IfcMaterial association renders as a simple material fact."""
    with patch(
        "ifc_processor.services.description_builder.element_util.get_material",
        return_value=FakeMaterial("Concrete C30/37"),
    ):
        text = builder.build(FakeElement(), {})
    assert "Material: Concrete C30/37" in text


def test_common_pset_properties_carry_provenance(builder):
    """Pset_WallCommon props appear under their pset name, not bare."""
    flat = {"Pset_WallCommon.FireRating": "EI60", "Pset_WallCommon.IsExternal": True}
    text = builder.build(FakeElement(), flat)
    assert "Pset_WallCommon: FireRating=EI60, IsExternal=True" in text


def test_quantities_render_with_project_units(builder):
    """Qto values finally carry units — SYSTEM_PROMPT rule 6 becomes satisfiable."""
    flat = {
        "Qto_WallBaseQuantities.NetSideArea": 12.34,
        "Qto_WallBaseQuantities.NetVolume": 3.7,
    }
    text = builder.build(FakeElement(), flat)
    assert "Quantities (Qto_WallBaseQuantities):" in text
    assert "NetSideArea=12.34 m²" in text
    assert "NetVolume=3.7 m³" in text


def test_custom_pset_props_matched_by_keyword_fallback(builder):
    """Non-standard psets still surface material/dimension-ish properties."""
    flat = {"AC_Pset_RenovationAndPhasing.Height": 3000, "AC_Pset_X.Irrelevant": "x"}
    text = builder.build(FakeElement(), flat)
    assert "Height=3000" in text
    assert "Irrelevant" not in text


def test_bare_dimension_attributes_render_with_length_unit(builder):
    """Door OverallWidth/OverallHeight become a Dimensions sentence."""
    element = FakeElement(ifc_class="IfcDoor", name="D-01")
    text = builder.build(element, {"OverallWidth": 1000.0, "OverallHeight": 2100.0})
    assert "Dimensions:" in text
    assert "OverallWidth=1000 mm" in text


def test_fills_opening_names_host_wall(builder):
    """Doors/windows finally answer 'which wall is this door in'."""

    class Rel:
        pass

    host = FakeElement(ifc_class="IfcWallStandardCase", name="W-07")
    void_rel = Rel()
    void_rel.RelatingBuildingElement = host
    opening = FakeElement(ifc_class="IfcOpeningElement", name="O-1")
    opening.VoidsElements = [void_rel]
    fill_rel = Rel()
    fill_rel.RelatingOpeningElement = opening
    door = FakeElement(ifc_class="IfcDoor", name="D-01", FillsVoids=[fill_rel])

    text = builder.build(door, {})
    assert "Fills an opening in WallStandardCase 'W-07'" in text


def test_graph_walk_failure_degrades_to_no_sentence(builder):
    """A crashing relationship walk loses one sentence, never the description."""
    with patch(
        "ifc_processor.services.description_builder.element_util.get_material",
        side_effect=RuntimeError("malformed model"),
    ):
        text = builder.build(FakeElement(), {"Pset_WallCommon.IsExternal": True})
    assert "This is a WallStandardCase" in text
    assert "Pset_WallCommon: IsExternal=True" in text


def test_empty_units_omit_unit_suffix():
    """Models without a unit assignment render bare numbers, not '12.3 '."""
    builder = DescriptionBuilder(None)
    text = builder.build(FakeElement(), {"Qto_WallBaseQuantities.NetSideArea": 12.3})
    assert "NetSideArea=12.3" in text
    assert "NetSideArea=12.3 " not in text.replace("NetSideArea=12.3, ", "")
