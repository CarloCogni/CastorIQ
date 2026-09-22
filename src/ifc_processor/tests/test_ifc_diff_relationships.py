# ifc_processor/tests/test_ifc_diff_relationships.py
"""Relationship changes and typed population changes are visible in the diff.

A container move, a material or classification assignment, a group membership
or a created zone changes no property; the snapshot tracks them as attributes
of the object so the pipeline's scope check and card can see them.
"""

from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import pytest

from ifc_processor.services.ifc_diff import IfcSnapshot, diff_snapshots

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "Ifc4_SampleHouse.ifc"


@pytest.fixture
def house():
    return ifcopenshell.open(str(FIXTURE))


def _rows(diff):
    return {(c.global_id, c.prop): (c.before, c.after) for c in diff.attribute_changes}


@pytest.mark.slow
def test_container_move_shows_as_a_container_attribute_change(house):
    """Moving a wall to the Roof storey is one attribute row, Ground Floor → Roof."""
    wall = house.by_type("IfcWall")[0]
    roof = next(s for s in house.by_type("IfcBuildingStorey") if s.Name == "Roof")
    before = IfcSnapshot.from_model(house)

    ifcopenshell.api.run(
        "spatial.assign_container", house, products=[wall], relating_structure=roof
    )

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert _rows(diff)[(wall.GlobalId, "Container")] == ("Ground Floor", "Roof")
    assert diff.unexpected(allowed={wall.GlobalId}, allow_population_change=True) == []


@pytest.mark.slow
def test_material_classification_and_group_show_on_the_object(house):
    """Assigning a material, a classification and a group changes the object's rows, not a property."""
    wall = house.by_type("IfcWall")[0]
    before = IfcSnapshot.from_model(house)

    material = ifcopenshell.api.run("material.add_material", house, name="Concrete C30")
    ifcopenshell.api.run(
        "material.assign_material", house, products=[wall], type="IfcMaterial", material=material
    )
    system = ifcopenshell.api.run(
        "classification.add_classification", house, classification="Uniclass"
    )
    ifcopenshell.api.run(
        "classification.add_reference",
        house,
        products=[wall],
        identification="EF_25_10",
        name="Walls",
        classification=system,
    )
    zone = ifcopenshell.api.run("group.add_group", house, name="Fire Zone A")
    ifcopenshell.api.run("group.assign_group", house, products=[wall], group=zone)

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    rows = _rows(diff)
    assert rows[(wall.GlobalId, "Materials")][1] == ("Concrete C30",)
    assert rows[(wall.GlobalId, "Classifications")][1] == ("EF_25_10",)
    assert rows[(wall.GlobalId, "Groups")][1] == ("Fire Zone A",)
    assert diff.added_objects == {zone.GlobalId: "IfcGroup"}
    assert diff.as_dict()["attribute_changes"][0]["after"].__class__ is list


@pytest.mark.slow
def test_deleting_a_window_is_a_typed_removed_object(house):
    """root.remove_product on a window shows the window in removed_objects with its class."""
    window = house.by_type("IfcWindow")[0]
    window_id = window.GlobalId
    before = IfcSnapshot.from_model(house)

    ifcopenshell.api.run("root.remove_product", house, product=window)

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert diff.removed_objects.get(window_id) == "IfcWindow"
    assert all(not cls.startswith("IfcRel") for cls in diff.removed_objects.values())


@pytest.mark.slow
def test_editing_a_type_pset_is_one_change_on_the_type_only(house):
    """Occurrences do not report inherited values: the type is the one entity that changed."""
    wall_type = house.by_type("IfcWallType")[0]
    before = IfcSnapshot.from_model(house)

    pset = ifcopenshell.api.run("pset.add_pset", house, product=wall_type, name="Pset_WallCommon")
    ifcopenshell.api.run("pset.edit_pset", house, pset=pset, properties={"FireRating": "EI60"})

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert {c.global_id for c in diff.property_changes} == {wall_type.GlobalId}
    assert diff.unexpected(allowed={wall_type.GlobalId}, allow_population_change=True) == []


@pytest.mark.slow
def test_an_empty_pset_added_is_one_presence_row(house):
    """A pset with no properties yet is visible as one row named after the pset, not as an entity."""
    wall = house.by_type("IfcWall")[0]
    before = IfcSnapshot.from_model(house)

    ifcopenshell.api.run("pset.add_pset", house, product=wall, name="Pset_FireCompliance")

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert [(c.global_id, c.pset, c.prop, c.before, c.after) for c in diff.property_changes] == [
        (wall.GlobalId, "Pset_FireCompliance", "", None, "Pset_FireCompliance")
    ]
    assert diff.added_objects == {}


@pytest.mark.slow
def test_material_and_classification_on_a_type_are_rows_on_the_type_only(house):
    """A type object's own materials and classifications are tracked, the occurrences (which
    inherit them) report nothing, and the project that registers the classification system is
    not tracked, so the change is one row on the type."""
    from writeback.services.verifier import is_empty

    wall_type = house.by_type("IfcWallType")[0]
    occurrences = [e.GlobalId for e in ifcopenshell.util.element.get_types(wall_type)]
    assert occurrences
    before = IfcSnapshot.from_model(house)

    material = ifcopenshell.api.run("material.add_material", house, name="Concrete C30")
    ifcopenshell.api.run(
        "material.assign_material",
        house,
        products=[wall_type],
        type="IfcMaterial",
        material=material,
    )
    system = ifcopenshell.api.run(
        "classification.add_classification", house, classification="Uniclass"
    )
    ifcopenshell.api.run(
        "classification.add_reference",
        house,
        products=[wall_type],
        identification="EF_25_10",
        name="Walls",
        classification=system,
    )

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    rows = _rows(diff)
    assert rows[(wall_type.GlobalId, "Materials")][1] == ("Concrete C30",)
    assert rows[(wall_type.GlobalId, "Classifications")][1] == ("EF_25_10",)
    assert {gid for gid, _ in rows} == {wall_type.GlobalId}
    assert diff.unexpected(allowed={wall_type.GlobalId}, allow_population_change=True) == []
    assert not is_empty(diff.as_dict())


@pytest.mark.slow
def test_an_occurrence_without_its_own_material_reports_no_inherited_change(house):
    """Materials are snapshotted own-only: when a type's material changes, an occurrence that
    carries none of its own stays silent instead of becoming a scope violation."""
    wall_type = house.by_type("IfcWallType")[0]
    occurrence = ifcopenshell.util.element.get_types(wall_type)[0]
    ifcopenshell.api.run("material.unassign_material", house, products=[occurrence])
    assert ifcopenshell.util.element.get_materials(occurrence, should_inherit=False) == []
    before = IfcSnapshot.from_model(house)

    material = ifcopenshell.api.run("material.add_material", house, name="Concrete C30")
    ifcopenshell.api.run(
        "material.assign_material",
        house,
        products=[wall_type],
        type="IfcMaterial",
        material=material,
    )

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert (occurrence.GlobalId, "Materials") not in _rows(diff)
    assert diff.unexpected(allowed={wall_type.GlobalId}, allow_population_change=True) == []


# ── aggregation: Parent rows and the attachment of new objects ────


def _storey(house, name):
    return next(s for s in house.by_type("IfcBuildingStorey") if s.Name == name)


@pytest.mark.slow
def test_a_new_zone_aggregated_under_an_unselected_storey_is_a_violation(house):
    """The live 7B block: zone created with the project selected, then hung from the Roof."""
    project = house.by_type("IfcProject")[0]
    roof = _storey(house, "Roof")
    before = IfcSnapshot.from_model(house)

    zone = ifcopenshell.api.run("root.create_entity", house, ifc_class="IfcZone", name="Z")
    ifcopenshell.api.run("aggregate.assign_object", house, products=[zone], relating_object=roof)

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert diff.added_objects == {zone.GlobalId: "IfcZone"}
    assert diff.added_attachments == {zone.GlobalId: roof.GlobalId}
    problems = diff.unexpected(allowed={project.GlobalId}, allow_population_change=True)
    assert len(problems) == 1
    assert zone.GlobalId in problems[0] and roof.GlobalId in problems[0]
    assert diff.unexpected(allowed={roof.GlobalId}, allow_population_change=True) == []
    assert diff.as_dict()["added_attachments"] == {zone.GlobalId: roof.GlobalId}


@pytest.mark.slow
def test_a_new_space_under_the_selected_storey_passes(house):
    """Corpus 10.2: select() returned the storey, the space is aggregated under it."""
    ground = _storey(house, "Ground Floor")
    before = IfcSnapshot.from_model(house)

    space = ifcopenshell.api.run("root.create_entity", house, ifc_class="IfcSpace", name="S-01")
    ifcopenshell.api.run("aggregate.assign_object", house, products=[space], relating_object=ground)

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert diff.added_objects == {space.GlobalId: "IfcSpace"}
    assert diff.unexpected(allowed={ground.GlobalId}, allow_population_change=True) == []


@pytest.mark.slow
def test_a_new_zone_attached_to_nothing_passes(house):
    """A zone on its own hangs from nothing: no attachment, no violation."""
    project = house.by_type("IfcProject")[0]
    before = IfcSnapshot.from_model(house)

    ifcopenshell.api.run("root.create_entity", house, ifc_class="IfcZone", name="Z")

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert diff.added_attachments == {}
    assert diff.unexpected(allowed={project.GlobalId}, allow_population_change=True) == []


@pytest.mark.slow
def test_moving_a_space_to_another_storey_is_one_parent_row_on_the_space(house):
    """Re-parenting an existing object is charged to the object, like a container move."""
    space = next(
        s
        for s in house.by_type("IfcSpace")
        if s.Decomposes[0].RelatingObject.Name == "Ground Floor"
    )
    roof = _storey(house, "Roof")
    before = IfcSnapshot.from_model(house)

    ifcopenshell.api.run("aggregate.assign_object", house, products=[space], relating_object=roof)

    diff = diff_snapshots(before, IfcSnapshot.from_model(house))
    assert _rows(diff)[(space.GlobalId, "Parent")] == ("Ground Floor", "Roof")
    assert diff.unexpected(allowed={space.GlobalId}, allow_population_change=True) == []
