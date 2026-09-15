# ifc_processor/tests/test_castor_select.py
"""Every `castor_select` helper against the benchmark sample house.

The fixture facts these tests rely on (two storeys, four spaces, five walls
of which two are internal partitions, three doors placed on the storey, twelve
furniture items in the living room) are the same facts the V3 docs quote.
"""

from pathlib import Path

import ifcopenshell
import pytest

from ifc_processor.services import castor_select as cs

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "Ifc4_SampleHouse.ifc"


@pytest.fixture(scope="module")
def house():
    return ifcopenshell.open(str(FIXTURE))


def test_all_eight_helpers_are_exported():
    """The prompt lists exactly these names; nothing more, nothing less."""
    assert cs.__all__ == [
        "elements_in_storey",
        "elements_in_space",
        "by_type",
        "by_name",
        "by_pset_value",
        "by_material",
        "decomposition_of",
        "container_of",
    ]
    assert all(callable(getattr(cs, name)) for name in cs.__all__)


def test_elements_in_storey_is_case_insensitive_and_excludes_spaces(house):
    """The ground floor holds elements but the four spaces are not elements."""
    elements = cs.elements_in_storey(house, "ground floor")

    assert elements
    assert not any(e.is_a("IfcSpace") for e in elements)
    assert len(cs.by_type(elements, "IfcWall")) == 5


def test_elements_in_storey_unknown_name_is_empty(house):
    """A storey that does not exist yields nothing, not an exception."""
    assert cs.elements_in_storey(house, "Level 7") == []


def test_elements_in_space_finds_the_living_room_furniture(house):
    """Twelve furniture items sit inside '1 - Living room'."""
    furniture = cs.by_type(cs.elements_in_space(house, "1 - Living room"), "IfcFurniture")
    assert len(furniture) == 12


def test_by_type_includes_subclasses_and_accepts_the_model(house):
    """IfcWallStandardCase is a kind of IfcWall; the model itself is a valid source."""
    assert len(cs.by_type(house, "IfcWall")) == 5
    assert len(cs.by_type(cs.by_type(house, "IfcWall"), "IfcWallStandardCase")) == 2


def test_by_name_substring_and_glob(house):
    """A bare pattern is a substring; a pattern with * is a glob."""
    walls = cs.by_type(house, "IfcWall")
    assert len(cs.by_name(walls, "partn")) == 2
    assert len(cs.by_name(walls, "*Wall-Ext*")) == 3


def test_by_pset_value_matches_booleans_and_strings_case_insensitively(house):
    """IsExternal=False picks the two partitions; string values ignore case."""
    walls = cs.by_type(house, "IfcWall")
    assert len(cs.by_pset_value(walls, "Pset_WallCommon", "IsExternal", False)) == 2
    doors = cs.by_type(house, "IfcDoor")
    assert len(cs.by_pset_value(doors, "Pset_DoorCommon", "IsExternal", False)) == 2
    assert cs.by_pset_value(walls, "Pset_WallCommon", "Reference", "wall-partn_12p-70mstd-12p")


def test_by_material_filters_on_material_name(house):
    """A material name substring keeps only elements carrying that material."""
    walls = cs.by_type(house, "IfcWall")
    names = {
        m.Name
        for w in walls
        for m in __import__("ifcopenshell.util.element").util.element.get_materials(w)
    }
    some_name = sorted(names)[0]
    assert cs.by_material(walls, some_name)
    assert cs.by_material(walls, "no-such-material-xyz") == []


def test_decomposition_of_and_container_of_round_trip(house):
    """A wall's container is the ground floor storey, which decomposes to the wall."""
    wall = cs.by_type(house, "IfcWall")[0]
    storey = cs.container_of(wall)
    assert storey.is_a("IfcBuildingStorey")
    assert wall in cs.decomposition_of(storey)


def test_elements_in_storey_excludes_openings(house):
    """Openings are feature elements, not things a user names; the ground floor has seven."""
    elements = cs.elements_in_storey(house, "Ground Floor")

    assert not any(e.is_a("IfcOpeningElement") for e in elements)
    assert len(elements) == 58
