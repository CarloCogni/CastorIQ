# metacastor/tests/test_rejection_classification.py
"""A router rejection must reach the user in its own words.

Rejections are decisions, not crashes: the pipeline composes a reason plus a
grounded hint specifically to tell the user what to do. They used to be
raised as bare ValueErrors, which the classifier's catch-all pattern
(`("ValueError", "", "FILTER_INVALID")`) turned into "The filter
specification was invalid: … Ensure the filter uses valid IFC entity types
and property names." — advice about a filter the user never wrote.

`RequestRejectedError` declares its own taxonomy label so no pattern
matching happens at all.
"""

from unittest.mock import patch

import pytest

from metacastor.services.failure_classifier import CATEGORY_MAP, classify_error
from writeback.services.tier_router import (
    REJECT_CATEGORY_DESTINATION_NOT_FOUND,
    REJECT_CATEGORY_ENTITY_NOT_FOUND,
    REJECT_CATEGORY_INTERNAL,
    REJECT_CATEGORY_OUT_OF_SCOPE,
    REJECT_CATEGORY_PSET_UNKNOWN,
    REJECT_CATEGORY_UNCLEAR,
    REJECTION_ERROR_TYPES,
    RequestRejectedError,
)


def _classify(exc, phase="VALIDATION"):
    """Classify while proving the LLM fallback was never reached."""
    with patch("metacastor.services.failure_classifier._llm_classify_fallback") as fallback:
        result = classify_error(exc, phase)
        fallback.assert_not_called()
    return result


def test_destination_rejection_reaches_the_user_verbatim():
    """The reported bug, end to end through the classifier."""
    message = (
        "Could not find 'Level 2' in this project to move into. "
        "This model's storeys are: `00 groundfloor`."
    )
    error_type, category, diagnosis = _classify(
        RequestRejectedError(message, category=REJECT_CATEGORY_DESTINATION_NOT_FOUND)
    )

    assert error_type == "DESTINATION_NOT_FOUND"
    assert category == "NON_RETRYABLE"
    assert diagnosis == message
    # The old wrapper must be gone.
    assert "filter" not in diagnosis.lower()


@pytest.mark.parametrize(
    "reject_category,expected_error_type",
    [
        (REJECT_CATEGORY_UNCLEAR, "REQUEST_UNCLEAR"),
        (REJECT_CATEGORY_OUT_OF_SCOPE, "REQUEST_OUT_OF_SCOPE"),
        (REJECT_CATEGORY_ENTITY_NOT_FOUND, "TARGET_NOT_FOUND"),
        (REJECT_CATEGORY_DESTINATION_NOT_FOUND, "DESTINATION_NOT_FOUND"),
        (REJECT_CATEGORY_PSET_UNKNOWN, "PROPERTY_NOT_IN_REGISTRY"),
        (REJECT_CATEGORY_INTERNAL, "REQUEST_REJECTED"),
    ],
)
def test_every_rejection_category_passes_its_message_through(reject_category, expected_error_type):
    error_type, _category, diagnosis = _classify(
        RequestRejectedError("Say this exactly.", category=reject_category)
    )
    assert error_type == expected_error_type
    assert diagnosis == "Say this exactly."


def test_every_rejection_error_type_is_in_the_category_map():
    """An unmapped error type silently defaults; assert the map is complete."""
    for error_type in REJECTION_ERROR_TYPES.values():
        assert error_type in CATEGORY_MAP


def test_unknown_category_still_classifies_as_a_rejection():
    exc = RequestRejectedError("Nope.", category="a_category_nobody_declared")
    error_type, _category, diagnosis = _classify(exc)
    assert error_type == "REQUEST_REJECTED"
    assert diagnosis == "Nope."


def test_a_plain_value_error_still_pattern_matches():
    """The declared-label shortcut must not disturb ordinary exceptions."""
    error_type, _category, _diagnosis = _classify(ValueError("Filter matched 0 entities"))
    assert error_type == "FILTER_NO_MATCH"


def test_rejection_is_still_a_value_error():
    """Existing broad handlers catch ValueError; subclassing keeps them working."""
    assert isinstance(RequestRejectedError("x"), ValueError)
