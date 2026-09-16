# metacastor/tests/test_failure_classifier.py
"""The deterministic taxonomy classifies the V3 pipeline's errors without the LLM fallback."""

from unittest.mock import patch

import pytest

from metacastor.services.failure_classifier import classify_error
from writeback.services.errors import ModelUnavailableError, ModificationError, NoChangeError


@pytest.fixture(autouse=True)
def no_llm_fallback():
    """A pattern miss would call a model; every case below must be a pattern hit."""
    with patch(
        "metacastor.services.failure_classifier._llm_classify_fallback",
        side_effect=AssertionError("LLM fallback reached"),
    ):
        yield


@pytest.mark.parametrize(
    ("exc", "error_type", "category"),
    [
        (
            ModelUnavailableError("The Modify model did not answer within 240 s."),
            "LLM_TIMEOUT",
            "RETRYABLE",
        ),
        (
            ModelUnavailableError("The Modify model could not be reached: ConnectionError"),
            "LLM_UNREACHABLE",
            "RETRYABLE",
        ),
        (ModificationError("Declined: this changes geometry"), "REQUEST_REJECTED", "RETRYABLE"),
        (
            ModificationError(
                "Could not produce a valid change after 3 attempts. Last error: select(model) selected no entities."
            ),
            "TARGET_NOT_FOUND",
            "RETRYABLE",
        ),
        (ModificationError("file changed, please re-propose"), "STALE_PROPOSAL", "RETRYABLE"),
        (NoChangeError("Already so"), "NO_CHANGE", "NON_RETRYABLE"),
    ],
)
def test_pipeline_errors_classify_by_pattern(exc, error_type, category):
    got_type, got_category, diagnosis = classify_error(exc, "GENERATE")
    assert (got_type, got_category) == (error_type, category)
    assert diagnosis
