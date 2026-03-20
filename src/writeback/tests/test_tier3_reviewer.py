# writeback/tests/test_tier3_reviewer.py
"""Tests for Tier3Reviewer — LLM always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.services.tier3_reviewer import Tier3Reviewer


@pytest.fixture
def reviewer():
    """Tier3Reviewer with mocked LLM."""
    with patch("writeback.services.tier3_reviewer.get_llm", return_value=MagicMock()):
        return Tier3Reviewer(user=None)


def _llm_response(content: str):
    """Build a fake LLM response."""
    mock = MagicMock()
    mock.content = content
    return mock


VALID_REVIEW = {
    "verdict": "ALIGNED",
    "summary": "The code correctly sets the FireRating property on all walls.",
    "steps": [
        {"description": "Finds all IfcWall elements in the model."},
        {"description": "Sets FireRating to EI120 for each wall."},
    ],
    "warnings": [],
}


class TestReview:
    """Tests for Tier3Reviewer.review()."""

    def test_valid_response_returns_review_dict(self, reviewer):
        """Valid LLM response returns parsed review dict."""
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(VALID_REVIEW))

        result = reviewer.review(
            user_message="Set fire rating to EI120 on all walls",
            code="def modify_ifc(model): pass",
            entity_context="IfcWall (5): W-001, W-002",
        )

        assert result["verdict"] == "ALIGNED"
        assert "summary" in result
        assert isinstance(result["steps"], list)
        assert len(result["steps"]) == 2

    def test_invalid_json_returns_fallback(self, reviewer):
        """Invalid JSON from LLM returns fallback dict."""
        reviewer.llm.invoke.return_value = _llm_response("not valid json {{")

        result = reviewer.review("request", "code", "context")

        assert result["verdict"] == "PARTIAL"
        assert "unavailable" in result["summary"].lower()

    def test_invalid_verdict_returns_fallback(self, reviewer):
        """Invalid verdict value triggers fallback."""
        invalid = {**VALID_REVIEW, "verdict": "UNKNOWN_VERDICT"}
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(invalid))

        result = reviewer.review("request", "code", "context")

        assert result["verdict"] == "PARTIAL"

    def test_missing_summary_returns_fallback(self, reviewer):
        """Response missing summary triggers fallback."""
        invalid = {**VALID_REVIEW, "summary": ""}
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(invalid))

        result = reviewer.review("request", "code", "context")
        assert result["verdict"] == "PARTIAL"

    def test_empty_steps_returns_fallback(self, reviewer):
        """Response with empty steps list triggers fallback."""
        invalid = {**VALID_REVIEW, "steps": []}
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(invalid))

        result = reviewer.review("request", "code", "context")
        assert result["verdict"] == "PARTIAL"

    def test_misaligned_verdict_is_preserved(self, reviewer):
        """MISALIGNED verdict is returned as-is when valid."""
        misaligned = {**VALID_REVIEW, "verdict": "MISALIGNED"}
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(misaligned))

        result = reviewer.review("request", "code", "context")
        assert result["verdict"] == "MISALIGNED"

    def test_partial_verdict_with_warnings(self, reviewer):
        """PARTIAL verdict with warnings is returned correctly."""
        partial = {
            "verdict": "PARTIAL",
            "summary": "Addresses request but with caveats.",
            "steps": [{"description": "Does something partial."}],
            "warnings": ["Might affect more entities than expected."],
        }
        reviewer.llm.invoke.return_value = _llm_response(json.dumps(partial))

        result = reviewer.review("request", "code", "context")
        assert result["verdict"] == "PARTIAL"
        assert len(result["warnings"]) == 1


class TestIsValid:
    """Tests for Tier3Reviewer._is_valid() static method."""

    def test_valid_result_returns_true(self):
        """Completely valid review dict returns True."""
        assert Tier3Reviewer._is_valid(VALID_REVIEW) is True

    def test_non_dict_returns_false(self):
        """Non-dict input returns False."""
        assert Tier3Reviewer._is_valid("string") is False
        assert Tier3Reviewer._is_valid(None) is False
        assert Tier3Reviewer._is_valid([]) is False

    def test_invalid_verdict_returns_false(self):
        """Unrecognized verdict value returns False."""
        assert Tier3Reviewer._is_valid({**VALID_REVIEW, "verdict": "BAD"}) is False

    def test_empty_summary_returns_false(self):
        """Empty summary returns False."""
        assert Tier3Reviewer._is_valid({**VALID_REVIEW, "summary": ""}) is False

    def test_none_steps_returns_false(self):
        """None steps returns False."""
        assert Tier3Reviewer._is_valid({**VALID_REVIEW, "steps": None}) is False

    def test_empty_steps_returns_false(self):
        """Empty steps list returns False."""
        assert Tier3Reviewer._is_valid({**VALID_REVIEW, "steps": []}) is False


class TestFallback:
    """Tests for Tier3Reviewer._fallback() static method."""

    def test_fallback_returns_partial_verdict(self):
        """Fallback always returns PARTIAL verdict."""
        result = Tier3Reviewer._fallback()
        assert result["verdict"] == "PARTIAL"
        assert "summary" in result
        assert isinstance(result["steps"], list)
        assert isinstance(result["warnings"], list)
        assert len(result["warnings"]) > 0
