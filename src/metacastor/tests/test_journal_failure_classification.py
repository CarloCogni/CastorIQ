# metacastor/tests/test_journal_failure_classification.py
"""Journal execution failures must classify deterministically.

Every tier now writes through the journal executor, so `JournalExecutionError`
is the exception the classifier sees for most write failures. Without explicit
patterns it fell through to `_llm_classify_fallback` — an LLM call per failure,
producing an invented label (`JOURNAL_EXECUTION_ERROR`) that is not in
CATEGORY_MAP and could differ between runs.

The `assert_not_called` on the LLM fallback is the point of these tests.
"""

from unittest.mock import patch

import pytest

from ifc_processor.services.journal_executor import JournalExecutionError, JournalStaleError
from metacastor.services.failure_classifier import classify_error


def _classify(exc, phase="EXECUTION"):
    """Classify while proving the LLM fallback was never reached."""
    with patch("metacastor.services.failure_classifier._llm_classify_fallback") as fallback:
        error_type, category, diagnosis = classify_error(exc, phase)
        fallback.assert_not_called()
    return error_type, category, diagnosis


def test_generated_code_failure_is_a_code_execution_error():
    """The real-world case: generated code raised inside the sandbox."""
    exc = JournalExecutionError("Generated code failed: ValueError: Could not find Level 2 storey")

    error_type, category, _ = _classify(exc)

    assert error_type == "CODE_EXECUTION_ERROR"
    assert category == "NON_RETRYABLE"


def test_stale_journal_is_retryable():
    """The file moved under the proposal — re-proposing is exactly the fix,
    so this must offer the user a retry rather than a dead end."""
    exc = JournalStaleError(
        "The IFC file changed after this proposal was created. Please re-propose."
    )

    error_type, category, diagnosis = _classify(exc)

    assert error_type == "STALE_JOURNAL"
    assert category == "RETRYABLE"
    assert "changed" in diagnosis


def test_sandbox_timeout_maps_to_code_timeout():
    exc = JournalExecutionError("Generated code exceeded the 30s budget and was terminated.")

    error_type, category, _ = _classify(exc)

    assert error_type == "CODE_TIMEOUT"
    assert category == "NON_RETRYABLE"


def test_unhandled_op_maps_to_tier3_generic():
    exc = JournalExecutionError("No handler registered for op ASSIGN_RELATIONSHIP (mutation m1).")

    error_type, _, _ = _classify(exc)

    assert error_type == "TIER3_GENERIC"


def test_unknown_journal_error_falls_back_to_a_write_error_not_the_llm():
    """Even an unrecognised journal failure must stay deterministic."""
    exc = JournalExecutionError("something entirely unexpected")

    error_type, category, _ = _classify(exc)

    assert error_type == "IFC_WRITE_GENERIC"
    assert category == "NON_RETRYABLE"


@pytest.mark.parametrize(
    "exc",
    [
        JournalExecutionError("Generated code failed: ValueError: boom"),
        JournalStaleError("changed"),
        JournalExecutionError("No handler registered for op X"),
    ],
)
def test_no_journal_failure_reaches_the_llm_fallback(exc):
    """A failure path that costs an LLM call is a failure path that can fail."""
    with patch("metacastor.services.failure_classifier._llm_classify_fallback") as fallback:
        classify_error(exc, "EXECUTION")
        fallback.assert_not_called()


def test_stale_journal_pattern_precedes_the_generic_journal_pattern():
    """JournalStaleError subclasses JournalExecutionError; if the generic
    pattern were matched first it would swallow the retryable case."""
    from metacastor.services.failure_classifier import EXCEPTION_PATTERNS

    names = [class_substr for class_substr, _, _ in EXCEPTION_PATTERNS]
    assert names.index("JournalStaleError") < names.index("JournalExecutionError")
