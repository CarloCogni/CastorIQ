# ifc_processor/tests/test_journal.py
"""Unit tests for the mutation-journal IR: codec, ids, adapter, fingerprint."""

from pathlib import Path

import pytest

from ifc_processor.services.journal import (
    JOURNAL_SCHEMA_VERSION,
    AppliedJournal,
    AppliedMutation,
    JournalDecodeError,
    Mutation,
    MutationJournal,
    MutationOp,
    applied_to_entity_changes,
    compute_fingerprint,
    new_journal_id,
    new_mutation_id,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


def _make_mutation(**overrides) -> Mutation:
    defaults = dict(
        id=new_mutation_id(),
        op=MutationOp.SET_PROPERTY,
        global_id="2O2Fr$t4X7Zf8NOew3FLOH",
        entity_name="Wall-01",
        ifc_type="IfcWall",
        pset="Pset_WallCommon",
        prop="FireRating",
        old_value="EI60",
        new_value="EI120",
        value_type="string",
    )
    defaults.update(overrides)
    return Mutation(**defaults)


def _make_journal(mutations: tuple[Mutation, ...]) -> MutationJournal:
    return MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id="file-1",
        source_tier=1,
        base_fingerprint="ab" * 32,
        captured_at="2026-08-03T12:00:00+00:00",
        mutations=mutations,
    )


# ── Codec round-trip ──────────────────────────────────────────────


def test_json_round_trip_preserves_everything():
    journal = _make_journal(
        (
            _make_mutation(),
            _make_mutation(
                op=MutationOp.SET_ATTRIBUTE,
                attribute="Name",
                pset="",
                prop="",
                old_value="Wall-01",
                new_value="Wall-01-Renamed",
            ),
        )
    )
    decoded = MutationJournal.from_json_dict(journal.to_json_dict())
    assert decoded == journal


def test_to_json_dict_is_json_safe():
    import json

    journal = _make_journal((_make_mutation(new_value=True),))
    payload = journal.to_json_dict()
    assert payload["schema_version"] == JOURNAL_SCHEMA_VERSION
    # Must survive a real JSONField-style dump/load cycle.
    assert json.loads(json.dumps(payload)) == payload


def test_decode_rejects_wrong_schema_version():
    payload = _make_journal((_make_mutation(),)).to_json_dict()
    payload["schema_version"] = 99
    with pytest.raises(JournalDecodeError, match="schema_version"):
        MutationJournal.from_json_dict(payload)


def test_decode_rejects_unknown_op():
    payload = _make_journal((_make_mutation(),)).to_json_dict()
    payload["mutations"][0]["op"] = "EXPLODE_ENTITY"
    with pytest.raises(JournalDecodeError, match="unknown op"):
        MutationJournal.from_json_dict(payload)


def test_decode_rejects_empty_mutations():
    with pytest.raises(JournalDecodeError, match="no mutations"):
        MutationJournal.from_json_dict({"schema_version": JOURNAL_SCHEMA_VERSION, "mutations": []})


def test_decode_rejects_non_dict():
    with pytest.raises(JournalDecodeError):
        MutationJournal.from_json_dict("not a dict")


def test_affected_global_ids_deduplicates_and_skips_empty():
    journal = _make_journal(
        (
            _make_mutation(),
            _make_mutation(prop="IsExternal"),
            _make_mutation(op=MutationOp.CREATE_ENTITY, global_id="", pset="", prop=""),
        )
    )
    assert journal.affected_global_ids == {"2O2Fr$t4X7Zf8NOew3FLOH"}


# ── EntityChange adapter ──────────────────────────────────────────


def _applied(mutation: Mutation, actual_old, stale=False) -> AppliedJournal:
    journal = _make_journal((mutation,))
    return AppliedJournal(
        journal=journal,
        applied=(AppliedMutation(mutation=mutation, actual_old_value=actual_old, stale=stale),),
    )


def test_adapter_maps_set_property():
    change = applied_to_entity_changes(_applied(_make_mutation(), "EI60"))[0]
    assert change.pset == "Pset_WallCommon"
    assert change.property == "FireRating"
    assert change.old_value == "EI60"
    assert change.new_value == "EI120"


def test_adapter_maps_attribute_sentinel():
    mutation = _make_mutation(
        op=MutationOp.SET_ATTRIBUTE, attribute="Name", pset="", prop="", new_value="W-2"
    )
    change = applied_to_entity_changes(_applied(mutation, "Wall-01"))[0]
    assert change.pset == "(attribute)"
    assert change.property == "Name"
    assert change.new_value == "W-2"


def test_adapter_maps_remove_and_none_sentinels():
    mutation = _make_mutation(op=MutationOp.REMOVE_PROPERTY, new_value=None)
    change = applied_to_entity_changes(_applied(mutation, None))[0]
    assert change.old_value == "(none)"
    assert change.new_value == "(removed)"


def test_stale_count():
    mutation = _make_mutation()
    result = _applied(mutation, "EI90", stale=True)
    assert result.stale_count == 1


# ── Fingerprint ───────────────────────────────────────────────────


def test_fingerprint_is_stable_and_content_sensitive(tmp_path):
    original = compute_fingerprint(FIXTURE_PATH)
    assert original == compute_fingerprint(FIXTURE_PATH)

    copy = tmp_path / "copy.ifc"
    copy.write_bytes(FIXTURE_PATH.read_bytes())
    assert compute_fingerprint(copy) == original

    copy.write_bytes(FIXTURE_PATH.read_bytes() + b"\n/* tampered */")
    assert compute_fingerprint(copy) != original


# ── Tier 3 lifecycle ops ──────────────────────────────────────────


def test_adapter_uses_execution_time_global_id_for_create():
    """A CREATE's GlobalId only exists after the writer mints it — the
    adapter must take it from result, not from the (empty) mutation."""
    mutation = _make_mutation(
        op=MutationOp.CREATE_ENTITY,
        global_id="",
        entity_name="Fire Zone A",
        ifc_type="IfcZone",
        pset="",
        prop="",
        old_value=None,
        new_value="Fire Zone A",
    )
    journal = _make_journal((mutation,))
    applied = AppliedJournal(
        journal=journal,
        applied=(
            AppliedMutation(
                mutation=mutation,
                actual_old_value=None,
                stale=False,
                result={
                    "global_id": "1minted$GUID000000000",
                    "ifc_type": "IfcZone",
                    "name": "Fire Zone A",
                    "created": True,
                },
            ),
        ),
    )

    change = applied_to_entity_changes(applied)[0]
    assert change.global_id == "1minted$GUID000000000"
    assert change.pset == "(entity)"
    assert change.property == "CREATE"
    assert change.old_value == "(does not exist)"
    assert change.new_value == "IfcZone: Fire Zone A"


def test_adapter_maps_delete_entity():
    mutation = _make_mutation(
        op=MutationOp.DELETE_ENTITY,
        entity_name="Chair-01",
        ifc_type="IfcFurnishingElement",
        pset="",
        prop="",
        old_value="Chair-01",
        new_value=None,
    )
    journal = _make_journal((mutation,))
    applied = AppliedJournal(
        journal=journal,
        applied=(
            AppliedMutation(
                mutation=mutation,
                actual_old_value="Chair-01",
                stale=False,
                result={"deleted": True},
            ),
        ),
    )

    change = applied_to_entity_changes(applied)[0]
    assert change.pset == "(entity)"
    assert change.property == "DELETE"
    assert change.new_value == "(deleted)"


def test_adapter_maps_run_code_from_result():
    """RUN_CODE self-reports each change; the adapter mirrors the legacy row."""
    mutation = _make_mutation(
        op=MutationOp.RUN_CODE, global_id="", pset="", prop="", new_value=None
    )
    journal = _make_journal((mutation,))
    applied = AppliedJournal(
        journal=journal,
        applied=(
            AppliedMutation(
                mutation=mutation,
                actual_old_value=None,
                stale=False,
                result={
                    "global_id": "3xCodeTouched00000000",
                    "entity_name": "Wall-9",
                    "ifc_type": "IfcWall",
                    "name": "Wall-9",
                    "description": "moved to Level 2",
                    "old_value": "Level 1",
                    "new_value": "Level 2",
                },
            ),
        ),
    )

    change = applied_to_entity_changes(applied)[0]
    assert change.global_id == "3xCodeTouched00000000"
    assert change.pset == "(code)"
    assert change.property == "moved to Level 2"
    assert (change.old_value, change.new_value) == ("Level 1", "Level 2")


def test_applied_mutation_result_defaults_empty():
    """Existing construction sites omit `result` — it must default cleanly."""
    item = AppliedMutation(mutation=_make_mutation(), actual_old_value="EI60", stale=False)
    assert item.result == {}
