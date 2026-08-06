# ifc_processor/tests/test_schema_data.py
"""Tests for the generated schema metadata tables and the lookup API.

Pure logic — no DB. The generated modules are data; these tests pin the
invariants every consumer (deterministic answers, property grounding,
description builder) relies on.
"""

from __future__ import annotations

import pytest

from ifc_processor.schema_data import entities, normalize_schema, psets
from ifc_processor.schema_data.lookup import (
    ancestors,
    expand_type,
    is_standard_pset_property,
    is_subtype_of,
    psets_for,
    resolve_property_term,
)

# ── Loading and normalization ──────────────────────────────────────────


@pytest.mark.parametrize("schema", ["IFC2X3", "IFC4", "IFC4X3"])
def test_all_schema_tables_load(schema):
    """Every supported schema resolves to non-trivial entity and pset tables."""
    assert len(entities(schema)) > 400
    assert len(psets(schema)) > 100


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IFC4", "IFC4"),
        ("ifc4", "IFC4"),
        ("IFC4X3_ADD2", "IFC4X3"),
        ("IFC2X3_TC1", "IFC2X3"),
        ("IFC9_FUTURE", "IFC4"),
        ("", "IFC4"),
    ],
)
def test_normalize_schema_tolerates_variants(raw, expected):
    """Real files carry suffixed schema strings; all must resolve to a table."""
    assert normalize_schema(raw) == expected


# ── Hierarchy ──────────────────────────────────────────────────────────


def test_expand_type_includes_wall_standard_case():
    """Counting IfcWall must include IfcWallStandardCase rows."""
    expanded = expand_type("IfcWall", "IFC4")
    assert "IfcWall" in expanded
    assert "IfcWallStandardCase" in expanded


def test_expand_type_building_element_covers_doors_and_walls():
    """The abstract building-element umbrella expands over concrete families."""
    expanded = expand_type("IfcBuildingElement", "IFC4")
    assert {"IfcWall", "IfcDoor", "IfcWindow", "IfcSlab"} <= expanded


def test_expand_type_unknown_type_returns_singleton():
    """Unknown types must not raise — downstream reports 'no rows' cleanly."""
    assert expand_type("IfcNotAThing", "IFC4") == frozenset({"IfcNotAThing"})


def test_ancestors_chain_walls_up_to_root():
    """IfcWallStandardCase walks through IfcWall to IfcRoot."""
    chain = ancestors("IfcWallStandardCase", "IFC4")
    assert chain[0] == "IfcWallStandardCase"
    assert "IfcWall" in chain
    assert chain[-1] == "IfcRoot"


def test_is_subtype_of_positive_and_negative():
    """Subtype relation holds downward only."""
    assert is_subtype_of("IfcWallStandardCase", "IfcWall", "IFC4")
    assert not is_subtype_of("IfcWall", "IfcWallStandardCase", "IFC4")
    assert not is_subtype_of("IfcDoor", "IfcWall", "IFC4")


# ── Pset applicability and property grounding ──────────────────────────


def test_psets_for_wall_includes_wall_common():
    """Pset_WallCommon is applicable to IfcWall directly."""
    assert "Pset_WallCommon" in psets_for("IfcWall", "IFC4")


def test_psets_for_inherits_through_ancestors():
    """IfcWallStandardCase inherits IfcWall's applicable psets."""
    assert "Pset_WallCommon" in psets_for("IfcWallStandardCase", "IFC4")


def test_resolve_fire_rating_on_door():
    """The flagship grounding case: 'fire rating' of doors."""
    assert resolve_property_term("fire rating", "IfcDoor") == ("Pset_DoorCommon", "FireRating")


def test_resolve_is_external_with_spacing_variants():
    """Normalization bridges 'is external' / 'IsExternal' / 'is_external'."""
    for term in ("is external", "IsExternal", "is_external"):
        assert resolve_property_term(term, "IfcWall") == ("Pset_WallCommon", "IsExternal")


def test_resolve_unknown_term_returns_none():
    """Nonsense terms must not ground to a random property."""
    assert resolve_property_term("zorbulation factor", "IfcWall") is None


def test_is_standard_pset_property():
    """Validation gate for LLM-proposed property names."""
    assert is_standard_pset_property("Pset_DoorCommon", "FireRating")
    assert not is_standard_pset_property("Pset_DoorCommon", "MadeUpProp")
    assert not is_standard_pset_property("Pset_MadeUp", "FireRating")


def test_enumeration_properties_carry_their_values():
    """Status enums survive generation with their member lists."""
    from ifc_processor.schema_data.lookup import properties_of

    status = properties_of("Pset_DoorCommon", "IFC4").get("Status", {})
    assert status.get("kind") == "enumeration"
    assert "NEW" in status.get("enum", ())
