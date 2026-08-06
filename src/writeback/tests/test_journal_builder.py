# writeback/tests/test_journal_builder.py
"""Unit tests for JournalBuilder.build_t1 — DB-snapshot capture, cap, errors.

Uses unsaved IFCEntity instances and a stub ifc_file, so no database is
required: the builder only reads attributes and hashes the file on disk.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ifc_processor.models import IFCEntity
from ifc_processor.services.journal import MutationOp
from writeback.services.journal_builder import JournalBuilder, JournalBuildError

FIXTURE_PATH = (
    Path(__file__).parents[2] / "ifc_processor" / "tests" / "fixtures" / "simple_wall.ifc"
)

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
WALL2_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"


@pytest.fixture
def ifc_file_stub():
    return SimpleNamespace(id="file-1", file=SimpleNamespace(path=str(FIXTURE_PATH)))


@pytest.fixture
def walls() -> list[IFCEntity]:
    return [
        IFCEntity(
            global_id=WALL1_GUID,
            ifc_type="IfcWall",
            name="Wall-01",
            properties={"Pset_WallCommon.FireRating": "EI60"},
        ),
        IFCEntity(
            global_id=WALL2_GUID,
            ifc_type="IfcWall",
            name="Wall-02",
            properties={},
        ),
    ]


def _set_property_intent(**overrides) -> dict:
    intent = {
        "tier": 1,
        "operation": "SET_PROPERTY",
        "pset": "Pset_WallCommon",
        "property": "FireRating",
        "new_value": "EI120",
    }
    intent.update(overrides)
    return intent


def test_build_t1_expands_one_mutation_per_entity(ifc_file_stub, walls):
    journal = JournalBuilder(project=None).build_t1(_set_property_intent(), walls, ifc_file_stub)

    assert journal.source_tier == 1
    assert journal.ifc_file_id == "file-1"
    assert len(journal.mutations) == 2
    assert journal.base_fingerprint  # pinned from the file on disk
    assert journal.affected_global_ids == {WALL1_GUID, WALL2_GUID}

    first, second = journal.mutations
    assert first.op == MutationOp.SET_PROPERTY
    assert first.old_value == "EI60"  # snapshot from entity.properties
    assert second.old_value is None  # property not set on Wall-02
    assert first.new_value == "EI120"
    assert first.value_type  # FireRating is in the standard registry


def test_build_t1_attribute_snapshot_from_columns(ifc_file_stub, walls):
    intent = {
        "tier": 1,
        "operation": "SET_ATTRIBUTE",
        "attribute": "Name",
        "new_value": "W-Renamed",
    }
    journal = JournalBuilder(project=None).build_t1(intent, walls[:1], ifc_file_stub)

    mutation = journal.mutations[0]
    assert mutation.op == MutationOp.SET_ATTRIBUTE
    assert mutation.attribute == "Name"
    assert mutation.old_value == "Wall-01"


def test_build_t1_remove_property_clears_new_value(ifc_file_stub, walls):
    intent = _set_property_intent(operation="REMOVE_PROPERTY", new_value="ignored")
    journal = JournalBuilder(project=None).build_t1(intent, walls[:1], ifc_file_stub)
    assert journal.mutations[0].new_value is None


def test_build_t1_rejects_empty_entities(ifc_file_stub):
    with pytest.raises(JournalBuildError, match="no target entities"):
        JournalBuilder(project=None).build_t1(_set_property_intent(), [], ifc_file_stub)


def test_build_t1_rejects_unknown_operation(ifc_file_stub, walls):
    with pytest.raises(JournalBuildError, match="Unknown Tier 1 operation"):
        JournalBuilder(project=None).build_t1(
            _set_property_intent(operation="EXPLODE"), walls, ifc_file_stub
        )


def test_build_t1_rejects_non_t1_operation(ifc_file_stub, walls):
    with pytest.raises(JournalBuildError, match="not a Tier 1 operation"):
        JournalBuilder(project=None).build_t1(
            _set_property_intent(operation="SET_MATERIAL"), walls, ifc_file_stub
        )


def test_build_t1_enforces_mutation_cap(ifc_file_stub, walls, settings):
    settings.WRITEBACK_JOURNAL_MAX_MUTATIONS = 1
    with pytest.raises(JournalBuildError, match="narrow the target filter"):
        JournalBuilder(project=None).build_t1(_set_property_intent(), walls, ifc_file_stub)


def test_build_t1_round_trips_through_json(ifc_file_stub, walls):
    from ifc_processor.services.journal import MutationJournal

    journal = JournalBuilder(project=None).build_t1(_set_property_intent(), walls, ifc_file_stub)
    assert MutationJournal.from_json_dict(journal.to_json_dict()) == journal


# ── build_t2 ──────────────────────────────────────────────────────


def _plan(*steps: dict) -> dict:
    return {
        "tier": 2,
        "plan": [{"step": i + 1, "filter": {}, **s} for i, s in enumerate(steps)],
        "confidence": 80,
        "explanation": "",
    }


def _validation(*entity_lists: list) -> SimpleNamespace:
    """Stub PlanValidation: only `.steps[i].entities` is read by build_t2."""
    return SimpleNamespace(steps=[SimpleNamespace(entities=e) for e in entity_lists])


def _build_t2(plan, validation, ifc_file_stub):
    return JournalBuilder(project=None).build_t2(plan, validation, ifc_file_stub)


def test_build_t2_add_pset_fans_per_new_property_and_skips_present(ifc_file_stub, walls):
    # Wall-01 already has Pset_WallCommon.FireRating; the ADD_PSET below adds two
    # fresh props to both walls but must skip the already-present FireRating.
    plan = _plan(
        {
            "operation": "ADD_PSET",
            "params": {
                "pset_name": "Pset_WallCommon",
                "properties": {"FireRating": "EI120", "Combustible": False},
            },
        }
    )
    journal = _build_t2(plan, _validation(walls), ifc_file_stub)

    assert journal.source_tier == 2
    # Wall-01: FireRating skipped (present) → 1 (Combustible). Wall-02: 2.
    ops = [(m.global_id, m.prop) for m in journal.mutations]
    assert ops == [
        (WALL1_GUID, "Combustible"),
        (WALL2_GUID, "FireRating"),
        (WALL2_GUID, "Combustible"),
    ]
    assert all(
        m.op == MutationOp.ADD_PSET and m.pset == "Pset_WallCommon" for m in journal.mutations
    )
    assert all(m.old_value is None for m in journal.mutations)


def test_build_t2_remove_pset_one_mutation_per_db_property(ifc_file_stub, walls):
    walls[0].properties = {
        "Pset_Old.A": "1",
        "Pset_Old.B": "2",
        "Pset_WallCommon.FireRating": "EI60",
    }
    plan = _plan({"operation": "REMOVE_PSET", "params": {"pset_name": "Pset_Old"}})
    journal = _build_t2(plan, _validation([walls[0]]), ifc_file_stub)

    assert {(m.prop, m.old_value) for m in journal.mutations} == {("A", "1"), ("B", "2")}
    assert all(m.op == MutationOp.REMOVE_PSET and m.pset == "Pset_Old" for m in journal.mutations)
    assert all(m.new_value is None for m in journal.mutations)


def test_build_t2_set_material_per_entity_with_sentinel(ifc_file_stub, walls):
    plan = _plan({"operation": "SET_MATERIAL", "params": {"material_name": "Concrete"}})
    journal = _build_t2(plan, _validation(walls), ifc_file_stub)

    assert len(journal.mutations) == 2
    m = journal.mutations[0]
    assert m.op == MutationOp.SET_MATERIAL
    assert m.pset == "(material)" and m.prop == "Material"
    assert m.old_value is None and m.new_value == "Concrete"


def test_build_t2_set_classification_carries_system_and_name(ifc_file_stub, walls):
    plan = _plan(
        {
            "operation": "SET_CLASSIFICATION",
            "params": {"system_name": "Uniclass", "reference": "Ss_20_10", "name": "Walls"},
        }
    )
    journal = _build_t2(plan, _validation(walls), ifc_file_stub)

    m = journal.mutations[0]
    assert m.op == MutationOp.SET_CLASSIFICATION
    assert m.pset == "(classification)" and m.prop == "Uniclass"
    assert m.new_value == "Ss_20_10" and m.params == {"name": "Walls"}


def test_build_t2_multi_step_combines(ifc_file_stub, walls):
    plan = _plan(
        {"operation": "SET_MATERIAL", "params": {"material_name": "Concrete"}},
        {"operation": "ADD_PSET", "params": {"pset_name": "Pset_New", "properties": {"X": "1"}}},
    )
    journal = _build_t2(plan, _validation(walls, walls), ifc_file_stub)
    # 2 material + 2 add-pset = 4
    assert len(journal.mutations) == 4
    assert journal.mutations[0].op == MutationOp.SET_MATERIAL
    assert journal.mutations[-1].op == MutationOp.ADD_PSET


def test_build_t2_copy_properties_raises(ifc_file_stub, walls):
    plan = _plan({"operation": "COPY_PROPERTIES", "params": {"source_name": "X", "pset_name": "P"}})
    with pytest.raises(JournalBuildError, match="Unsupported Tier 2 operation"):
        _build_t2(plan, _validation(walls), ifc_file_stub)


def test_build_t2_empty_plan_raises(ifc_file_stub):
    with pytest.raises(JournalBuildError, match="empty plan"):
        _build_t2(_plan(), _validation(), ifc_file_stub)


def test_build_t2_all_present_raises_no_changes(ifc_file_stub, walls):
    walls[0].properties = {"Pset_WallCommon.FireRating": "EI60"}
    plan = _plan(
        {
            "operation": "ADD_PSET",
            "params": {"pset_name": "Pset_WallCommon", "properties": {"FireRating": "EI60"}},
        }
    )
    with pytest.raises(JournalBuildError, match="no applicable changes"):
        _build_t2(plan, _validation([walls[0]]), ifc_file_stub)


def test_build_t2_enforces_total_cap(ifc_file_stub, walls, settings):
    settings.WRITEBACK_JOURNAL_MAX_MUTATIONS = 1
    plan = _plan({"operation": "SET_MATERIAL", "params": {"material_name": "Concrete"}})
    with pytest.raises(JournalBuildError, match="narrow the target filter"):
        _build_t2(plan, _validation(walls), ifc_file_stub)


def test_build_t2_round_trips_through_json(ifc_file_stub, walls):
    from ifc_processor.services.journal import MutationJournal

    plan = _plan(
        {"operation": "SET_CLASSIFICATION", "params": {"system_name": "Uni", "reference": "R"}}
    )
    journal = _build_t2(plan, _validation(walls), ifc_file_stub)
    assert MutationJournal.from_json_dict(journal.to_json_dict()) == journal


# ── build_t3 (typed Tier 3 ops) ───────────────────────────────────
# These need the DB: grounding re-checks every referenced GlobalId against
# IFCEntity rows for the file, independent of what the planner claimed.


@pytest.mark.django_db
class TestBuildT3:
    def _builder(self, project):
        return JournalBuilder(project=project)

    def test_create_entity_mutation_shape(self, project, ifc_file, wall_entities):
        ops = [
            {
                "op": "CREATE_ENTITY",
                "ifc_class": "IfcZone",
                "name": "Fire Zone A",
                "long_name": "Fire compartment A",
                "member_global_ids": [wall_entities[0].global_id],
            }
        ]
        journal = self._builder(project).build_t3(ops, ifc_file)

        assert journal.source_tier == 3
        assert len(journal.mutations) == 1
        m = journal.mutations[0]
        assert m.op == MutationOp.CREATE_ENTITY
        assert m.global_id == ""  # minted at execution, not now
        assert m.entity_name == "Fire Zone A"
        assert m.ifc_type == "IfcZone"
        assert m.params["long_name"] == "Fire compartment A"
        assert m.params["member_global_ids"] == [wall_entities[0].global_id]
        # A CREATE touches no existing entity.
        assert journal.affected_global_ids == frozenset()

    def test_delete_entity_snapshots_identity_from_db(self, project, ifc_file, wall_entities):
        target = wall_entities[0]
        ops = [{"op": "DELETE_ENTITY", "global_id": target.global_id}]

        journal = self._builder(project).build_t3(ops, ifc_file)

        m = journal.mutations[0]
        assert m.op == MutationOp.DELETE_ENTITY
        assert m.global_id == target.global_id
        assert m.entity_name == target.name
        assert m.ifc_type == target.ifc_type

    def test_delete_of_unknown_entity_is_rejected(self, project, ifc_file, wall_entities):
        """Grounding: the builder re-checks against the DB, so a hallucinated
        GlobalId cannot survive to execution."""
        ops = [{"op": "DELETE_ENTITY", "global_id": "0HallucinatedGuid0000"}]

        with pytest.raises(JournalBuildError, match="does not exist"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_create_with_unknown_parent_is_rejected(self, project, ifc_file, wall_entities):
        ops = [
            {
                "op": "CREATE_ENTITY",
                "ifc_class": "IfcSpace",
                "name": "Room",
                "parent_global_id": "0HallucinatedGuid0000",
                "parent_relation": "aggregate",
            }
        ]
        with pytest.raises(JournalBuildError, match="does not exist"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_assign_relationship_mutation_shape(self, project, ifc_file, wall_entities):
        moved, destination = wall_entities[0], wall_entities[1]
        ops = [
            {
                "op": "ASSIGN_RELATIONSHIP",
                "global_id": moved.global_id,
                "destination_global_id": destination.global_id,
                "relation": "container",
            }
        ]

        journal = self._builder(project).build_t3(ops, ifc_file)

        m = journal.mutations[0]
        assert m.op == MutationOp.ASSIGN_RELATIONSHIP
        assert m.global_id == moved.global_id
        assert m.entity_name == moved.name
        assert m.params["destination_global_id"] == destination.global_id
        assert m.params["relation"] == "container"
        assert m.new_value == destination.name

    def test_assign_relationship_requires_both_ids(self, project, ifc_file, wall_entities):
        ops = [{"op": "ASSIGN_RELATIONSHIP", "global_id": wall_entities[0].global_id}]
        with pytest.raises(JournalBuildError, match="requires both"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_assign_relationship_grounds_the_destination(self, project, ifc_file, wall_entities):
        """A hallucinated destination must die at build time, not at execution."""
        ops = [
            {
                "op": "ASSIGN_RELATIONSHIP",
                "global_id": wall_entities[0].global_id,
                "destination_global_id": "0HallucinatedGuid0000",
            }
        ]
        with pytest.raises(JournalBuildError, match="does not exist"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_unsupported_op_is_rejected(self, project, ifc_file, wall_entities):
        ops = [{"op": "TELEPORT_ENTITY", "global_id": wall_entities[0].global_id}]
        with pytest.raises(JournalBuildError, match="unsupported Tier 3 op"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_empty_ops_rejected(self, project, ifc_file):
        with pytest.raises(JournalBuildError, match="no operations"):
            self._builder(project).build_t3([], ifc_file)

    def test_cap_enforced(self, project, ifc_file, wall_entities, settings):
        settings.WRITEBACK_JOURNAL_MAX_MUTATIONS = 1
        ops = [
            {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "A"},
            {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "B"},
        ]
        with pytest.raises(JournalBuildError, match="safety cap"):
            self._builder(project).build_t3(ops, ifc_file)

    def test_round_trips_through_json(self, project, ifc_file, wall_entities):
        from ifc_processor.services.journal import MutationJournal

        ops = [{"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Z"}]
        journal = self._builder(project).build_t3(ops, ifc_file)
        assert MutationJournal.from_json_dict(journal.to_json_dict()) == journal


@pytest.mark.django_db
class TestBuildT3Code:
    def test_wraps_code_in_a_single_mutation(self, project, ifc_file):
        code = "def modify_ifc(model):\n    return {'summary': '', 'changes': []}"

        journal = JournalBuilder(project=project).build_t3_code(
            code, ifc_file, explanation="does a thing"
        )

        assert journal.source_tier == 3
        assert len(journal.mutations) == 1, "RUN_CODE must never share a journal"
        m = journal.mutations[0]
        assert m.op == MutationOp.RUN_CODE
        assert m.params["code"] == code
        assert m.params["explanation"] == "does a thing"

    def test_empty_code_rejected(self, project, ifc_file):
        with pytest.raises(JournalBuildError, match="no code"):
            JournalBuilder(project=project).build_t3_code("   ", ifc_file)
