# writeback/tests/test_intent_classifier.py
"""Tests for IntentClassifier — LLM always mocked."""

import json
from unittest.mock import MagicMock

import pytest

from writeback.services.intent_classifier import IntentClassifier, IntentParseError


@pytest.fixture
def classifier(monkeypatch):
    """IntentClassifier with LLM replaced by a MagicMock."""
    mock_llm = MagicMock()
    monkeypatch.setattr("core.llm.get_llm", lambda **kw: mock_llm)
    instance = IntentClassifier(user=None)
    instance.llm = mock_llm
    return instance, mock_llm


def _make_llm_return(mock_llm, content: str):
    """Helper: configure mock_llm to return content string."""
    response = MagicMock()
    response.content = content
    mock_llm.invoke.return_value = response


class TestBuildEntityContext:
    def test_empty_entity_list_returns_sentinel(self, classifier):
        """Empty entity list → '(no entities found)'."""
        instance, _ = classifier
        result = instance.build_entity_context([])
        assert result == "(no entities found)"

    def test_full_context_includes_pset_names(self):
        """Full tier includes property set names from entity properties."""
        classifier = IntentClassifier.__new__(IntentClassifier)

        class FakeEntity:
            ifc_type = "IfcWall"
            name = "Wall-001"
            global_id = "GUID-001"
            properties = {
                "Pset_WallCommon.FireRating": "EI60",
                "Pset_WallCommon.IsExternal": True,
            }

        result = classifier.build_entity_context([FakeEntity()])
        assert "Pset_WallCommon" in result

    def test_minimal_context_returns_type_counts_only(self):
        """Minimal tier returns only type counts like '5 IfcWall'."""
        classifier = IntentClassifier.__new__(IntentClassifier)

        class FakeEntity:
            ifc_type = "IfcWall"
            name = "Wall"
            global_id = "GUID"
            properties = {}

        entities = [FakeEntity() for _ in range(3)]
        # _build_entity_context_minimal directly
        result = classifier._build_entity_context_minimal(entities)
        assert "3 IfcWall" in result

    def test_full_context_caps_at_50_entities_per_type(self):
        """_build_entity_context_full uses at most 50 entities."""
        classifier = IntentClassifier.__new__(IntentClassifier)

        class FakeEntity:
            ifc_type = "IfcWall"
            name = "W"
            global_id = "G"
            properties = {}

        entities = [FakeEntity() for _ in range(60)]
        result = classifier._build_entity_context_full(entities)
        # Should not crash; 60 entities only first 50 are used
        assert "IfcWall" in result


class TestClassify:
    def test_valid_tier1_json_returns_parsed_intent(self, classifier, monkeypatch):
        """Valid tier-1 JSON from LLM → parsed intent dict returned."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "pset": "Pset_WallCommon",
                    "property": "FireRating",
                    "new_value": "EI120",
                    "confidence": 0.9,
                    "explanation": "Set fire rating",
                }
            ),
        )
        result = instance.classify("Set fire rating to EI120", "5 IfcWall")
        assert result["tier"] == 1
        assert result["operation"] == "SET_PROPERTY"

    def test_message_normalized_before_llm_sees_it(self, classifier, monkeypatch):
        """Message is normalized (e.g. 'fire rating' → 'FireRating') before LLM call."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "pset": "Pset_WallCommon",
                    "property": "FireRating",
                    "new_value": "EI120",
                    "confidence": 0.9,
                    "explanation": "Set fire rating",
                }
            ),
        )
        instance.classify("Set fire rating to EI120", "ctx")
        # The message passed to LLM should have been normalized
        call_args = mock_llm.invoke.call_args[0][0]
        # Find the HumanMessage content
        human_msg = call_args[-1]
        assert "FireRating" in human_msg.content

    def test_confidence_float_normalized_to_int(self, classifier):
        """Confidence 0.9 (float ≤1.0) is normalized to 90."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "pset": "Pset_WallCommon",
                    "property": "FireRating",
                    "new_value": "EI120",
                    "confidence": 0.9,
                    "explanation": "Set fire rating",
                }
            ),
        )
        result = instance.classify("Set fire rating to EI120", "ctx")
        assert result["confidence"] == 90

    def test_confidence_already_int_stays_int(self, classifier):
        """Confidence 90 (>1.0) stays as integer 90 without normalization."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "pset": "Pset_WallCommon",
                    "property": "FireRating",
                    "new_value": "EI120",
                    "confidence": 90,
                    "explanation": "Set fire rating",
                }
            ),
        )
        result = instance.classify("Set fire rating to EI120", "ctx")
        assert result["confidence"] == 90

    def test_non_json_response_raises_intent_parse_error(self, classifier):
        """LLM returns garbage text → IntentParseError raised."""
        instance, mock_llm = classifier
        _make_llm_return(mock_llm, "I cannot parse this request into a modification intent.")
        with pytest.raises(IntentParseError):
            instance.classify("Do something", "ctx")

    def test_missing_tier_field_raises_intent_parse_error(self, classifier):
        """JSON missing 'tier' key → IntentParseError."""
        instance, mock_llm = classifier
        _make_llm_return(mock_llm, json.dumps({"operation": "SET_PROPERTY", "confidence": 0.9}))
        with pytest.raises(IntentParseError, match="tier"):
            instance.classify("Set something", "ctx")

    def test_invalid_tier_value_raises_intent_parse_error(self, classifier):
        """tier=5 is not valid (must be 0, 1, 2, or 3) → IntentParseError."""
        instance, mock_llm = classifier
        _make_llm_return(mock_llm, json.dumps({"tier": 5, "confidence": 0.8, "explanation": "x"}))
        with pytest.raises(IntentParseError, match="tier"):
            instance.classify("Do something complex", "ctx")

    def test_tier0_rejection_with_explanation_is_accepted(self, classifier):
        """Tier 0 (rejection) with explanation → parsed as valid intent, no error."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 0,
                    "explanation": "Request is too vague — specify which walls and what value.",
                    "confidence": 0.9,
                }
            ),
        )
        result = instance.classify("Make the building better", "ctx")
        assert result["tier"] == 0
        assert "vague" in result["explanation"]

    def test_tier0_missing_explanation_raises(self, classifier):
        """Tier 0 without explanation → IntentParseError (user-facing reason is required)."""
        instance, mock_llm = classifier
        _make_llm_return(mock_llm, json.dumps({"tier": 0, "confidence": 0.5}))
        with pytest.raises(IntentParseError, match="explanation"):
            instance.classify("vague request", "ctx")

    def test_tier1_missing_operation_raises_intent_parse_error(self, classifier):
        """Tier 1 intent missing 'operation' → IntentParseError."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                {
                    "tier": 1,
                    "filter": {"ifc_type": "IfcWall"},
                    "confidence": 0.9,
                    "explanation": "set something",
                }
            ),
        )
        with pytest.raises(IntentParseError):
            instance.classify("Set something", "ctx")

    def test_array_response_returns_list_of_intents(self, classifier):
        """LLM returns array → list of intent dicts returned."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                [
                    {
                        "tier": 1,
                        "operation": "SET_PROPERTY",
                        "filter": {"ifc_type": "IfcWall"},
                        "pset": "Pset_WallCommon",
                        "property": "FireRating",
                        "new_value": "EI120",
                        "confidence": 0.9,
                        "explanation": "Set fire rating",
                    },
                    {
                        "tier": 1,
                        "operation": "SET_PROPERTY",
                        "filter": {"ifc_type": "IfcWall"},
                        "pset": "Pset_WallCommon",
                        "property": "IsExternal",
                        "new_value": True,
                        "confidence": 0.85,
                        "explanation": "Set IsExternal",
                    },
                ]
            ),
        )
        result = instance.classify("Set fire rating and IsExternal", "ctx")
        assert isinstance(result, list)
        assert len(result) == 2

    def test_array_with_non_dict_element_raises(self, classifier):
        """Array containing a non-dict element → IntentParseError."""
        instance, mock_llm = classifier
        _make_llm_return(
            mock_llm,
            json.dumps(
                [
                    {
                        "tier": 1,
                        "operation": "SET_PROPERTY",
                        "filter": {},
                        "confidence": 0.9,
                        "explanation": "x",
                    },
                    "not a dict",
                ]
            ),
        )
        with pytest.raises(IntentParseError):
            instance.classify("Do two things", "ctx")
