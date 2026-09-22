# ifc_processor/tests/test_property_access.py
"""Tests for the flat→nested property view adapter."""

from __future__ import annotations

from ifc_processor.services.property_access import get_prop, nested_view


def test_nested_view_groups_pset_keys():
    """Dotted keys become pset-scoped dicts."""
    flat = {"Pset_WallCommon.FireRating": "EI60", "Pset_WallCommon.IsExternal": True}
    assert nested_view(flat) == {"Pset_WallCommon": {"FireRating": "EI60", "IsExternal": True}}


def test_nested_view_type_scope_nests_two_levels():
    """Type-inherited keys land under a 'Type' subtree."""
    flat = {"Type.Pset_DoorCommon.FireRating": "EI30"}
    assert nested_view(flat) == {"Type": {"Pset_DoorCommon": {"FireRating": "EI30"}}}


def test_nested_view_bare_keys_pass_through():
    """Bare attribute keys (OverallWidth) stay scalar at top level."""
    flat = {"OverallWidth": 1000.0, "Qto_DoorBaseQuantities.Area": 2.2}
    nested = nested_view(flat)
    assert nested["OverallWidth"] == 1000.0
    assert nested["Qto_DoorBaseQuantities"] == {"Area": 2.2}


def test_nested_view_none_and_empty_are_safe():
    """None and {} both produce an empty view."""
    assert nested_view(None) == {}
    assert nested_view({}) == {}


def test_nested_view_extra_dots_stay_in_property_name():
    """Only the first segment groups; dotted property names survive intact."""
    flat = {"Pset_X.Some.Dotted.Name": 1}
    assert nested_view(flat) == {"Pset_X": {"Some.Dotted.Name": 1}}


def test_get_prop_prefers_occurrence_over_type():
    """Occurrence value wins when both scopes carry the property."""
    flat = {
        "Pset_DoorCommon.FireRating": "EI90",
        "Type.Pset_DoorCommon.FireRating": "EI30",
    }
    assert get_prop(flat, "Pset_DoorCommon", "FireRating") == "EI90"


def test_get_prop_falls_back_to_type_scope():
    """Type-level value is used when the occurrence lacks the property."""
    flat = {"Type.Pset_DoorCommon.FireRating": "EI30"}
    assert get_prop(flat, "Pset_DoorCommon", "FireRating") == "EI30"
    assert get_prop(flat, "Pset_DoorCommon", "FireRating", include_type=False) is None
