# ifc_processor/tests/test_journal_executor.py
"""Integration tests for JournalExecutor against the simple_wall fixture.

Verifies the corruption-prevention contract: the original file is only
ever touched by one atomic replace after every mutation succeeded, and
any failure leaves it byte-identical.

Run: cd src && uv run pytest ifc_processor/tests/test_journal_executor.py -v
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util
import pytest

from ifc_processor.services import journal_executor
from ifc_processor.services.ifc_writer import IFCWriteError
from ifc_processor.services.journal import (
    Mutation,
    MutationJournal,
    MutationOp,
    compute_fingerprint,
    new_journal_id,
    new_mutation_id,
)
from ifc_processor.services.journal_executor import (
    JournalExecutionError,
    JournalExecutor,
    JournalStaleError,
)

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
WALL2_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "test.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return dest


def _journal(
    ifc_path: Path, *mutations: Mutation, fingerprint: str | None = None
) -> MutationJournal:
    return MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id="file-1",
        source_tier=1,
        base_fingerprint=compute_fingerprint(ifc_path) if fingerprint is None else fingerprint,
        captured_at="2026-08-03T12:00:00+00:00",
        mutations=tuple(mutations),
    )


def _set_property(guid: str, prop: str, old, new) -> Mutation:
    return Mutation(
        id=new_mutation_id(),
        op=MutationOp.SET_PROPERTY,
        global_id=guid,
        entity_name="Wall",
        ifc_type="IfcWall",
        pset="Pset_WallCommon",
        prop=prop,
        old_value=old,
        new_value=new,
    )


@pytest.mark.slow
def test_apply_persists_and_reports_old_values(ifc_copy: Path) -> None:
    journal = _journal(
        ifc_copy,
        _set_property(WALL1_GUID, "FireRating", "EI60", "EI120"),
        _set_property(WALL2_GUID, "FireRating", "EI60", "EI120"),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)

    assert len(applied.applied) == 2
    assert applied.stale_count == 0
    assert all(a.actual_old_value == "EI60" for a in applied.applied)

    model = ifcopenshell.open(str(ifc_copy))
    for guid in (WALL1_GUID, WALL2_GUID):
        psets = element_util.get_psets(model.by_guid(guid))
        assert psets["Pset_WallCommon"]["FireRating"] == "EI120"


@pytest.mark.slow
def test_apply_failure_leaves_original_byte_identical(ifc_copy: Path) -> None:
    """Second mutation targets a missing GUID → whole apply fails, file untouched."""
    before = ifc_copy.read_bytes()
    journal = _journal(
        ifc_copy,
        _set_property(WALL1_GUID, "FireRating", "EI60", "EI120"),
        _set_property("0MissingGuid0000000000", "FireRating", "EI60", "EI120"),
    )

    with pytest.raises(Exception):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before
    # No temp files left behind either.
    assert list(ifc_copy.parent.glob(".*journal-*")) == []


@pytest.mark.slow
def test_apply_aborts_on_stale_fingerprint(ifc_copy: Path) -> None:
    journal = _journal(
        ifc_copy,
        _set_property(WALL1_GUID, "FireRating", "EI60", "EI120"),
        fingerprint="0" * 64,
    )
    before = ifc_copy.read_bytes()

    with pytest.raises(JournalStaleError, match="re-propose"):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before


@pytest.mark.slow
def test_apply_warn_policy_proceeds_on_stale_fingerprint(ifc_copy: Path) -> None:
    journal = _journal(
        ifc_copy,
        _set_property(WALL1_GUID, "FireRating", "EI60", "EI120"),
        fingerprint="0" * 64,
    )
    applied = JournalExecutor(ifc_copy).apply(journal, stale_policy="warn")
    assert len(applied.applied) == 1


@pytest.mark.slow
def test_preview_never_modifies_the_file(ifc_copy: Path) -> None:
    before = ifc_copy.read_bytes()
    journal = _journal(ifc_copy, _set_property(WALL1_GUID, "FireRating", "EI60", "EI120"))

    applied = JournalExecutor(ifc_copy).preview(journal)

    assert len(applied.applied) == 1
    assert applied.applied[0].actual_old_value == "EI60"
    assert ifc_copy.read_bytes() == before


@pytest.mark.slow
def test_stale_flag_set_when_db_snapshot_drifted(ifc_copy: Path) -> None:
    """Journal claims old FireRating was EI90; the file says EI60 → stale flag."""
    journal = _journal(ifc_copy, _set_property(WALL1_GUID, "FireRating", "EI90", "EI120"))
    applied = JournalExecutor(ifc_copy).apply(journal)
    assert applied.stale_count == 1
    assert applied.applied[0].stale is True


@pytest.mark.slow
def test_unregistered_op_fails_cleanly(ifc_copy: Path, monkeypatch) -> None:
    # Every MutationOp is handled today (RUN_CODE is dispatched separately),
    # so this drops a handler to exercise the guard rather than relying on a
    # genuinely unregistered op that a later phase would type and remove.
    handlers = dict(journal_executor._HANDLERS)
    handlers.pop(MutationOp.SET_PROPERTY)
    monkeypatch.setattr(journal_executor, "_HANDLERS", handlers)

    before = ifc_copy.read_bytes()
    mutation = Mutation(
        id=new_mutation_id(),
        op=MutationOp.SET_PROPERTY,
        global_id=WALL1_GUID,
        pset="Pset_WallCommon",
        prop="IsExternal",
        new_value=True,
    )
    journal = _journal(ifc_copy, mutation)

    with pytest.raises(JournalExecutionError, match="No handler"):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before


def test_missing_file_raises() -> None:
    with pytest.raises(JournalExecutionError, match="not found"):
        JournalExecutor("Z:/does/not/exist.ifc")


# ── Tier 2 handlers ───────────────────────────────────────────────


def _t2(op: MutationOp, guid: str, **kw) -> Mutation:
    base = dict(
        id=new_mutation_id(),
        op=op,
        global_id=guid,
        entity_name="Wall",
        ifc_type="IfcWall",
    )
    base.update(kw)
    return Mutation(**base)


@pytest.mark.slow
def test_add_pset_handler_matches_writer_shape(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.ADD_PSET,
            WALL1_GUID,
            pset="Pset_Maintenance",
            prop="Inspector",
            new_value="TBD",
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    assert (change.pset, change.property, change.old_value, change.new_value) == (
        "Pset_Maintenance",
        "Inspector",
        "(none)",
        "TBD",
    )
    model = ifcopenshell.open(str(ifc_copy))
    assert (
        element_util.get_psets(model.by_guid(WALL1_GUID))["Pset_Maintenance"]["Inspector"] == "TBD"
    )


@pytest.mark.slow
def test_remove_pset_handler_groups_per_pset(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    # Two REMOVE_PSET mutations for the same (entity, pset): the executor must
    # call remove_pset ONCE (a second call would raise "pset not found"), then
    # hand each mutation its own change row.
    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.REMOVE_PSET,
            WALL1_GUID,
            pset="Pset_WallCommon",
            prop="FireRating",
            old_value="EI60",
        ),
        _t2(
            MutationOp.REMOVE_PSET,
            WALL1_GUID,
            pset="Pset_WallCommon",
            prop="IsExternal",
            old_value="True",
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    changes = applied_to_entity_changes(applied)

    assert len(changes) == 2
    assert all(c.new_value == "(removed)" for c in changes)
    assert {c.property for c in changes} == {"FireRating", "IsExternal"}
    model = ifcopenshell.open(str(ifc_copy))
    assert "Pset_WallCommon" not in element_util.get_psets(model.by_guid(WALL1_GUID))


@pytest.mark.slow
def test_set_material_handler_sentinel_shape(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.SET_MATERIAL,
            WALL1_GUID,
            pset="(material)",
            prop="Material",
            new_value="Concrete",
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    assert (change.pset, change.property, change.new_value) == (
        "(material)",
        "Material",
        "Concrete",
    )
    assert applied.stale_count == 0  # old snapshot None → never stale


@pytest.mark.slow
def test_set_classification_handler_sentinel_shape(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.SET_CLASSIFICATION,
            WALL1_GUID,
            pset="(classification)",
            prop="Uniclass",
            new_value="Ss_20_10",
            params={"name": "Walls"},
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    assert (change.pset, change.property, change.new_value) == (
        "(classification)",
        "Uniclass",
        "Ss_20_10",
    )


@pytest.mark.slow
def test_t2_failure_leaves_original_byte_identical(ifc_copy: Path) -> None:
    before = ifc_copy.read_bytes()
    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.SET_MATERIAL,
            WALL1_GUID,
            pset="(material)",
            prop="Material",
            new_value="Concrete",
        ),
        # REMOVE_PSET of a pset the entity lacks → writer raises → whole apply fails.
        _t2(MutationOp.REMOVE_PSET, WALL2_GUID, pset="Pset_DoesNotExist", prop="X", old_value="1"),
    )
    with pytest.raises(Exception):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before
    assert list(ifc_copy.parent.glob(".*journal-*")) == []


# ── Tier 3 lifecycle handlers ─────────────────────────────────────


@pytest.mark.slow
def test_create_entity_round_trips_the_minted_global_id(ifc_copy: Path) -> None:
    """THE round-trip assertion: the GlobalId IfcOpenShell mints at execution
    must reach the EntityChange, or the created entity never enters the DB
    index — the exact bug this phase exists to fix."""
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.CREATE_ENTITY,
            "",  # no GlobalId exists yet
            entity_name="Fire Zone A",
            ifc_type="IfcZone",
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    model = ifcopenshell.open(str(ifc_copy))
    created = model.by_type("IfcZone")[-1]

    assert change.global_id == created.GlobalId
    assert len(change.global_id) == 22
    assert change.pset == "(entity)"
    assert change.property == "CREATE"


@pytest.mark.slow
def test_create_space_under_storey_via_journal(ifc_copy: Path) -> None:
    model = ifcopenshell.open(str(ifc_copy))
    storey_guid = model.by_type("IfcBuildingStorey")[0].GlobalId

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.CREATE_ENTITY,
            "",
            entity_name="Server Room",
            ifc_type="IfcSpace",
            params={"parent_global_id": storey_guid, "parent_relation": "aggregate"},
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)

    reopened = ifcopenshell.open(str(ifc_copy))
    space = reopened.by_guid(applied.applied[0].result["global_id"])
    storey = reopened.by_guid(storey_guid)
    assert space in [o for rel in storey.IsDecomposedBy for o in rel.RelatedObjects]


@pytest.mark.slow
def test_delete_entity_via_journal(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(MutationOp.DELETE_ENTITY, WALL1_GUID, entity_name="TestWall-001", ifc_type="IfcWall"),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    assert change.property == "DELETE"
    assert change.new_value == "(deleted)"
    model = ifcopenshell.open(str(ifc_copy))
    with pytest.raises(RuntimeError):
        model.by_guid(WALL1_GUID)


@pytest.mark.slow
def test_create_failure_leaves_original_byte_identical(ifc_copy: Path) -> None:
    before = ifc_copy.read_bytes()
    journal = _journal(
        ifc_copy,
        _t2(MutationOp.CREATE_ENTITY, "", entity_name="Zone OK", ifc_type="IfcZone"),
        # Physical class → writer refuses → whole apply fails.
        _t2(MutationOp.CREATE_ENTITY, "", entity_name="Wall", ifc_type="IfcWall"),
    )

    with pytest.raises(Exception):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before
    assert list(ifc_copy.parent.glob(".*journal-*")) == []


@pytest.mark.slow
def test_mixed_code_and_typed_journal_is_rejected(ifc_copy: Path) -> None:
    """Two writers on one file (in-memory writer + sandbox subprocess) would
    silently clobber each other — reject before anything runs."""
    before = ifc_copy.read_bytes()
    journal = _journal(
        ifc_copy,
        _t2(MutationOp.CREATE_ENTITY, "", entity_name="Zone", ifc_type="IfcZone"),
        _t2(MutationOp.RUN_CODE, "", params={"code": "def modify_ifc(model): pass"}),
    )

    with pytest.raises(JournalExecutionError, match="exactly one mutation"):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before


@pytest.mark.slow
def test_run_code_journal_applies_and_reports_changes(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    code = """
def modify_ifc(model):
    wall = model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")
    wall.Name = "Renamed By Code"
    return {"summary": "renamed", "changes": [{
        "global_id": "2O2Fr$t4X7Zf8NOew3FLOH", "entity_name": "Renamed By Code",
        "ifc_type": "IfcWall", "description": "renamed",
        "old_value": "TestWall-001", "new_value": "Renamed By Code"}]}
"""
    journal = _journal(ifc_copy, _t2(MutationOp.RUN_CODE, "", params={"code": code}))
    applied = JournalExecutor(ifc_copy).apply(journal)
    change = applied_to_entity_changes(applied)[0]

    assert change.pset == "(code)"
    assert change.new_value == "Renamed By Code"
    model = ifcopenshell.open(str(ifc_copy))
    assert model.by_guid(WALL1_GUID).Name == "Renamed By Code"


@pytest.mark.slow
def test_run_code_without_code_is_rejected(ifc_copy: Path) -> None:
    journal = _journal(ifc_copy, _t2(MutationOp.RUN_CODE, "", params={}))
    with pytest.raises(JournalExecutionError, match="carries no code"):
        JournalExecutor(ifc_copy).apply(journal)


# ── ASSIGN_RELATIONSHIP ───────────────────────────────────────────

BUILDING_GUID = "3eglpeTM9E8O1D6DaKKJfb"


@pytest.mark.slow
def test_assign_relationship_via_journal(ifc_copy: Path) -> None:
    from ifc_processor.services.journal import applied_to_entity_changes

    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.ASSIGN_RELATIONSHIP,
            WALL1_GUID,
            pset="(entity)",
            prop="CONTAINER",
            old_value="Level 0",
            new_value="TestBuilding",
            params={"destination_global_id": BUILDING_GUID, "relation": "container"},
        ),
    )
    applied = JournalExecutor(ifc_copy).apply(journal)

    reopened = ifcopenshell.open(str(ifc_copy))
    container = element_util.get_container(reopened.by_guid(WALL1_GUID))
    assert container.GlobalId == BUILDING_GUID

    assert applied.applied[0].result["moved"] is True
    assert applied.applied[0].result["destination_global_id"] == BUILDING_GUID

    change = applied_to_entity_changes(applied)[0]
    assert change.global_id == WALL1_GUID
    assert change.pset == "(entity)"
    assert change.property == "CONTAINER"
    assert change.old_value == "Level 0"


@pytest.mark.slow
def test_assign_relationship_failure_leaves_the_file_untouched(ifc_copy: Path) -> None:
    before = ifc_copy.read_bytes()
    journal = _journal(
        ifc_copy,
        _t2(
            MutationOp.ASSIGN_RELATIONSHIP,
            WALL1_GUID,
            params={"destination_global_id": "NOT_A_REAL_GUID_0000"},
        ),
    )
    # The writer's IFCWriteError propagates as-is, like every other handler.
    with pytest.raises(IFCWriteError, match="Entity not found"):
        JournalExecutor(ifc_copy).apply(journal)

    assert ifc_copy.read_bytes() == before
