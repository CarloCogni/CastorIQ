# writeback/tests/test_feasibility_checker.py
"""Unit tests for FeasibilityChecker.

All LLM calls are mocked — these tests cover the checker's own logic:
fail-open behaviour, JSON parsing, and correct result mapping.
"""

import json
from unittest.mock import MagicMock, patch

from writeback.services.feasibility_checker import FeasibilityChecker, FeasibilityResult


class TestFeasibilityCheckerCheck:
    """Unit tests for FeasibilityChecker.check()."""

    def _make_llm_response(self, feasible: bool, reason: str) -> MagicMock:
        response = MagicMock()
        response.content = json.dumps({"feasible": feasible, "reason": reason})
        return response

    def test_empty_message_returns_not_feasible_without_llm(self):
        """Empty string is rejected immediately, no LLM call made."""
        with patch("writeback.services.feasibility_checker.get_llm") as mock_get_llm:
            result = FeasibilityChecker().check("")

        assert result.feasible is False
        assert "empty" in result.reason.lower()
        mock_get_llm.assert_not_called()

    def test_whitespace_only_message_returns_not_feasible(self):
        """Whitespace-only string is rejected without LLM call."""
        with patch("writeback.services.feasibility_checker.get_llm") as mock_get_llm:
            result = FeasibilityChecker().check("   ")

        assert result.feasible is False
        mock_get_llm.assert_not_called()

    def test_llm_returns_feasible_true(self):
        """LLM returning feasible=true propagates correctly."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._make_llm_response(
            feasible=True, reason="Target, property and value all specified."
        )

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("Set fire rating of all walls to EI120")

        assert result.feasible is True
        assert result.reason == "Target, property and value all specified."

    def test_llm_returns_feasible_false(self):
        """LLM returning feasible=false propagates correctly."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._make_llm_response(
            feasible=False, reason="No target entity type or property specified."
        )

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("change everything")

        assert result.feasible is False
        assert result.reason == "No target entity type or property specified."

    def test_llm_error_fails_open(self):
        """Any LLM exception returns feasible=True (fail-open — don't block valid requests)."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("Ollama unavailable")

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("Set fire rating of all walls to EI120")

        assert result.feasible is True
        assert result.reason == ""

    def test_llm_invalid_json_fails_open(self):
        """Non-JSON LLM response returns feasible=True (fail-open)."""
        mock_llm = MagicMock()
        bad_response = MagicMock()
        bad_response.content = "Sorry, I cannot help with that."
        mock_llm.invoke.return_value = bad_response

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("Set fire rating of all walls to EI120")

        assert result.feasible is True

    def test_llm_missing_feasible_key_defaults_to_true(self):
        """JSON response without 'feasible' key defaults to feasible=True."""
        mock_llm = MagicMock()
        response = MagicMock()
        response.content = json.dumps({"reason": "Looks fine"})
        mock_llm.invoke.return_value = response

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("Set fire rating of all walls to EI120")

        assert result.feasible is True

    def test_result_is_feasibility_result_dataclass(self):
        """Return type is always FeasibilityResult."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._make_llm_response(feasible=True, reason="ok")

        with patch("writeback.services.feasibility_checker.get_llm", return_value=mock_llm):
            result = FeasibilityChecker().check("Set fire rating of all walls to EI120")

        assert isinstance(result, FeasibilityResult)
