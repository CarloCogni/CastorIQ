# writeback/tests/test_benchmark_verify.py
"""Tests for the benchmark fidelity verifiers — real IfcOpenShell, no LLM.

These use the real writers to produce a real change, then check that the
verifier agrees. Mocking here would defeat the purpose: the whole reason
fidelity is scored separately is to catch writes that *report* success without
changing the file, and only a real read-back can see that.

Each test also asserts the negative case, because a verifier that returns True
unconditionally would pass every positive test while catching nothing.
"""

import shutil
from pathlib import Path

import ifcopenshell
import pytest

from ifc_processor.services.ifc_writer import Tier1Writer
from ifc_processor.services.journal import (
    AppliedJournal,
    AppliedMutation,
    Mutation,
    MutationJournal,
    MutationOp,
    new_journal_id,
    new_mutation_id,
)
from ifc_processor.services.tier3_writer import Tier3Writer
from writeback.services.benchmark.verify import _values_match, verify_journal

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = Path(__file__).resolve().parents[2] / "ifc_processor/tests/fixtures/simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "verify.ifc"
    shutil.copy2(FIXTURE_PATH, destination)
    return destination


def _mutation(op: MutationOp, **kwargs) -> Mutation:
    base = {"id": new_mutation_id(), "op": op, "global_id": WALL1_GUID}
    base.update(kwargs)
    return Mutation(**base)


def _applied(*pairs) -> AppliedJournal:
    """Build an AppliedJournal from (mutation, result) pairs."""
    mutations = tuple(m for m, _ in pairs)
    journal = MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id="file-1",
        source_tier=1,
        base_fingerprint="",
        captured_at="2026-08-05T00:00:00+00:00",
        mutations=mutations,
    )
    return AppliedJournal(
        journal=journal,
        applied=tuple(
            AppliedMutation(mutation=m, actual_old_value=m.old_value, stale=False, result=r or {})
            for m, r in pairs
        ),
    )


def _verify_one(ifc_copy: Path, mutation: Mutation, result: dict | None = None):
    return verify_journal(_applied((mutation, result)), str(ifc_copy))[0]


# ── Properties ────────────────────────────────────────────────────


@pytest.mark.slow
class TestPropertyVerification:
    def test_passes_when_the_property_really_changed(self, ifc_copy: Path):
        writer = Tier1Writer(ifc_copy)
        writer.set_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI120")
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.SET_PROPERTY,
                pset="Pset_WallCommon",
                prop="FireRating",
                new_value="EI120",
            ),
        )
        assert check.passed, check.detail

    def test_fails_when_the_value_is_stale(self, ifc_copy: Path):
        """The silent-no-op case — the whole reason fidelity is scored.

        Nothing is written; the fixture still holds EI60, so a journal claiming
        EI120 must be reported as not landed.
        """
        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.SET_PROPERTY,
                pset="Pset_WallCommon",
                prop="FireRating",
                new_value="EI120",
            ),
        )
        assert not check.passed
        assert "EI60" in check.detail

    def test_fails_when_the_pset_is_absent_entirely(self, ifc_copy: Path):
        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.SET_PROPERTY,
                pset="Pset_Maintenance",
                prop="Inspector",
                new_value="TBD",
            ),
        )
        assert not check.passed
        assert "absent" in check.detail

    def test_remove_property_passes_once_gone(self, ifc_copy: Path):
        writer = Tier1Writer(ifc_copy)
        writer.remove_property([WALL1_GUID], "Pset_WallCommon", "FireRating")
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(MutationOp.REMOVE_PROPERTY, pset="Pset_WallCommon", prop="FireRating"),
        )
        assert check.passed, check.detail

    def test_missing_entity_fails_rather_than_raising(self, ifc_copy: Path):
        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.SET_PROPERTY,
                global_id="0NoSuchGuid0000000000",
                pset="Pset_WallCommon",
                prop="FireRating",
                new_value="EI120",
            ),
        )
        assert not check.passed
        assert "not found" in check.detail


# ── Attributes ────────────────────────────────────────────────────


@pytest.mark.slow
class TestAttributeVerification:
    def test_passes_after_a_rename(self, ifc_copy: Path):
        writer = Tier1Writer(ifc_copy)
        writer.set_attribute([WALL1_GUID], "Name", "Renamed Wall")
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(MutationOp.SET_ATTRIBUTE, attribute="Name", new_value="Renamed Wall"),
        )
        assert check.passed, check.detail

    def test_fails_when_the_name_is_unchanged(self, ifc_copy: Path):
        check = _verify_one(
            ifc_copy,
            _mutation(MutationOp.SET_ATTRIBUTE, attribute="Name", new_value="Renamed Wall"),
        )
        assert not check.passed


# ── Entity lifecycle ──────────────────────────────────────────────


@pytest.mark.slow
class TestLifecycleVerification:
    def test_create_uses_the_execution_time_global_id(self, ifc_copy: Path):
        """A CREATE's journal id is blank; the minted one arrives in `result`."""
        writer = Tier3Writer(ifc_copy)
        change = writer.create_entity("IfcZone", "Fire Zone A")
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.CREATE_ENTITY,
                global_id="",
                entity_name="Fire Zone A",
                ifc_type="IfcZone",
            ),
            {"global_id": change.global_id},
        )
        assert check.passed, check.detail

    def test_create_fails_when_the_entity_is_absent(self, ifc_copy: Path):
        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.CREATE_ENTITY,
                global_id="",
                entity_name="Fire Zone A",
                ifc_type="IfcZone",
            ),
            {"global_id": "0NeverMinted00000000"},
        )
        assert not check.passed

    def test_non_rooted_create_is_verified_by_name(self, ifc_copy: Path):
        """IfcMaterial has no GlobalId, so by_guid can never confirm it."""
        writer = Tier3Writer(ifc_copy)
        writer.create_entity("IfcMaterial", "Concrete C30/37")
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.CREATE_ENTITY,
                global_id="",
                entity_name="Concrete C30/37",
                ifc_type="IfcMaterial",
            ),
        )
        assert check.passed, check.detail

    def test_delete_passes_once_the_entity_is_gone(self, ifc_copy: Path):
        writer = Tier3Writer(ifc_copy)
        writer.delete_entity(WALL1_GUID)
        writer.save()

        check = _verify_one(ifc_copy, _mutation(MutationOp.DELETE_ENTITY))
        assert check.passed, check.detail

    def test_delete_fails_while_the_entity_survives(self, ifc_copy: Path):
        check = _verify_one(ifc_copy, _mutation(MutationOp.DELETE_ENTITY))
        assert not check.passed
        assert "still resolves" in check.detail


@pytest.mark.slow
class TestContainerVerification:
    def test_passes_after_a_real_move(self, ifc_copy: Path):
        writer = Tier3Writer(ifc_copy)
        building = writer.model.by_type("IfcBuilding")[0]
        writer.assign_container(WALL1_GUID, building.GlobalId)
        writer.save()

        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.ASSIGN_RELATIONSHIP,
                params={"destination_global_id": building.GlobalId},
            ),
        )
        assert check.passed, check.detail

    def test_fails_when_still_in_the_old_container(self, ifc_copy: Path):
        model = ifcopenshell.open(str(ifc_copy))
        building = model.by_type("IfcBuilding")[0]

        check = _verify_one(
            ifc_copy,
            _mutation(
                MutationOp.ASSIGN_RELATIONSHIP,
                params={"destination_global_id": building.GlobalId},
            ),
        )
        assert not check.passed


# ── Advisory and robustness ───────────────────────────────────────


@pytest.mark.slow
class TestAdvisoryAndRobustness:
    def test_run_code_is_advisory_not_scored(self, ifc_copy: Path):
        check = _verify_one(ifc_copy, _mutation(MutationOp.RUN_CODE, global_id=""))
        assert check.advisory
        assert check.passed

    def test_unreadable_file_fails_every_check_without_raising(self, tmp_path: Path):
        broken = tmp_path / "broken.ifc"
        broken.write_text("this is not an IFC file", encoding="utf-8")

        checks = verify_journal(
            _applied((_mutation(MutationOp.SET_PROPERTY, pset="P", prop="X"), None)), str(broken)
        )
        assert len(checks) == 1
        assert not checks[0].passed


class TestValueMatching:
    """IFC round-trips values through text; comparing repr would lie."""

    @pytest.mark.parametrize(
        "expected,actual",
        [
            ("EI120", "EI120"),
            ("ei120", "EI120"),
            (0.18, 0.18),
            ("0.18", 0.18),
            (0.18, 0.180000001),
            (True, True),
            ("true", True),
            (False, ".F."),
            (None, None),
        ],
    )
    def test_equivalent_values_match(self, expected, actual):
        assert _values_match(expected, actual)

    @pytest.mark.parametrize(
        "expected,actual",
        [
            ("EI120", "EI60"),
            (0.18, 0.24),
            (True, False),
            ("true", False),
            (None, "EI120"),
            ("EI120", None),
        ],
    )
    def test_different_values_do_not_match(self, expected, actual):
        assert not _values_match(expected, actual)
