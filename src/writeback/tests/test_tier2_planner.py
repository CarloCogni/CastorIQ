# writeback/tests/test_tier2_planner.py
"""Tests for Tier2Planner — LLM always mocked."""

import json
from unittest.mock import MagicMock

import pytest

from writeback.services.tier2_planner import PlanGenerationError, Tier2Planner

VALID_PLAN = {
    "tier": 2,
    "plan": [
        {
            "step": 1,
            "operation": "ADD_PSET",
            "filter": {"ifc_type": "IfcWall"},
            "params": {
                "pset_name": "Pset_FireCompliance",
                "properties": {"Standard": "FS-2026", "Status": "Pending"},
            },
            "explanation": "Add fire compliance pset to all walls",
        },
        {
            "step": 2,
            "operation": "SET_CLASSIFICATION",
            "filter": {"ifc_type": "IfcWall"},
            "params": {"system_name": "FireSafety", "reference": "FS-2026/W"},
            "explanation": "Classify walls under fire safety",
        },
    ],
    "confidence": 0.85,
    "explanation": "Fire compliance and classification for walls",
}

TIER1_ONLY_PLAN = {
    "tier": 2,
    "plan": [
        {
            "step": 1,
            "operation": "SET_PROPERTY",
            "filter": {"ifc_type": "IfcWall"},
            "params": {"pset": "Pset_WallCommon", "property": "FireRating", "new_value": "EI120"},
            "explanation": "Set fire rating",
        }
    ],
    "confidence": 0.9,
    "explanation": "Simple property update",
}

TIER3_ESCALATION = {
    "tier": 3,
    "confidence": 0.5,
    "explanation": "This requires entity creation — needs Tier 3.",
}


@pytest.fixture
def mock_llm():
    """A mock LLM object."""
    return MagicMock()


@pytest.fixture
def planner(mock_llm):
    """Tier2Planner with LLM replaced by a mock."""
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "writeback.services.tier2_planner.get_llm", return_value=mock_llm
    ):
        instance = Tier2Planner(user=None)
        instance.llm = mock_llm
        return instance, mock_llm


def _set_llm_response(mock_llm, content: str) -> None:
    """Helper: configure mock_llm.invoke to return given JSON string."""
    response = MagicMock()
    response.content = content
    mock_llm.invoke.return_value = response


class TestGeneratePlan:
    """Tests for Tier2Planner.generate_plan()."""

    def test_valid_plan_returns_dict_with_plan_key(self, planner):
        """Valid LLM JSON → returns dict containing 'plan' list."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, json.dumps(VALID_PLAN))

        result = instance.generate_plan("Add fire compliance to walls", "5 IfcWall entities")

        assert isinstance(result, dict)
        assert "plan" in result
        assert isinstance(result["plan"], list)
        assert len(result["plan"]) == 2

    def test_valid_plan_normalizes_confidence_to_0_100(self, planner):
        """Confidence in 0-1 range is scaled to 0-100."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, json.dumps(VALID_PLAN))  # confidence=0.85

        result = instance.generate_plan("Add fire compliance", "context")

        assert result["confidence"] == 85

    def test_tier1_only_steps_accepted_as_valid_tier2_plan(self, planner):
        """Tier 2 plan that uses only Tier 1 operations is still valid."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, json.dumps(TIER1_ONLY_PLAN))

        result = instance.generate_plan("Set fire rating on walls", "context")

        assert result["tier"] == 2
        assert len(result["plan"]) == 1

    def test_tier3_escalation_passes_validation(self, planner):
        """LLM returning tier=3 is a valid escalation — no error raised."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, json.dumps(TIER3_ESCALATION))

        result = instance.generate_plan("Create a new wall", "context")

        assert result["tier"] == 3

    def test_malformed_json_raises_plan_generation_error(self, planner):
        """Non-JSON LLM response raises PlanGenerationError."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, "This is not JSON at all!")

        with pytest.raises(PlanGenerationError, match="Could not parse plan as JSON"):
            instance.generate_plan("Some request", "context")

    def test_missing_plan_key_raises_plan_generation_error(self, planner):
        """JSON dict without 'plan' key raises PlanGenerationError."""
        instance, mock_llm = planner
        _set_llm_response(mock_llm, json.dumps({"tier": 2, "confidence": 0.8}))

        with pytest.raises(PlanGenerationError, match="non-empty 'plan' array"):
            instance.generate_plan("Some request", "context")

    def test_wrong_tier_raises_plan_generation_error(self, planner):
        """JSON with tier=1 raises PlanGenerationError (expected tier 2)."""
        instance, mock_llm = planner
        bad_plan = {**VALID_PLAN, "tier": 1}
        _set_llm_response(mock_llm, json.dumps(bad_plan))

        with pytest.raises(PlanGenerationError, match="Expected tier 2"):
            instance.generate_plan("Some request", "context")

    def test_step_missing_required_fields_raises_error(self, planner):
        """Plan step missing 'operation' raises PlanGenerationError."""
        instance, mock_llm = planner
        bad_plan = {
            "tier": 2,
            "plan": [
                {
                    "step": 1,
                    # missing 'operation', 'filter', 'params', 'explanation'
                }
            ],
            "confidence": 0.5,
            "explanation": "Bad plan",
        }
        _set_llm_response(mock_llm, json.dumps(bad_plan))

        with pytest.raises(PlanGenerationError, match="missing fields"):
            instance.generate_plan("Some request", "context")

    def test_unknown_operation_raises_plan_generation_error(self, planner):
        """Plan step with unknown operation raises PlanGenerationError."""
        instance, mock_llm = planner
        bad_plan = {
            "tier": 2,
            "plan": [
                {
                    "step": 1,
                    "operation": "TELEPORT_WALL",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {},
                    "explanation": "Teleport",
                }
            ],
            "confidence": 0.5,
            "explanation": "Bad op",
        }
        _set_llm_response(mock_llm, json.dumps(bad_plan))

        with pytest.raises(PlanGenerationError, match="unknown operation"):
            instance.generate_plan("Some request", "context")

    def test_plan_exceeding_10_steps_raises_error(self, planner):
        """Plan with more than 10 steps raises PlanGenerationError."""
        instance, mock_llm = planner
        steps = [
            {
                "step": i + 1,
                "operation": "SET_PROPERTY",
                "filter": {"ifc_type": "IfcWall"},
                "params": {
                    "pset": "Pset_WallCommon",
                    "property": "FireRating",
                    "new_value": "EI60",
                },
                "explanation": f"Step {i + 1}",
            }
            for i in range(11)
        ]
        oversized_plan = {
            "tier": 2,
            "plan": steps,
            "confidence": 0.5,
            "explanation": "Too many steps",
        }
        _set_llm_response(mock_llm, json.dumps(oversized_plan))

        with pytest.raises(PlanGenerationError, match="max 10"):
            instance.generate_plan("Some request", "context")


class TestValidateStepParams:
    """Unit tests for _validate_step_params — no LLM needed."""

    def test_set_property_missing_pset_raises_error(self, planner):
        """SET_PROPERTY without 'pset' raises PlanGenerationError."""
        instance, _ = planner
        with pytest.raises(PlanGenerationError, match="missing params"):
            instance._validate_step_params(
                "SET_PROPERTY", {"property": "FireRating", "new_value": "EI60"}, "Step 1"
            )

    def test_add_pset_missing_properties_raises_error(self, planner):
        """ADD_PSET without 'properties' raises PlanGenerationError."""
        instance, _ = planner
        with pytest.raises(PlanGenerationError, match="missing params"):
            instance._validate_step_params("ADD_PSET", {"pset_name": "MyPset"}, "Step 1")

    def test_set_material_valid_params_passes(self, planner):
        """SET_MATERIAL with 'material_name' passes without error."""
        instance, _ = planner
        # Should not raise
        instance._validate_step_params("SET_MATERIAL", {"material_name": "Concrete"}, "Step 1")

    def test_copy_properties_missing_source_name_raises_error(self, planner):
        """COPY_PROPERTIES without 'source_name' raises PlanGenerationError."""
        instance, _ = planner
        with pytest.raises(PlanGenerationError, match="missing params"):
            instance._validate_step_params(
                "COPY_PROPERTIES", {"pset_name": "Pset_WallCommon"}, "Step 1"
            )
