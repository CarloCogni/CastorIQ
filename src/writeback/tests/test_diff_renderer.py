# writeback/tests/test_diff_renderer.py
"""Tests for journal-derived diff preview rows — no DB, no LLM."""

import re

from ifc_processor.services.journal import (
    Mutation,
    MutationJournal,
    MutationOp,
    new_journal_id,
    new_mutation_id,
)
from writeback.services.diff_renderer import NEW_ENTITY_PLACEHOLDER, render_diff

# 22-char base64 IFC GlobalId
_GUID_RE = re.compile(r"\b[0-9A-Za-z_$]{22}\b")


def _journal(*mutations: Mutation) -> MutationJournal:
    return MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id="file-1",
        source_tier=3,
        base_fingerprint="ab" * 32,
        captured_at="2026-08-04T12:00:00+00:00",
        mutations=tuple(mutations),
    )


def _mutation(op: MutationOp, **kw) -> Mutation:
    base = dict(id=new_mutation_id(), op=op, global_id="")
    base.update(kw)
    return Mutation(**base)


def test_create_entity_row_never_shows_a_guid():
    """preview() mints a throwaway GlobalId that won't match the applied run,
    so the preview must show a placeholder instead."""
    journal = _journal(
        _mutation(
            MutationOp.CREATE_ENTITY,
            entity_name="Fire Zone A",
            ifc_type="IfcZone",
            params={"parent_global_id": "2O2Fr$t4X7Zf8NOew3FLOH"},
        )
    )

    row = render_diff(journal)[0]

    assert row["global_id"] == NEW_ENTITY_PLACEHOLDER
    assert not _GUID_RE.search(row["global_id"])
    assert row["name"] == "Fire Zone A"
    assert row["old_value"] == "(does not exist)"
    assert row["new_value"].startswith("create IfcZone under ")


def test_create_entity_row_without_parent_omits_suffix():
    journal = _journal(
        _mutation(MutationOp.CREATE_ENTITY, entity_name="Zone B", ifc_type="IfcZone")
    )
    assert render_diff(journal)[0]["new_value"] == "create IfcZone"


def test_delete_entity_row():
    journal = _journal(
        _mutation(
            MutationOp.DELETE_ENTITY,
            global_id="3x4Kf8NOew3FLOHt4X7Zf8",
            entity_name="Chair-01",
            ifc_type="IfcFurnishingElement",
        )
    )

    row = render_diff(journal)[0]

    assert row["global_id"] == "3x4Kf8NOew3FLOHt4X7Zf8"
    assert row["name"] == "Chair-01"
    assert row["old_value"] == "Chair-01"
    assert row["new_value"] == "(deleted)"


def test_run_code_row_is_honest_about_unknown_effects():
    journal = _journal(_mutation(MutationOp.RUN_CODE, params={"code": "def modify_ifc(m): ..."}))

    row = render_diff(journal)[0]

    assert row["field"] == "Generated code"
    assert "after execution" in row["new_value"]


def test_property_rows_still_render_unchanged():
    """Tier 1/2 rows must be untouched by the lifecycle branch."""
    journal = _journal(
        _mutation(
            MutationOp.SET_PROPERTY,
            global_id="2O2Fr$t4X7Zf8NOew3FLOH",
            entity_name="Wall-01",
            ifc_type="IfcWall",
            pset="Pset_WallCommon",
            prop="FireRating",
            old_value="EI60",
            new_value="EI120",
        )
    )

    row = render_diff(journal)[0]

    assert row["field"] == "Pset_WallCommon.FireRating"
    assert (row["old_value"], row["new_value"]) == ("EI60", "EI120")
