# writeback/tests/test_tier_router.py
"""Tests for the deterministic tier router (Stage 3.5 of the V2 pipeline)."""

from unittest.mock import MagicMock

import pytest

from writeback.services.tier_router import (
    SAFE_ATTRIBUTE_BULK_LIMIT,
    RoutingResult,
    route,
)


def _segment(kind, slots=None, resolution_count=1, **extra):
    """Build a segment dict the way the V2 pipeline shapes it.

    ``resolution_count`` defaults to 1 because the most common
    routing case is "resolver matched something". Tests that need to
    exercise the empty-resolution rejection path pass ``0`` explicitly.
    UNCLEAR / OUT_OF_SCOPE / CREATE segments are exempt from the
    router's resolution check, so the default is harmless for them.
    """
    seg = {
        "kind": kind,
        "target_phrase": "",
        "value_phrase": "",
        "slots": slots or {},
    }
    if resolution_count:
        resolution = MagicMock()
        resolution.entities = [MagicMock() for _ in range(resolution_count)]
        seg["resolution"] = resolution
    seg.update(extra)
    return seg


class TestRejectPaths:
    def test_empty_segments_rejects_with_reason(self):
        result = route([])
        assert result.is_rejected
        assert "No actionable segments" in result.rejection_reason

    def test_unclear_segment_rejects_with_missing_slots(self):
        seg = _segment("UNCLEAR", missing=["target", "value"])
        result = route([seg])
        assert result.is_rejected
        assert "target" in result.rejection_reason
        assert "value" in result.rejection_reason

    def test_unclear_without_missing_uses_generic_reason(self):
        seg = _segment("UNCLEAR")
        result = route([seg])
        assert result.is_rejected
        assert "ambiguous" in result.rejection_reason.lower()

    def test_out_of_scope_with_reason_passes_reason_through(self):
        seg = _segment(
            "OUT_OF_SCOPE",
            reason="Geometric edits are out of scope.",
        )
        result = route([seg])
        assert result.is_rejected
        assert "Geometric" in result.rejection_reason

    def test_unknown_kind_rejects(self):
        seg = {"kind": "DESTROY_ALL_HUMANS"}
        result = route([seg])
        assert result.is_rejected
        assert "Unrecognised" in result.rejection_reason


class TestTier1Routing:
    def test_property_with_known_pset_routes_t1_set_property(self):
        seg = _segment(
            "PROPERTY",
            slots={"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
        )
        result = route([seg])
        assert result.tier == 1
        assert result.operation == "SET_PROPERTY"

    def test_property_with_no_pset_infers_standard_pset(self):
        """When the slot extractor returned empty pset, the router fills
        it in deterministically from the standard registry — FireRating
        on a wall lives in Pset_WallCommon. The mutated segment is what
        the downstream dispatcher and writer will see.
        """
        # Resolution carries an ifc_type_hint so get_applicable_psets
        # narrows the search to wall-related psets.
        from unittest.mock import MagicMock

        resolution = MagicMock()
        resolution.entities = [MagicMock()]
        resolution.ifc_type_hint = "IfcWall"

        seg = {
            "kind": "PROPERTY",
            "target_phrase": "Wall-01",
            "value_phrase": "FireRating to EI120",
            "slots": {"pset": "", "property": "FireRating", "value": "EI120"},
            "resolution": resolution,
        }
        result = route([seg])

        assert result.tier == 1
        assert result.operation == "SET_PROPERTY"
        # The router mutated the segment so the writer sees a populated pset.
        assert seg["slots"]["pset"] == "Pset_WallCommon"

    def test_property_with_no_pset_and_unknown_property_rejects(self):
        """Custom property (not in any standard pset) with no user-named
        pset is rejected with an actionable message instead of crashing
        the validator with 'SET_PROPERTY requires pset'.
        """
        from unittest.mock import MagicMock

        resolution = MagicMock()
        resolution.entities = [MagicMock()]
        resolution.ifc_type_hint = "IfcWall"

        seg = {
            "kind": "PROPERTY",
            "target_phrase": "all walls",
            "value_phrase": "Inspector to TBD",
            "slots": {"pset": "", "property": "Inspector", "value": "TBD"},
            "resolution": resolution,
        }
        result = route([seg])
        assert result.is_rejected
        assert "Inspector" in result.rejection_reason
        assert "Pset" in result.rejection_reason  # mentions naming a pset

    def test_property_with_custom_pset_escalates_to_t2(self):
        seg = _segment(
            "PROPERTY",
            slots={"pset": "Pset_Custom_Maintenance", "property": "Inspector", "value": "TBD"},
        )
        result = route([seg])
        assert result.tier == 2
        assert result.operation == "PLAN"

    def test_attribute_under_bulk_limit_routes_t1(self):
        seg = _segment(
            "ATTRIBUTE",
            slots={"attribute": "Name", "value": "D-007-Updated"},
            resolution_count=3,
        )
        result = route([seg])
        assert result.tier == 1
        assert result.operation == "SET_ATTRIBUTE"

    def test_attribute_over_bulk_limit_escalates_to_t2(self):
        seg = _segment(
            "ATTRIBUTE",
            slots={"attribute": "Name", "value": "All-Renamed"},
            resolution_count=SAFE_ATTRIBUTE_BULK_LIMIT + 1,
        )
        result = route([seg])
        assert result.tier == 2
        assert result.operation == "PLAN"


class TestMultiSegmentRouting:
    """Multi-segment requests route to Tier 2 (compound plan) — NOT
    Tier 1 chain. The chain primitive can't survive the unique
    constraint on ``ModificationProposal.message`` (one row per chat
    message). Tier 2 builds a single plan with one step per segment.
    """

    def test_two_property_segments_route_t2_plan(self):
        segments = [
            _segment(
                "PROPERTY",
                slots={"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
            ),
            _segment(
                "PROPERTY",
                slots={"pset": "Pset_WallCommon", "property": "IsExternal", "value": True},
            ),
        ]
        result = route(segments)
        assert result.tier == 2
        assert result.operation == "PLAN"

    def test_property_plus_attribute_routes_t2_plan(self):
        segments = [
            _segment(
                "PROPERTY",
                slots={"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
            ),
            _segment(
                "ATTRIBUTE",
                slots={"attribute": "Name", "value": "Wall-001-Renamed"},
                resolution_count=1,
            ),
        ]
        result = route(segments)
        assert result.tier == 2
        assert result.operation == "PLAN"


class TestTier2Routing:
    def test_pset_segment_routes_t2(self):
        seg = _segment(
            "PSET",
            slots={
                "operation": "ADD_PSET",
                "pset_name": "Pset_Maintenance",
                "properties": {"Inspector": "TBD"},
            },
        )
        result = route([seg])
        assert result.tier == 2
        assert result.operation == "PLAN"

    def test_property_plus_pset_routes_t2(self):
        segments = [
            _segment(
                "PROPERTY",
                slots={"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
            ),
            _segment(
                "PSET",
                slots={
                    "operation": "ADD_PSET",
                    "pset_name": "Pset_Maintenance",
                    "properties": {},
                },
            ),
        ]
        result = route(segments)
        assert result.tier == 2


class TestTier3Routing:
    def test_create_segment_routes_t3(self):
        seg = _segment(
            "CREATE",
            slots={"entity_class": "IfcZone", "names": ["Fire Zone A"], "parent_phrase": ""},
        )
        result = route([seg])
        assert result.tier == 3
        assert result.operation == "CODE"

    def test_delete_segment_routes_t3(self):
        seg = _segment("DELETE")
        result = route([seg])
        assert result.tier == 3

    def test_relationship_segment_routes_t3(self):
        seg = _segment("RELATIONSHIP")
        result = route([seg])
        assert result.tier == 3

    def test_create_plus_pset_routes_t3_dominant(self):
        """Multi-segment with any T3 kind dominates → whole request routes T3."""
        segments = [
            _segment(
                "PSET",
                slots={"operation": "ADD_PSET", "pset_name": "Pset_X", "properties": {}},
            ),
            _segment(
                "CREATE",
                slots={"entity_class": "IfcZone", "names": ["A"], "parent_phrase": ""},
            ),
        ]
        result = route(segments)
        assert result.tier == 3


class TestRoutingResultPredicates:
    def test_is_rejected_true_for_tier_zero(self):
        assert RoutingResult(tier=0).is_rejected

    @pytest.mark.parametrize("tier", [1, 2, 3])
    def test_is_rejected_false_for_real_tiers(self, tier):
        assert not RoutingResult(tier=tier, operation="X").is_rejected
