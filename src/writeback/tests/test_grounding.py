# writeback/tests/test_grounding.py
"""Grounding is a lookup: full lists, exact strings, one named match (spec G-1)."""

from unittest.mock import patch

import pytest

from ifc_processor.tests.factories import IFCEntityFactory, IFCSpatialElementFactory
from writeback.services.grounding import SPACE_CAP, build_grounding, match_types


def _storey(ifc_file, name, elevation):
    entity = IFCEntityFactory(
        ifc_file=ifc_file, ifc_type="IfcBuildingStorey", name=name, properties={}
    )
    return IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=entity, spatial_type="building_storey", elevation=elevation
    )


def _space(ifc_file, name, storey):
    entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcSpace", name=name, properties={})
    return IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=entity, spatial_type="space", parent=storey
    )


@pytest.fixture
def house(ifc_file):
    """Two storeys, two spaces, three walls with Pset_WallCommon, two doors."""
    ground = _storey(ifc_file, "Ground Floor", 0)
    _storey(ifc_file, "Roof", 2500)
    _space(ifc_file, "1 - Living room", ground)
    _space(ifc_file, "2 - Bedroom", ground)
    for i in range(3):
        IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcWall",
            name=f"Wall-{i}",
            properties={
                "Pset_WallCommon.IsExternal": True,
                "Pset_WallCommon.Reference": "x",
                "Type.Pset_WallCommon.LoadBearing": False,
                "ClassRef.Uniclass": "Ss",
            },
        )
    for i in range(2):
        IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcDoor",
            name=f"Door-{i}",
            properties={
                "Pset_DoorCommon.IsExternal": False,
                "Pset_DoorCommon.Reference": "810x2110mm",
                "Pset_DoorCommon.ThermalTransmittance": 3.7021,
                "Constraints.Level": "Ground Floor",
                "OverallWidth": 810,
            },
        )
    return ifc_file


@pytest.mark.django_db
def test_grounding_lists_every_storey_and_space_as_exact_strings(house):
    """Both storeys with elevations and both spaces with their storey are injected verbatim."""
    grounding = build_grounding(
        house, "Set the fire rating of the walls on the first floor to EI60"
    )

    assert '- "Ground Floor" · 0' in grounding.text
    assert '- "Roof" · 2500' in grounding.text
    assert '- "1 - Living room" · Ground Floor' in grounding.text
    assert grounding.storey_count == 2
    assert grounding.space_count == 2


@pytest.mark.django_db
def test_first_floor_is_not_resolved_by_grounding(house):
    """'first floor' matches no storey and the text says nothing about it: the model decides."""
    grounding = build_grounding(house, "walls on the first floor")
    assert "first floor" not in grounding.text.lower()


@pytest.mark.django_db
def test_type_counts_and_matched_type_psets(house):
    """'walls' selects IfcWall for the pset list; doors get none; Type./ClassRef. keys are skipped."""
    grounding = build_grounding(house, "Set the fire rating of all walls to EI60")

    assert "IfcWall 3" in grounding.text
    assert "IfcDoor 2" in grounding.text
    assert grounding.matched_types == ("IfcWall",)
    assert "## Property sets on IfcWall (3 entities)" in grounding.text
    assert "- Pset_WallCommon: IsExternal, Reference · not yet set: " in grounding.text
    assert "Type." not in grounding.text
    assert "ClassRef" not in grounding.text
    assert "Pset_DoorCommon" not in grounding.text


@pytest.mark.django_db
def test_supertype_counts_state_the_subtype_split(house):
    """IfcWallStandardCase folds into IfcWall's line: by_type("IfcWall") already covers it."""
    for i in range(2):
        IFCEntityFactory(
            ifc_file=house, ifc_type="IfcWallStandardCase", name=f"Partition-{i}", properties={}
        )

    grounding = build_grounding(house, "change fire rating on all walls")

    assert (
        "IfcWall: 3 direct, plus 2 IfcWallStandardCase (subtype of IfcWall); "
        "model.by_type('IfcWall') returns all 5" in grounding.text
    )
    assert "IfcWallStandardCase 2" not in grounding.text


@pytest.mark.django_db
def test_grounding_for_the_sample_house_is_small(house):
    """A property request injects well under 1.5k tokens."""
    grounding = build_grounding(house, "Set the fire rating of all walls to EI60")
    assert grounding.token_estimate < 1500


@pytest.mark.django_db
def test_space_list_is_capped_with_a_warning(ifc_file):
    """Above the cap the list is cut and a warning is logged, never an error."""
    ground = _storey(ifc_file, "G", 0)
    for i in range(SPACE_CAP + 3):
        _space(ifc_file, f"Room {i:03d}", ground)

    with patch("writeback.services.grounding.logger") as log:
        grounding = build_grounding(ifc_file, "rename the rooms")

    assert grounding.space_count == SPACE_CAP
    assert log.warning.called


def test_match_types_is_a_plural_tolerant_substring_on_the_stem():
    """'walls' hits IfcWall and IfcWallStandardCase; short words never match."""
    types = ["IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcSite", "IfcSlab"]
    assert match_types("Set the fire rating of all walls to EI60", types) == [
        "IfcWall",
        "IfcWallStandardCase",
    ]
    assert match_types("doors in the hall", types) == ["IfcDoor"]
    assert match_types("to the on", types) == []


def test_match_types_is_capped_with_whole_stem_matches_first():
    """A generic word matches many types; the whole-stem match comes first, then by count."""
    types = {
        "IfcBuildingElementProxy": 900,
        "IfcBuilding": 1,
        "IfcBuildingStorey": 3,
        "IfcDistributionElement": 40,
        "IfcElementAssembly": 2,
        "IfcWall": 5,
        "IfcFurniture": 14,
    }
    matched = match_types("set IsExternal on every element of the building", types, limit=3)
    assert matched == ["IfcBuilding", "IfcBuildingElementProxy", "IfcDistributionElement"]


@pytest.mark.django_db
def test_unset_standard_properties_are_listed_per_pset(house):
    """The doors hold no FireRating: the catalogue offers it as not yet set, an exact string."""
    grounding = build_grounding(house, "change fire rating on all doors to 120")

    line = next(ln for ln in grounding.text.splitlines() if ln.startswith("- Pset_DoorCommon"))
    held, unset = line.split(" · not yet set: ")

    assert held == "- Pset_DoorCommon: IsExternal, Reference, ThermalTransmittance"
    assert "FireRating" in unset.split(", ")
    assert "ThermalTransmittance" not in unset
    assert grounding.token_estimate < 1500


@pytest.mark.django_db
def test_non_standard_psets_get_no_unset_suffix(house):
    """A Revit pset is not in the catalogue: its line lists what the index holds and nothing more."""
    grounding = build_grounding(house, "change fire rating on all doors to 120")

    assert "- Constraints: Level\n" in grounding.text


@pytest.mark.django_db
def test_type_without_indexed_psets_lists_the_standard_ones_as_not_yet_set(ifc_file):
    """No pset in the index for the type: the standard psets are offered, every property unset."""
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWindow", name="Win-1", properties={})

    grounding = build_grounding(ifc_file, "set the fire rating of the windows to EI30")

    assert "- Pset_WindowCommon: not yet set: " in grounding.text
    assert "FireRating" in grounding.text
