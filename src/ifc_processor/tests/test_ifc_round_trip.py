# ifc_processor/tests/test_ifc_round_trip.py
"""
Round-trip integrity: write an IFC, re-read it, prove nothing else degraded.

Every test here uses real IfcOpenShell against real files — no mocks. The
question answered is not "did the requested change land" (that is
``verify_journal``'s job) but "did *anything else* change": entity population,
geometry, properties on untouched entities.

Fixtures:
    ifc_processor/tests/fixtures/simple_wall.ifc   — two walls, fast
    fixtures/benchmark/Ifc4_SampleHouse.ifc         — buildingSMART IFC4, ~2.3 MB

Run:
    cd src && uv run pytest ifc_processor/tests/test_ifc_round_trip.py -v
"""

from __future__ import annotations

import shutil
from pathlib import Path

import ifcopenshell
import pytest

from ifc_processor.services.ifc_diff import (
    IfcDiff,
    IfcSnapshot,
    PropertyChange,
    diff_files,
    diff_snapshots,
)
from ifc_processor.services.ifc_writer import Tier1Writer

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
WALL2_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"

SIMPLE_WALL = Path(__file__).parent / "fixtures" / "simple_wall.ifc"
SAMPLE_HOUSE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "Ifc4_SampleHouse.ifc"
)

ROUND_TRIP_FIXTURES = [
    pytest.param(SIMPLE_WALL, id="simple_wall"),
    pytest.param(
        SAMPLE_HOUSE,
        id="sample_house",
        marks=pytest.mark.skipif(not SAMPLE_HOUSE.exists(), reason="benchmark fixture absent"),
    ),
]


@pytest.fixture
def wall_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "walls.ifc"
    shutil.copy(SIMPLE_WALL, dest)
    return dest


# ── Pure diff logic (no files) ───────────────────────────────────────────────


def test_diff_of_identical_snapshots_is_empty() -> None:
    """Same snapshot twice → nothing to report."""
    snap = IfcSnapshot.from_file(SIMPLE_WALL)

    diff = diff_snapshots(snap, snap)

    assert diff.is_empty
    assert diff.unexpected() == []


def test_unexpected_ignores_property_changes_on_allowed_entities() -> None:
    """A property change on a journaled entity is expected; elsewhere it is not."""
    diff = IfcDiff(
        property_changes=[
            PropertyChange("A", "Pset_WallCommon", "FireRating", "EI60", "EI120"),
            PropertyChange("B", "Pset_WallCommon", "FireRating", "EI60", "EI120"),
        ]
    )

    problems = diff.unexpected(allowed={"A"})

    assert len(problems) == 1
    assert problems[0].startswith("B Pset_WallCommon.FireRating")


def test_unexpected_reports_population_change_unless_allowed() -> None:
    """Entity creation/deletion is only fine when the journal declares it."""
    diff = IfcDiff(added_global_ids=frozenset({"NEW"}), type_count_delta={"IfcWall": 1})

    assert diff.unexpected() != []
    assert diff.unexpected(allow_population_change=True) == []


def test_geometry_change_is_never_allowed() -> None:
    """Geometry is out of writeback's scope: any drift is a defect."""
    diff = IfcDiff(geometry_changed=frozenset({"A"}))

    assert diff.unexpected(allowed={"A"}, allow_population_change=True) != []


# ── Snapshot contents ────────────────────────────────────────────────────────


def test_snapshot_captures_population_geometry_and_properties() -> None:
    """The three integrity dimensions are populated for a rooted product."""
    snap = IfcSnapshot.from_file(SIMPLE_WALL)

    assert snap.type_counts["IfcWall"] == 2
    assert {WALL1_GUID, WALL2_GUID} <= snap.global_ids
    assert WALL1_GUID in snap.geometry
    assert snap.properties[WALL1_GUID]["Pset_WallCommon"]["FireRating"] == "EI60"
    assert "Name" in snap.attributes[WALL1_GUID]


# ── Round trips against real files ───────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.parametrize("fixture", ROUND_TRIP_FIXTURES)
def test_open_and_save_without_edits_is_lossless(fixture: Path, tmp_path: Path) -> None:
    """Load → write nothing → save → diff is empty (parser/serialiser baseline)."""
    dest = tmp_path / fixture.name
    model = ifcopenshell.open(str(fixture))
    model.write(str(dest))

    diff = diff_files(fixture, dest)

    assert diff.is_empty, diff.unexpected()


@pytest.mark.slow
def test_set_property_changes_exactly_one_value(wall_copy: Path) -> None:
    """A Tier 1 SET_PROPERTY touches one (entity, pset, prop) and nothing else."""
    before = IfcSnapshot.from_file(wall_copy)
    writer = Tier1Writer(wall_copy)
    writer.set_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI120")
    writer.save()

    diff = diff_snapshots(before, IfcSnapshot.from_file(wall_copy))

    assert diff.population_ok
    assert diff.geometry_ok
    assert [c.as_dict() for c in diff.property_changes] == [
        {
            "global_id": WALL1_GUID,
            "pset": "Pset_WallCommon",
            "prop": "FireRating",
            "before": "EI60",
            "after": "EI120",
        }
    ]
    assert diff.unexpected(allowed={WALL1_GUID}) == []
    assert diff.unexpected(allowed=set()) != []


@pytest.mark.slow
def test_add_and_remove_property_leave_population_and_geometry_intact(wall_copy: Path) -> None:
    """ADD then REMOVE on different walls: two property deltas, zero collateral."""
    before = IfcSnapshot.from_file(wall_copy)
    writer = Tier1Writer(wall_copy)
    writer.add_property([WALL1_GUID], "Pset_WallCommon", "AcousticRating", "R'w 52")
    writer.remove_property([WALL2_GUID], "Pset_WallCommon", "FireRating")
    writer.save()

    diff = diff_snapshots(before, IfcSnapshot.from_file(wall_copy))

    assert diff.population_ok, diff.type_count_delta
    assert diff.geometry_ok
    touched = {(c.global_id, c.prop) for c in diff.property_changes}
    assert touched == {(WALL1_GUID, "AcousticRating"), (WALL2_GUID, "FireRating")}
    assert diff.unexpected(allowed={WALL1_GUID, WALL2_GUID}) == []


@pytest.mark.slow
def test_set_attribute_is_reported_as_attribute_change(wall_copy: Path) -> None:
    """Renaming an entity is tracked separately from property-set changes."""
    before = IfcSnapshot.from_file(wall_copy)
    writer = Tier1Writer(wall_copy)
    writer.set_attribute([WALL1_GUID], "Name", "Renamed Wall")
    writer.save()

    diff = diff_snapshots(before, IfcSnapshot.from_file(wall_copy))

    assert diff.property_changes == []
    assert [(c.global_id, c.prop, c.after) for c in diff.attribute_changes] == [
        (WALL1_GUID, "Name", "Renamed Wall")
    ]


@pytest.mark.slow
@pytest.mark.skipif(not SAMPLE_HOUSE.exists(), reason="benchmark fixture absent")
def test_geometry_edit_is_detected(tmp_path: Path) -> None:
    """Moving one cartesian point of a wall's representation flips its hash."""
    dest = tmp_path / SAMPLE_HOUSE.name
    shutil.copy(SAMPLE_HOUSE, dest)
    before = IfcSnapshot.from_file(dest)
    model = ifcopenshell.open(str(dest))
    wall = next(w for w in model.by_type("IfcWall") if w.Representation)
    point = next(p for p in model.traverse(wall.Representation) if p.is_a("IfcCartesianPoint"))
    point.Coordinates = tuple(c + 0.5 for c in point.Coordinates)
    model.write(str(dest))

    diff = diff_snapshots(before, IfcSnapshot.from_file(dest))

    assert wall.GlobalId in diff.geometry_changed
    assert diff.property_changes == []


@pytest.mark.slow
def test_entity_deletion_is_detected(wall_copy: Path) -> None:
    """Removing a wall shows up as a missing GlobalId and a class-count delta."""
    before = IfcSnapshot.from_file(wall_copy)
    model = ifcopenshell.open(str(wall_copy))
    model.remove(model.by_guid(WALL2_GUID))
    model.write(str(wall_copy))

    diff = diff_snapshots(before, IfcSnapshot.from_file(wall_copy))

    assert WALL2_GUID in diff.removed_global_ids
    assert diff.type_count_delta.get("IfcWall") == -1
    assert not diff.population_ok
