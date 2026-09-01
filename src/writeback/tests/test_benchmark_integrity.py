# writeback/tests/test_benchmark_integrity.py
"""
Integrity scoring in the NL benchmark runner.

Exercises ``BenchmarkRunner._check_integrity`` against real IFC files with the
pipeline stubbed out: the column must pass when the file changed only where the
journal said, and fail on any bystander edit, geometry drift, or lost entity.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ifcopenshell
import pytest

from ifc_processor.services.ifc_writer import Tier1Writer
from ifc_processor.services.journal import Mutation, MutationJournal, MutationOp
from writeback.services.benchmark.runner import BenchmarkRunner, CaseResult

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
WALL2_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"
SIMPLE_WALL = Path(__file__).resolve().parents[2] / "ifc_processor/tests/fixtures/simple_wall.ifc"


@pytest.fixture
def runner(tmp_path: Path) -> BenchmarkRunner:
    """A runner whose source file is a pristine copy of the two-wall fixture."""
    source = tmp_path / "source.ifc"
    shutil.copy(SIMPLE_WALL, source)
    ifc_file = SimpleNamespace(file=SimpleNamespace(path=str(source)))
    with patch("writeback.services.benchmark.runner.ProposalPipeline"):
        return BenchmarkRunner(project=None, ifc_file=ifc_file)


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    dest = tmp_path / "scratch.ifc"
    shutil.copy(SIMPLE_WALL, dest)
    return dest


def _journal(*global_ids: str, op: MutationOp = MutationOp.SET_PROPERTY) -> MutationJournal:
    return MutationJournal(
        journal_id="jrn_test",
        ifc_file_id="f",
        source_tier=1,
        base_fingerprint="",
        captured_at="",
        mutations=tuple(
            Mutation(id=f"mut_{i}", op=op, global_id=gid, pset="Pset_WallCommon", prop="FireRating")
            for i, gid in enumerate(global_ids)
        ),
    )


def _result() -> CaseResult:
    return CaseResult(case_id="1", section_number="1", prompt="", expectation="", advisory=False)


def test_change_within_journal_passes(runner: BenchmarkRunner, scratch: Path) -> None:
    """One property on the journaled wall → integrity ok, and it counts as passed."""
    writer = Tier1Writer(scratch)
    writer.set_property([WALL1_GUID], "Pset_WallCommon", "FireRating", "EI120")
    writer.save()
    result = _result()

    runner._check_integrity(_journal(WALL1_GUID), scratch, result)

    assert result.integrity_ok is True
    assert "within journal" in result.integrity_detail


def test_bystander_property_change_fails(runner: BenchmarkRunner, scratch: Path) -> None:
    """The journal names wall 1 but wall 2 changed too → integrity failure."""
    writer = Tier1Writer(scratch)
    writer.set_property([WALL1_GUID, WALL2_GUID], "Pset_WallCommon", "FireRating", "EI120")
    writer.save()
    result = _result()

    runner._check_integrity(_journal(WALL1_GUID), scratch, result)

    assert result.integrity_ok is False
    assert WALL2_GUID in result.integrity_detail


def test_lost_entity_fails_for_property_journal(runner: BenchmarkRunner, scratch: Path) -> None:
    """A SET_PROPERTY journal must never change the entity population."""
    model = ifcopenshell.open(str(scratch))
    model.remove(model.by_guid(WALL2_GUID))
    model.write(str(scratch))
    result = _result()

    runner._check_integrity(_journal(WALL1_GUID), scratch, result)

    assert result.integrity_ok is False
    assert "population changed" in result.integrity_detail


def test_lost_entity_tolerated_for_delete_journal(runner: BenchmarkRunner, scratch: Path) -> None:
    """DELETE_ENTITY declares a population change, so one is allowed."""
    model = ifcopenshell.open(str(scratch))
    model.remove(model.by_guid(WALL2_GUID))
    model.write(str(scratch))
    result = _result()

    runner._check_integrity(_journal(WALL2_GUID, op=MutationOp.DELETE_ENTITY), scratch, result)

    assert result.integrity_ok is True


def test_integrity_failure_makes_case_fail() -> None:
    """A case that understood and wrote faithfully still fails on integrity."""
    result = _result()
    result.understood = True
    result.fidelity_ok = True
    result.integrity_ok = False

    assert result.passed is False
