# ifc_processor/tests/test_ifc_writer_integration.py
"""
Integration tests for Tier1Writer against a real IFC file.

These tests call IfcOpenShell with no mocks, verifying that write operations
actually persist to disk and produce valid, re-readable IFC files.

Fixture: ifc_processor/tests/fixtures/simple_wall.ifc
    - Two IfcWall entities with Pset_WallCommon
    - Wall 1 GUID: 2O2Fr$t4X7Zf8NOew3FLOH  (FireRating=EI60, IsExternal=True, LoadBearing=False)
    - Wall 2 GUID: 3x4Kf8NOew3FLOHt4X7Zf8  (FireRating=EI60, IsExternal=False, LoadBearing=True)

Run only when explicitly targeting integration tests:
    cd src && uv run pytest ifc_processor/tests/test_ifc_writer_integration.py -v
    cd src && uv run pytest -m slow -v
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util
import pytest

from ifc_processor.services.ifc_writer import IFCWriteError, Tier1Writer

# Known GUIDs from the fixture file
WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
WALL2_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    """Copy the fixture IFC to a temp directory so each test gets a fresh file."""
    dest = tmp_path / "test.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return dest


# ── set_property round-trip ───────────────────────────────────────────────────


@pytest.mark.slow
def test_set_property_persists_string_value_to_file(ifc_copy: Path) -> None:
    """SET_PROPERTY: string change survives a full save → re-open cycle."""
    writer = Tier1Writer(ifc_copy)
    changes = writer.set_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI120")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)

    assert psets["Pset_WallCommon"]["FireRating"] == "EI120"
    assert len(changes) == 1
    assert changes[0].old_value == "EI60"
    assert changes[0].new_value == "EI120"
    assert changes[0].global_id == WALL1_GUID


@pytest.mark.slow
def test_set_property_boolean_coercion_persists(ifc_copy: Path) -> None:
    """SET_PROPERTY: 'true' string is coerced to bool True and persists correctly."""
    writer = Tier1Writer(ifc_copy)
    writer.set_property([WALL1_GUID], "Pset_WallCommon", "IsExternal", "false")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)

    # IsExternal was True in fixture; we set it to 'false' (string) → should be bool False
    assert psets["Pset_WallCommon"]["IsExternal"] is False


@pytest.mark.slow
def test_set_property_float_coercion_persists(ifc_copy: Path) -> None:
    """SET_PROPERTY: string '0.25' is coerced to float for ThermalTransmittance."""
    writer = Tier1Writer(ifc_copy)
    writer.set_property([WALL1_GUID], "Pset_WallCommon", "ThermalTransmittance", "0.25")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)

    assert psets["Pset_WallCommon"]["ThermalTransmittance"] == pytest.approx(0.25)


@pytest.mark.slow
def test_set_property_multiple_entities_all_changed(ifc_copy: Path) -> None:
    """SET_PROPERTY on two GUIDs produces two EntityChange records."""
    writer = Tier1Writer(ifc_copy)
    changes = writer.set_property(
        [WALL1_GUID, WALL2_GUID], "Pset_WallCommon", "FireRating", "EI180"
    )
    writer.save()

    assert len(changes) == 2
    assert all(c.new_value == "EI180" for c in changes)

    model = ifcopenshell.open(str(ifc_copy))
    for guid in [WALL1_GUID, WALL2_GUID]:
        element = model.by_guid(guid)
        psets = element_util.get_psets(element)
        assert psets["Pset_WallCommon"]["FireRating"] == "EI180"


@pytest.mark.slow
def test_set_property_rolls_back_on_missing_non_standard_pset(ifc_copy: Path) -> None:
    """SET_PROPERTY on a non-existent NON-STANDARD pset raises and leaves
    the file unmodified. Standard psets get upsert; custom psets stay strict."""
    writer = Tier1Writer(ifc_copy)

    with pytest.raises(IFCWriteError, match="not found"):
        writer.set_property([WALL1_GUID], "Pset_CustomCompliance", "Standard", "FS-2026")

    # File unchanged: original FireRating still EI60
    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)
    assert psets["Pset_WallCommon"]["FireRating"] == "EI60"


@pytest.mark.slow
def test_set_property_upserts_missing_property_on_standard_pset(ifc_copy: Path) -> None:
    """SET_PROPERTY on a non-existent property of a STANDARD pset adds the
    property (upsert) and the change persists after save → re-open."""
    writer = Tier1Writer(ifc_copy)

    changes = writer.set_property([WALL1_GUID], "Pset_WallCommon", "AcousticRating", "Rw45")
    writer.save()

    assert len(changes) == 1
    assert changes[0].old_value == "(none)"
    assert changes[0].new_value == "Rw45"

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)
    assert psets["Pset_WallCommon"]["AcousticRating"] == "Rw45"
    # Other properties untouched
    assert psets["Pset_WallCommon"]["FireRating"] == "EI60"


# ── add_property round-trip ───────────────────────────────────────────────────


@pytest.mark.slow
def test_add_property_new_property_persists(ifc_copy: Path) -> None:
    """ADD_PROPERTY: new property appears in pset after save → re-open."""
    writer = Tier1Writer(ifc_copy)
    changes = writer.add_property([WALL1_GUID], "Pset_WallCommon", "AcousticRating", "Rw45")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)

    assert "AcousticRating" in psets["Pset_WallCommon"]
    assert psets["Pset_WallCommon"]["AcousticRating"] == "Rw45"
    assert len(changes) == 1
    assert changes[0].old_value == "(none)"


@pytest.mark.slow
def test_add_property_raises_when_already_exists(ifc_copy: Path) -> None:
    """ADD_PROPERTY raises IFCWriteError when property already exists on the element."""
    writer = Tier1Writer(ifc_copy)

    with pytest.raises(IFCWriteError, match="already exists"):
        writer.add_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI120")


# ── remove_property round-trip ────────────────────────────────────────────────


@pytest.mark.slow
def test_remove_property_property_absent_after_save(ifc_copy: Path) -> None:
    """REMOVE_PROPERTY: property is gone from pset after save → re-open."""
    writer = Tier1Writer(ifc_copy)
    changes = writer.remove_property([WALL1_GUID], "Pset_WallCommon", "FireRating")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)
    psets = element_util.get_psets(element)

    assert "FireRating" not in psets["Pset_WallCommon"]
    assert len(changes) == 1
    assert changes[0].new_value == "(removed)"
    assert changes[0].old_value == "EI60"


# ── set_attribute round-trip ──────────────────────────────────────────────────


@pytest.mark.slow
def test_set_attribute_name_persists(ifc_copy: Path) -> None:
    """SET_ATTRIBUTE: Name change on IfcWall persists correctly to file."""
    writer = Tier1Writer(ifc_copy)
    changes = writer.set_attribute([WALL1_GUID], "Name", "Renamed-Wall")
    writer.save()

    model = ifcopenshell.open(str(ifc_copy))
    element = model.by_guid(WALL1_GUID)

    assert element.Name == "Renamed-Wall"
    assert len(changes) == 1
    assert changes[0].new_value == "Renamed-Wall"
    assert changes[0].old_value == "TestWall-001"


# ── file integrity ────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_saved_file_is_valid_ifc_reopenable(ifc_copy: Path) -> None:
    """After any write, the saved file can be re-opened as a valid IFC model."""
    writer = Tier1Writer(ifc_copy)
    writer.set_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI240")
    writer.save()

    # Must not raise
    model = ifcopenshell.open(str(ifc_copy))
    assert model is not None
    assert len(model.by_type("IfcWall")) == 2
