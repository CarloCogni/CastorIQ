# writeback/tests/test_intent_assembler.py
"""Tests for the V2-to-legacy intent shape assembler."""

from unittest.mock import MagicMock

from writeback.services.intent_assembler import (
    assemble_tier1_intent,
    assemble_tier2_intent,
    assemble_tier3_payload,
    derive_tier3_inputs,
)
from writeback.services.tier_router import RoutingResult


def _entity_mock(global_id, name, ifc_type):
    e = MagicMock()
    e.global_id = global_id
    e.name = name
    e.ifc_type = ifc_type
    return e


def _resolution_mock(entities):
    r = MagicMock()
    r.entities = entities
    return r


class TestAssembleTier1Intent:
    def test_property_segment_assembles_set_property_intent(self):
        seg = {
            "kind": "PROPERTY",
            "slots": {
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "value": "EI120",
            },
        }
        routing = RoutingResult(tier=1, operation="SET_PROPERTY")
        intent = assemble_tier1_intent(seg, routing)

        assert intent["tier"] == 1
        assert intent["operation"] == "SET_PROPERTY"
        assert intent["pset"] == "Pset_WallCommon"
        assert intent["property"] == "FireRating"
        assert intent["new_value"] == "EI120"
        assert "explanation" in intent
        assert intent["confidence"] == 80

    def test_attribute_segment_assembles_set_attribute_intent(self):
        seg = {
            "kind": "ATTRIBUTE",
            "slots": {"attribute": "Name", "value": "D-007-Updated"},
        }
        routing = RoutingResult(tier=1, operation="SET_ATTRIBUTE")
        intent = assemble_tier1_intent(seg, routing)

        assert intent["operation"] == "SET_ATTRIBUTE"
        assert intent["attribute"] == "Name"
        assert intent["new_value"] == "D-007-Updated"
        assert "pset" not in intent  # attributes don't carry pset

    def test_warnings_propagated_to_intent(self):
        seg = {
            "kind": "PROPERTY",
            "slots": {"pset": "", "property": "FireRating", "value": "EI120"},
            "warnings": ["Value EI120 not in user request"],
        }
        routing = RoutingResult(tier=1, operation="SET_PROPERTY")
        intent = assemble_tier1_intent(seg, routing)
        assert intent["warnings"] == ["Value EI120 not in user request"]


class TestAssembleTier2Intent:
    def test_single_pset_segment_becomes_one_step(self):
        seg = {
            "kind": "PSET",
            "slots": {
                "operation": "ADD_PSET",
                "pset_name": "Pset_Maintenance",
                "properties": {"Inspector": "TBD"},
            },
        }
        plan = assemble_tier2_intent([seg])

        assert plan["tier"] == 2
        assert len(plan["plan"]) == 1
        step = plan["plan"][0]
        assert step["operation"] == "ADD_PSET"
        assert step["params"]["pset_name"] == "Pset_Maintenance"
        assert step["params"]["properties"]["Inspector"] == "TBD"
        assert step["filter"] == {}  # filled in by propose() pipeline

    def test_remove_pset_segment(self):
        seg = {
            "kind": "PSET",
            "slots": {"operation": "REMOVE_PSET", "pset_name": "Pset_Custom"},
        }
        plan = assemble_tier2_intent([seg])
        step = plan["plan"][0]
        assert step["operation"] == "REMOVE_PSET"
        assert step["params"]["pset_name"] == "Pset_Custom"

    def test_property_segment_in_t2_plan(self):
        """Mixed-tier requests routed to T2 carry property steps too."""
        seg = {
            "kind": "PROPERTY",
            "slots": {"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
        }
        plan = assemble_tier2_intent([seg])
        step = plan["plan"][0]
        assert step["operation"] == "SET_PROPERTY"
        assert step["params"]["pset"] == "Pset_WallCommon"

    def test_multi_segment_plan(self):
        segments = [
            {
                "kind": "PSET",
                "slots": {
                    "operation": "ADD_PSET",
                    "pset_name": "Pset_A",
                    "properties": {},
                },
            },
            {
                "kind": "PSET",
                "slots": {
                    "operation": "ADD_PSET",
                    "pset_name": "Pset_B",
                    "properties": {},
                },
            },
        ]
        plan = assemble_tier2_intent(segments)
        assert len(plan["plan"]) == 2
        assert plan["plan"][0]["step"] == 1
        assert plan["plan"][1]["step"] == 2

    def test_unsupported_kind_is_skipped_with_log(self):
        segments = [
            {"kind": "UNCLEAR", "slots": {}},
            {
                "kind": "PSET",
                "slots": {
                    "operation": "ADD_PSET",
                    "pset_name": "Pset_X",
                    "properties": {},
                },
            },
        ]
        plan = assemble_tier2_intent(segments)
        # UNCLEAR was skipped, PSET became step 2 (preserves index counting).
        assert len(plan["plan"]) == 1
        assert plan["plan"][0]["operation"] == "ADD_PSET"


class TestAssembleTier3Payload:
    def test_packages_code_with_explanation_and_confidence(self):
        payload = assemble_tier3_payload(
            segments=[],
            code="def modify_ifc(model):\n    return {'summary': 'noop', 'changes': []}",
            explanation="Test code",
        )
        assert payload["tier"] == 3
        assert "modify_ifc" in payload["code"]
        assert payload["explanation"] == "Test code"
        assert payload["confidence"] == 70


class TestDeriveTier3Inputs:
    def test_create_segment_extracts_class_names_parent(self):
        parent = _entity_mock("parent_guid", "Level 1", "IfcBuildingStorey")
        seg = {
            "kind": "CREATE",
            "slots": {
                "entity_class": "IfcZone",
                "names": ["Fire Zone A", "Fire Zone B"],
                "parent_phrase": "Level 1",
            },
            "parent_resolution": _resolution_mock([parent]),
        }
        inputs = derive_tier3_inputs([seg])

        assert inputs["entity_class"] == "IfcZone"
        assert inputs["proposed_names"] == ["Fire Zone A", "Fire Zone B"]
        assert len(inputs["existing_parents"]) == 1
        assert inputs["existing_parents"][0]["global_id"] == "parent_guid"
        assert inputs["existing_parents"][0]["name"] == "Level 1"
        assert inputs["existing_parents"][0]["ifc_type"] == "IfcBuildingStorey"
        assert inputs["targets_to_delete"] == []

    def test_create_without_parent_resolution(self):
        seg = {
            "kind": "CREATE",
            "slots": {
                "entity_class": "IfcZone",
                "names": ["Fire Zone A"],
                "parent_phrase": "",
            },
        }
        inputs = derive_tier3_inputs([seg])
        assert inputs["existing_parents"] == []
        assert inputs["proposed_names"] == ["Fire Zone A"]

    def test_delete_segment_extracts_targets(self):
        target = _entity_mock("chair_guid", "Chair-001", "IfcFurniture")
        seg = {
            "kind": "DELETE",
            "resolution": _resolution_mock([target]),
        }
        inputs = derive_tier3_inputs([seg])
        assert len(inputs["targets_to_delete"]) == 1
        assert inputs["targets_to_delete"][0]["name"] == "Chair-001"

    def test_relationship_segment_extracts_targets(self):
        target = _entity_mock("wall_guid", "Wall-01", "IfcWall")
        seg = {
            "kind": "RELATIONSHIP",
            "resolution": _resolution_mock([target]),
        }
        inputs = derive_tier3_inputs([seg])
        assert inputs["targets_to_delete"][0]["global_id"] == "wall_guid"

    def test_create_dedupes_names_across_segments(self):
        segments = [
            {
                "kind": "CREATE",
                "slots": {
                    "entity_class": "IfcZone",
                    "names": ["Fire Zone A"],
                    "parent_phrase": "",
                },
            },
            {
                "kind": "CREATE",
                "slots": {
                    "entity_class": "IfcZone",
                    "names": ["Fire Zone A", "Fire Zone B"],
                    "parent_phrase": "",
                },
            },
        ]
        inputs = derive_tier3_inputs(segments)
        # 'Fire Zone A' is deduplicated across the two segments.
        assert inputs["proposed_names"] == ["Fire Zone A", "Fire Zone B"]

    def test_empty_segments_returns_empty_lists(self):
        inputs = derive_tier3_inputs([])
        assert inputs["existing_parents"] == []
        assert inputs["proposed_names"] == []
        assert inputs["entity_class"] == ""
        assert inputs["targets_to_delete"] == []
