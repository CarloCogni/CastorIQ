# ifc_processor/tests/test_ifc_standard_psets.py
"""Unit tests for the standard psets registry helpers.

The registry's data is auto-generated; these tests cover the helper
functions on top of it, especially the tolerant resolvers used by the
writeback router to canonicalize user-typed pset / property names.
"""

import pytest

from ifc_processor.services.ifc_standard_psets import (
    lookup_property,
    resolve_property_name,
    resolve_pset_name,
)


class TestResolvePsetName:
    def test_canonical_passes_through(self):
        assert resolve_pset_name("Pset_WallCommon") == "Pset_WallCommon"

    @pytest.mark.parametrize(
        "variant",
        [
            "pset_wallcommon",
            "PSET_WALLCOMMON",
            "Pset-Wall-Common",
            "pset wall common",
            "PsetWallCommon",
        ],
    )
    def test_tolerant_variants_canonicalize(self, variant):
        assert resolve_pset_name(variant) == "Pset_WallCommon"

    def test_unknown_pset_returns_none(self):
        assert resolve_pset_name("Pset_TotallyMadeUp") is None

    def test_empty_input_returns_none(self):
        assert resolve_pset_name("") is None
        assert resolve_pset_name(None) is None  # type: ignore[arg-type]


class TestResolvePropertyName:
    def test_canonical_passes_through(self):
        assert resolve_property_name("Pset_WallCommon", "FireRating") == "FireRating"

    @pytest.mark.parametrize(
        "variant",
        [
            "fire rating",
            "FIRE_RATING",
            "fire-rating",
            "  FireRating  ",
            "firerating",
        ],
    )
    def test_tolerant_property_variants_canonicalize(self, variant):
        assert resolve_property_name("Pset_WallCommon", variant) == "FireRating"

    def test_tolerant_pset_with_tolerant_property(self):
        # Both pset and property in user wording — both canonicalize.
        assert resolve_property_name("pset_wallcommon", "fire rating") == "FireRating"

    def test_unknown_property_returns_none(self):
        assert resolve_property_name("Pset_WallCommon", "nonexistent thing") is None

    def test_unknown_pset_returns_none(self):
        assert resolve_property_name("Pset_TotallyMadeUp", "FireRating") is None

    def test_empty_input_returns_none(self):
        assert resolve_property_name("Pset_WallCommon", "") is None
        assert resolve_property_name("", "FireRating") is None


class TestLookupPropertyStaysStrict:
    """``lookup_property`` is used as an exact-match boolean gate in
    several places (typo detection, hint validation, fallback
    SET_PROPERTY → ADD_PROPERTY in the validator). It must stay strict;
    only the new resolvers are tolerant.
    """

    def test_canonical_lookup_returns_type_tuple(self):
        result = lookup_property("Pset_WallCommon", "FireRating")
        assert result is not None
        type_str, _enum_values = result
        assert type_str == "string"

    def test_lookup_remains_case_sensitive(self):
        # If this ever returns non-None, it means lookup_property was
        # accidentally made tolerant — the strict callers (validator,
        # hint validator) would then mask typos and let fabricated LLM
        # suggestions through.
        assert lookup_property("Pset_WallCommon", "fire rating") is None
        assert lookup_property("pset_wallcommon", "FireRating") is None
