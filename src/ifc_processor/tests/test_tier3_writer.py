# ifc_processor/tests/test_tier3_writer.py
"""Integration tests for Tier3Writer against the simple_wall fixture.

Real IfcOpenShell, no mocks — the whole point is to catch API-signature
drift and wrong-class dispatch, which mocked tests structurally cannot see
(that is exactly how the `products=`/`product=` bugs shipped twice).
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import pytest

from ifc_processor.services.ifc_writer import IFCWriteError
from ifc_processor.services.tier3_writer import CREATABLE_CLASSES, Tier3Writer

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "t3writer.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return dest


@pytest.fixture
def writer(ifc_copy: Path) -> Tier3Writer:
    return Tier3Writer(ifc_copy)


def _storey_guid(writer: Tier3Writer) -> str:
    return writer.model.by_type("IfcBuildingStorey")[0].GlobalId


# ── create ────────────────────────────────────────────────────────


@pytest.mark.slow
def test_create_zone_mints_a_real_global_id(writer: Tier3Writer):
    change = writer.create_entity("IfcZone", "Fire Zone A")

    assert len(change.global_id) == 22, "IFC GlobalIds are 22-char base64"
    assert change.ifc_type == "IfcZone"
    assert change.pset == "(entity)"
    assert change.property == "CREATE"
    assert writer.model.by_guid(change.global_id).Name == "Fire Zone A"


@pytest.mark.slow
def test_create_space_aggregates_under_the_storey(writer: Tier3Writer):
    """A space DECOMPOSES a storey (IfcRelAggregates), not 'contained in'."""
    storey_guid = _storey_guid(writer)

    change = writer.create_entity(
        "IfcSpace",
        "Server Room",
        long_name="Server Room (secure)",
        parent_global_id=storey_guid,
        parent_relation="aggregate",
    )

    space = writer.model.by_guid(change.global_id)
    storey = writer.model.by_guid(storey_guid)
    decomposed = [obj for rel in storey.IsDecomposedBy for obj in rel.RelatedObjects]
    assert space in decomposed
    assert space.LongName == "Server Room (secure)"


@pytest.mark.slow
def test_create_zone_assigns_members(writer: Tier3Writer):
    change = writer.create_entity("IfcZone", "Wall Zone", member_global_ids=[WALL1_GUID])

    zone = writer.model.by_guid(change.global_id)
    members = [obj for rel in zone.IsGroupedBy for obj in rel.RelatedObjects]
    assert writer.model.by_guid(WALL1_GUID) in members


@pytest.mark.slow
def test_create_material_is_non_rooted(writer: Tier3Writer):
    """IfcMaterial has no GlobalId — it never enters the entity index."""
    change = writer.create_entity("IfcMaterial", "Concrete C30/37")

    assert change.global_id == ""
    assert any(m.Name == "Concrete C30/37" for m in writer.model.by_type("IfcMaterial"))


@pytest.mark.slow
def test_create_rejects_physical_classes(writer: Tier3Writer):
    """Geometry is out of scope — authoring a wall must be refused."""
    assert "IfcWall" not in CREATABLE_CLASSES
    with pytest.raises(IFCWriteError, match="only"):
        writer.create_entity("IfcWall", "New Wall")


@pytest.mark.slow
def test_create_rejects_blank_name(writer: Tier3Writer):
    with pytest.raises(IFCWriteError, match="without a name"):
        writer.create_entity("IfcZone", "   ")


@pytest.mark.slow
def test_create_rejects_aggregating_a_zone(writer: Tier3Writer):
    """Only spaces decompose a spatial parent."""
    with pytest.raises(IFCWriteError, match="cannot be aggregated"):
        writer.create_entity(
            "IfcZone", "Z", parent_global_id=_storey_guid(writer), parent_relation="aggregate"
        )


@pytest.mark.slow
def test_create_rejects_unknown_parent(writer: Tier3Writer):
    with pytest.raises(IFCWriteError, match="Entity not found"):
        writer.create_entity(
            "IfcSpace", "S", parent_global_id="0NoSuchGuid0000000000", parent_relation="aggregate"
        )


# ── delete ────────────────────────────────────────────────────────


@pytest.mark.slow
def test_delete_wall_uses_remove_product(writer: Tier3Writer):
    change = writer.delete_entity(WALL1_GUID)

    assert change.property == "DELETE"
    assert change.new_value == "(deleted)"
    assert change.ifc_type == "IfcWall"
    assert not writer._guid_exists(WALL1_GUID)


@pytest.mark.slow
def test_delete_zone_uses_group_remove_not_remove_product(writer: Tier3Writer):
    """THE dispatch regression: IfcZone is an IfcGroup, not an IfcProduct.

    root.remove_product is a silent no-op for it — the survival
    post-condition would raise if we dispatched wrongly.
    """
    created = writer.create_entity("IfcZone", "Doomed Zone")

    change = writer.delete_entity(created.global_id)

    assert change.ifc_type == "IfcZone"
    assert not writer._guid_exists(created.global_id)


@pytest.mark.slow
def test_delete_space_removes_it(writer: Tier3Writer):
    created = writer.create_entity(
        "IfcSpace", "Temp Room", parent_global_id=_storey_guid(writer), parent_relation="aggregate"
    )

    writer.delete_entity(created.global_id)

    assert not writer._guid_exists(created.global_id)


@pytest.mark.slow
def test_delete_unknown_guid_raises(writer: Tier3Writer):
    with pytest.raises(IFCWriteError, match="Entity not found"):
        writer.delete_entity("0NoSuchGuid0000000000")


@pytest.mark.slow
def test_delete_captures_identity_before_removing(writer: Tier3Writer):
    """The change row must describe what was deleted — read before removal."""
    change = writer.delete_entity(WALL1_GUID)
    assert change.entity_name == "TestWall-001"
    assert change.old_value == "TestWall-001"


# ── assign_container ──────────────────────────────────────────────


def _second_storey(writer: Tier3Writer):
    """Add a storey the fixture doesn't have, aggregated under the building."""
    storey = ifcopenshell.api.run(
        "root.create_entity", writer.model, ifc_class="IfcBuildingStorey", name="Level 1"
    )
    building = writer.model.by_type("IfcBuilding")[0]
    ifcopenshell.api.run(
        "aggregate.assign_object", writer.model, products=[storey], relating_object=building
    )
    return storey


@pytest.mark.slow
def test_assign_container_moves_a_wall_between_storeys(writer: Tier3Writer):
    destination = _second_storey(writer)

    change = writer.assign_container(WALL1_GUID, destination.GlobalId)

    moved_to = ifcopenshell.util.element.get_container(writer.model.by_guid(WALL1_GUID))
    assert moved_to.GlobalId == destination.GlobalId
    assert change.old_value == "Level 0"
    assert change.new_value == "Level 1"
    assert change.property == "CONTAINER"


@pytest.mark.slow
def test_assign_container_refuses_a_space(writer: Tier3Writer):
    """A space DECOMPOSES its storey; re-containing it would break the tree."""
    space = writer.create_entity(
        "IfcSpace",
        "Server Room",
        parent_global_id=_storey_guid(writer),
        parent_relation="aggregate",
    )
    destination = _second_storey(writer)

    with pytest.raises(IFCWriteError, match="spatial structure"):
        writer.assign_container(space.global_id, destination.GlobalId)


@pytest.mark.slow
def test_assign_container_refuses_a_non_spatial_destination(writer: Tier3Writer):
    zone = writer.create_entity("IfcZone", "Fire Zone A")

    with pytest.raises(IFCWriteError, match="must be a spatial"):
        writer.assign_container(WALL1_GUID, zone.global_id)


@pytest.mark.slow
def test_assign_container_rejects_a_silent_no_op(writer: Tier3Writer, monkeypatch):
    """The API returns None on a no-op, so success is only provable by re-reading."""
    destination = _second_storey(writer)
    monkeypatch.setattr(ifcopenshell.api, "run", lambda *a, **k: None)

    with pytest.raises(IFCWriteError, match="did not take effect"):
        writer.assign_container(WALL1_GUID, destination.GlobalId)

    # Rolled back: the wall is still on its original storey.
    monkeypatch.undo()
    still = ifcopenshell.util.element.get_container(writer.model.by_guid(WALL1_GUID))
    assert still.Name == "Level 0"


@pytest.mark.slow
def test_assign_container_unknown_guid_raises(writer: Tier3Writer):
    with pytest.raises(IFCWriteError, match="Entity not found"):
        writer.assign_container(WALL1_GUID, "NOPE_NOT_A_GUID_00000")


# ── persistence ───────────────────────────────────────────────────


@pytest.mark.slow
def test_create_then_save_round_trips_to_disk(writer: Tier3Writer, ifc_copy: Path):
    change = writer.create_entity("IfcZone", "Persisted Zone")
    writer.save()

    reopened = ifcopenshell.open(str(ifc_copy))
    assert reopened.by_guid(change.global_id).Name == "Persisted Zone"
