# writeback/tests/test_verifier.py
"""The deterministic checks: scope, aggregation and the one flag rule (spec V-1, V-3)."""

import pytest

from ifc_processor.services.ifc_diff import IfcDiff, PropertyChange
from writeback.services.verifier import (
    aggregate_rows,
    flag_rows,
    flagged_keys,
    is_empty,
    scope_error,
)


def _diff(**overrides):
    base = {
        "schema_changed": False,
        "type_count_delta": {},
        "added_global_ids": [],
        "removed_global_ids": [],
        "geometry_changed": [],
        "property_changes": [],
        "attribute_changes": [],
    }
    base.update(overrides)
    return base


def _prop(gid, pset, prop, before, after):
    return {"global_id": gid, "pset": pset, "prop": prop, "before": before, "after": after}


WALLS = ["W1", "W2", "W3", "W4", "W5"]
FIRE_RATING_ROWS = [_prop(g, "Pset_WallCommon", "FireRating", None, "EI60") for g in WALLS]


# ── scope ─────────────────────────────────────────────────────────


def test_scope_error_is_none_when_every_change_is_on_a_target():
    """Five property rows on five targets is exactly the allowed shape."""
    assert scope_error(_diff(property_changes=FIRE_RATING_ROWS), WALLS) is None


def test_scope_error_names_a_change_outside_the_selection():
    """A sixth wall the selection did not return is a violation with its id in the text."""
    rows = FIRE_RATING_ROWS + [_prop("W6", "Pset_WallCommon", "FireRating", None, "EI60")]

    error = scope_error(_diff(property_changes=rows), WALLS)

    assert error is not None
    assert "outside the selection" in error
    assert "W6" in error


def test_scope_error_geometry_is_always_a_violation():
    """Geometry drift on a target is still forbidden."""
    error = scope_error(_diff(geometry_changed=["W1"]), WALLS)
    assert error is not None
    assert "geometry" in error


def test_scope_error_population_change_is_not_a_violation():
    """A new entity is a flagged row later, never a scope error."""
    assert scope_error(_diff(added_global_ids=["NEW1"]), WALLS) is None


def test_is_empty_detects_a_no_change_run():
    """An all-empty diff is the 'already so' case."""
    assert is_empty(_diff())
    assert not is_empty(_diff(property_changes=FIRE_RATING_ROWS))


# ── aggregation ───────────────────────────────────────────────────


def test_aggregate_rows_groups_by_pset_property_before_after():
    """Five identical property changes become one row with count 5 and five ids."""
    rows = aggregate_rows(_diff(property_changes=FIRE_RATING_ROWS))

    assert len(rows) == 1
    assert (rows[0].pset, rows[0].prop, rows[0].before, rows[0].after) == (
        "Pset_WallCommon",
        "FireRating",
        None,
        "EI60",
    )
    assert rows[0].count == 5
    assert rows[0].global_ids == WALLS


def test_aggregate_rows_keeps_different_befores_apart_and_lists_population_rows():
    """Different before-values are different rows; added/removed are one row each."""
    diff = _diff(
        property_changes=[
            _prop("W1", "Pset_WallCommon", "FireRating", "EI30", "EI60"),
            _prop("W2", "Pset_WallCommon", "FireRating", None, "EI60"),
        ],
        attribute_changes=[_prop("W3", "", "Name", "old", "new")],
        added_global_ids=["NEW1"],
        removed_global_ids=["OLD1"],
    )

    rows = aggregate_rows(diff)

    assert [r.kind for r in rows] == ["property", "property", "attribute", "added", "removed"]
    assert rows[2].label == "Name"


def test_row_keys_are_stable_across_calls():
    """The approve request carries keys; recomputing them must give the same strings."""
    diff = _diff(property_changes=FIRE_RATING_ROWS, added_global_ids=["NEW1"])
    assert [r.key for r in aggregate_rows(diff)] == [r.key for r in aggregate_rows(diff)]


# ── the one flag rule ─────────────────────────────────────────────


def test_value_present_in_the_request_is_not_flagged():
    """'EI60' appears in the request, so the row is normal."""
    rows = flag_rows(
        aggregate_rows(_diff(property_changes=FIRE_RATING_ROWS)),
        "Set the fire rating of all walls to EI60",
    )
    assert rows[0].flagged is False


def test_value_absent_from_the_request_is_flagged():
    """A second property copied from the api sheet is a value the user never typed."""
    diff = _diff(
        property_changes=FIRE_RATING_ROWS
        + [_prop("W1", "Pset_WallCommon", "IsExternal", True, "yes")]
    )
    rows = flag_rows(aggregate_rows(diff), "Set the fire rating of all walls to EI60")

    flagged = [r for r in rows if r.flagged]
    assert [r.prop for r in flagged] == ["IsExternal"]
    assert rows[0].flagged, "flagged rows sort first"


@pytest.mark.parametrize("after", [True, False, None])
def test_booleans_and_removals_are_never_flagged(after):
    """A boolean cannot appear verbatim in prose; None is a removal the user asked for."""
    diff = _diff(property_changes=[_prop("W1", "Pset_WallCommon", "IsExternal", "x", after)])
    rows = flag_rows(aggregate_rows(diff), "mark the wall as not external / remove the value")
    assert rows[0].flagged is False


def test_remove_executed_as_set_to_empty_is_flagged():
    """'remove FireRating' executed as set-to-"" is not a removal and is flagged."""
    diff = _diff(property_changes=[_prop("W1", "Pset_WallCommon", "FireRating", "EI60", "")])
    rows = flag_rows(aggregate_rows(diff), "remove FireRating from the wall")
    assert rows[0].flagged is True


def test_every_added_or_removed_entity_is_a_flagged_row():
    """A creation flags one row per new entity even when the request asked for it."""
    diff = _diff(added_global_ids=["Z1", "Z2", "Z3"])
    rows = flag_rows(aggregate_rows(diff), "create three zones")
    assert [r.flagged for r in rows] == [True, True, True]


def test_unit_conversion_is_flagged():
    """The user said 0.24, the code wrote 240; the number is not in the request."""
    diff = _diff(
        property_changes=[_prop("W1", "Pset_WallCommon", "ThermalTransmittance", 0.3, 240)]
    )
    rows = flag_rows(aggregate_rows(diff), "set the U-value of the wall to 0.24")
    assert rows[0].flagged is True


def test_numeric_value_matches_when_written_as_in_the_request():
    """0.18 written as a float still matches the '0.18' the user typed; 3.0 matches '3'."""
    diff = _diff(
        property_changes=[
            _prop("W1", "Pset_WallCommon", "ThermalTransmittance", 0.3, 0.18),
            _prop("D1", "Pset_DoorCommon", "FireExit", 1.0, 3.0),
        ]
    )
    rows = flag_rows(aggregate_rows(diff), "set U-value to 0.18 and FireExit to 3")
    assert [r.flagged for r in rows] == [False, False]


def test_flag_rule_is_case_insensitive():
    """'ei60' in the request matches 'EI60' in the diff."""
    rows = flag_rows(
        aggregate_rows(_diff(property_changes=FIRE_RATING_ROWS)), "fire rating ei60 please"
    )
    assert rows[0].flagged is False


def test_mass_write_over_a_heterogeneous_prior_state_is_flagged():
    """TEMP4/NRS_ARK shape: EI90, EI30 and no rating all become EI60 in one proposal.

    EI60 is the literal value typed and FireRating is the property named, so the
    value/property checks are silent on every row; the EI90->EI60 rows lower an
    existing rating and would otherwise approve on one click.
    """
    diff = _diff(
        property_changes=(
            [_prop(f"A{i}", "Pset_WallCommon", "FireRating", "EI 90", "EI60") for i in range(9)]
            + [_prop(f"B{i}", "Pset_WallCommon", "FireRating", None, "EI60") for i in range(271)]
            + [_prop(f"C{i}", "Pset_WallCommon", "FireRating", "EI 30", "EI60") for i in range(53)]
            + [_prop(f"D{i}", "Pset_WallCommon", "FireRating", "EI 60", "EI60") for i in range(134)]
        )
    )

    rows = flag_rows(
        aggregate_rows(diff),
        "Set Pset_WallCommon.FireRating to EI60 on all interior partition walls",
    )

    assert len(rows) == 4
    assert all(r.flagged for r in rows)
    assert {r.flag_reason for r in rows} == {"heterogeneous"}


def test_a_single_target_edit_has_one_before_value_and_is_not_flagged():
    """One target, one before-value: the ordinary case the rule must not touch."""
    diff = _diff(property_changes=[_prop("W1", "Pset_WallCommon", "FireRating", "EI30", "EI60")])
    rows = flag_rows(aggregate_rows(diff), "set FireRating to EI60 on wall W1")
    assert rows[0].flag_reason == ""


def test_a_uniform_mass_edit_has_one_before_value_and_is_not_flagged():
    """Five targets sharing the same prior value is an ordinary edit, not a mixed one."""
    rows = flag_rows(
        aggregate_rows(_diff(property_changes=FIRE_RATING_ROWS)),
        "Set the fire rating of all walls to EI60",
    )
    assert all(r.flag_reason == "" for r in rows)


def test_a_removal_is_exempt_from_the_mixed_prior_value_rule():
    """Corpus case 6.3 shape: two Reference strings, both removed, is not an overwrite."""
    diff = _diff(
        property_changes=[
            _prop("W1", "Pset_WallCommon", "Reference", "Wall-Ext_102Bwk-75Ins-100LBlk-12P", None),
            _prop("W2", "Pset_WallCommon", "Reference", "Wall-Ext_102Bwk-75Ins-100LBlk-12P", None),
            _prop("W3", "Pset_WallCommon", "Reference", "Wall-Ext_102Bwk-75Ins-100LBlk-12P", None),
            _prop("W4", "Pset_WallCommon", "Reference", "Wall-Partn_12P-70MStd-12P", None),
            _prop("W5", "Pset_WallCommon", "Reference", "Wall-Partn_12P-70MStd-12P", None),
        ]
    )
    rows = flag_rows(aggregate_rows(diff), "remove Reference from all walls")
    assert all(r.flag_reason == "" for r in rows)


def test_flagged_keys_returns_only_the_flagged_rows():
    """The approve check compares this set with the keys the client sent."""
    diff = _diff(property_changes=FIRE_RATING_ROWS, added_global_ids=["NEW1"])
    keys = flagged_keys(diff, "Set fire rating to EI60")
    rows = aggregate_rows(diff)
    assert keys == {rows[1].key}


def test_added_and_removed_rows_carry_the_type_and_skip_relationships():
    """Typed population rows come from added_objects; a bare added_global_ids list is a fallback."""
    diff = _diff(
        added_global_ids=["Z1", "REL1"],
        added_objects={"Z1": "IfcZone"},
        removed_objects={"W1": "IfcWall"},
    )
    rows = aggregate_rows(diff)
    assert [(r.kind, r.prop, r.label) for r in rows] == [
        ("added", "IfcZone", "IfcZone added"),
        ("removed", "IfcWall", "IfcWall removed"),
    ]


def test_relationship_values_are_flagged_only_when_a_new_name_is_absent_from_the_request():
    """Materials: () → ('Concrete',) is normal when 'concrete' is in the request."""
    diff = _diff(attribute_changes=[_prop("W1", "", "Materials", [], ["Concrete C30/37"])])
    assert (
        flag_rows(aggregate_rows(diff), "set the material to concrete c30/37")[0].flagged is False
    )
    assert flag_rows(aggregate_rows(diff), "set the material to steel")[0].flagged is True


# ── population and presence rows ──────────────────────────────────


def test_is_empty_judges_population_on_objects():
    """A pset or relationship that appeared is not a change on its own; an object is."""
    assert is_empty(_diff(added_global_ids=["PSET1"], added_objects={}))
    assert not is_empty(_diff(added_global_ids=["NEW1"], added_objects={"NEW1": "IfcWall"}))
    assert not is_empty(_diff(added_global_ids=["NEW1"]))  # an older diff without typed keys


def test_a_pset_presence_row_is_labelled_by_the_pset_and_follows_the_flag_rule():
    """An empty pset added is one row named after the pset; flagged unless the request names it."""
    diff = _diff(
        property_changes=[_prop("W1", "Pset_FireCompliance", "", None, "Pset_FireCompliance")]
    )

    named = flag_rows(aggregate_rows(diff), "add Pset_FireCompliance to the wall")
    unnamed = flag_rows(aggregate_rows(diff), "add the fire compliance pset to the wall")

    assert named[0].label == "Pset_FireCompliance"
    assert named[0].flagged is False
    assert unnamed[0].flagged is True


def test_scope_error_labels_attribute_rows_without_a_leading_dot():
    error = scope_error(_diff(attribute_changes=[_prop("W9", "", "Name", "a", "b")]), WALLS)
    assert "W9 Name" in error
    assert " .Name" not in error


@pytest.mark.parametrize(
    ("diff", "allowed"),
    [
        (IfcDiff(), set()),
        (IfcDiff(attribute_changes=[PropertyChange("A", "", "Name", "x", "y")]), {"A"}),
        (IfcDiff(attribute_changes=[PropertyChange("A", "", "Name", "x", "y")]), {"B"}),
        (IfcDiff(property_changes=[PropertyChange("A", "P", "Q", 1, 2)]), set()),
        (IfcDiff(geometry_changed=frozenset({"A"})), {"A"}),
        (IfcDiff(schema_changed=True), {"A"}),
        (
            IfcDiff(
                added_global_ids=frozenset({"N"}),
                added_objects={"N": "IfcWall"},
                type_count_delta={"IfcWall": 1},
            ),
            set(),
        ),
        (IfcDiff(removed_global_ids=frozenset({"A"}), removed_objects={"A": "IfcDoor"}), {"B"}),
        (
            IfcDiff(
                added_global_ids=frozenset({"N"}),
                added_objects={"N": "IfcZone"},
                added_attachments={"N": "S"},
            ),
            {"P"},
        ),
        (
            IfcDiff(
                added_global_ids=frozenset({"N"}),
                added_objects={"N": "IfcZone"},
                added_attachments={"N": "S"},
            ),
            {"S"},
        ),
        (
            IfcDiff(
                added_global_ids=frozenset({"N", "S"}),
                added_objects={"N": "IfcSpace", "S": "IfcBuildingStorey"},
                added_attachments={"N": "S"},
            ),
            {"P"},
        ),
    ],
)
def test_scope_error_agrees_with_ifc_diff_unexpected(diff, allowed):
    """The dict-based rule the pipeline gates on and IfcDiff.unexpected are one rule.

    The child returns ``as_dict()`` across the process boundary and the benchmark
    re-reads the written copy through ``IfcDiff``; a drift between the two
    implementations would let one side pass what the other refuses.
    """
    from_dict = scope_error(diff.as_dict(), allowed) is None
    from_object = diff.unexpected(allowed=allowed, allow_population_change=True) == []
    assert from_dict == from_object


# ── the property half of the flag rule ───────────────────────────


def test_property_absent_from_the_request_is_flagged():
    """A right value on the wrong property: 120 written to ThermalTransmittance for a fire rating."""
    diff = _diff(
        property_changes=[_prop("D1", "Pset_DoorCommon", "ThermalTransmittance", 3.7021, 120.0)]
    )

    rows = flag_rows(aggregate_rows(diff), "change fire rating on all doors to 120")

    assert rows[0].flagged is True
    assert rows[0].flag_reason == "property"


def test_property_named_in_the_request_is_not_flagged():
    """FireRating for "fire rating": the squashed name is in the squashed request."""
    diff = _diff(property_changes=[_prop("D1", "Pset_DoorCommon", "FireRating", None, "120")])

    rows = flag_rows(aggregate_rows(diff), "change fire rating on all doors to 120")

    assert rows[0].flagged is False
    assert rows[0].flag_reason == ""


@pytest.mark.parametrize(
    "kind, pset, prop, after, text",
    [
        (
            "property",
            "Pset_WallCommon",
            "ThermalTransmittance",
            0.18,
            "set the U-value of the walls to 0.18",
        ),
        ("property", "Pset_WallCommon", "FireRating", "EI60", "make the walls fireproof to EI60"),
        ("property", "Pset_WallCommon", "IsExternal", True, "make the walls external"),
        ("property", "Pset_WallCommon", "LoadBearing", True, "mark the walls as load-bearing"),
        (
            "property",
            "Pset_Maintenance",
            "Inspector",
            "Jane",
            "set Inspector to Jane in Pset_Maintenance",
        ),
        ("attribute", "", "Materials", ["Concrete"], "assign material Concrete to the walls"),
        ("attribute", "", "Container", "Roof", "move the door to the Roof"),
        ("attribute", "", "Parent", "Roof", "move the bedroom to the Roof"),
        ("attribute", "", "Name", "Wall-North", "rename wall :285330 to Wall-North"),
    ],
)
def test_property_word_or_synonym_in_the_request_is_not_flagged(kind, pset, prop, after, text):
    """A camel word, a plural or a listed synonym names the property; no tick needed."""
    row = _prop("W1", pset, prop, None, after)
    diff = _diff(**{f"{kind}_changes": [row]})

    rows = flag_rows(aggregate_rows(diff), text)

    assert rows[0].flag_reason == ""
    assert rows[0].flagged is False


def test_value_reason_is_kept_when_the_property_is_named():
    """Both halves run in order: a named property with a foreign value flags for the value."""
    diff = _diff(property_changes=[_prop("W1", "Pset_WallCommon", "FireRating", None, "EI999")])

    rows = flag_rows(aggregate_rows(diff), "set the fire rating of the walls to EI60")

    assert rows[0].flag_reason == "value"


def test_population_rows_carry_their_kind_as_the_reason():
    """Added and removed entities are flagged with the reason the badge shows."""
    diff = _diff(added_objects={"NEW1": "IfcSpace"}, removed_objects={"OLD1": "IfcWall"})

    rows = flag_rows(aggregate_rows(diff), "add a space and delete a wall")

    assert sorted(r.flag_reason for r in rows) == ["added", "removed"]
    assert all(r.flagged for r in rows)


def test_property_flag_needs_a_tick_at_approval():
    """The approve gate sees a property flag exactly like a value flag."""
    diff = _diff(
        property_changes=[_prop("D1", "Pset_DoorCommon", "ThermalTransmittance", None, 120.0)]
    )

    keys = flagged_keys(diff, "change fire rating on all doors to 120")

    assert keys == {aggregate_rows(diff)[0].key}


def test_a_new_object_attached_outside_the_selection_names_the_parent():
    """The repair text says what was hung where, and what select() should have returned."""
    diff = _diff(
        added_global_ids=["Z1"],
        added_objects={"Z1": "IfcZone"},
        added_attachments={"Z1": "ROOF"},
    )

    error = scope_error(diff, ["PROJECT"])

    assert "new IfcZone Z1 was attached to ROOF" in error
    assert "select() must return the storey, building or project" in error
    assert scope_error(diff, ["ROOF"]) is None
