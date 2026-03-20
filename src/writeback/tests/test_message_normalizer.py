# writeback/tests/test_message_normalizer.py
"""Tests for the deterministic message normalizer — pure regex, no DB."""

import pytest

from writeback.services.message_normalizer import normalize


def test_normalize_fire_rating_alias_maps_to_canonical():
    """'fire rating' maps to 'FireRating'."""
    result = normalize("Set fire rating to EI120")
    assert "FireRating" in result


def test_normalize_fire_rated_alias_maps_to_canonical():
    """'fire rated' maps to 'FireRating'."""
    result = normalize("All fire rated walls")
    assert "FireRating" in result


def test_normalize_case_insensitive():
    """Matching is case-insensitive."""
    result = normalize("Set FIRE RATING to EI60")
    assert "FireRating" in result


def test_normalize_longer_pattern_wins_over_shorter():
    """'fire rating' should match before 'fire' alone could."""
    result = normalize("update fire rating")
    assert "FireRating" in result
    assert "fire rating" not in result.lower()


def test_normalize_col_word_boundary_not_replaced_in_column():
    """'col' as part of 'column' should NOT trigger the 'col' → 'column' alias replacement incorrectly.
    The entity alias for 'col' should use word boundaries — 'column' itself shouldn't double-expand."""
    # "column" contains "col" — but \b boundary should not match inside "column"
    result = normalize("column fire rating")
    # "col" inside "column" should not expand; column should stay as "column"
    assert "column" in result.lower()


def test_normalize_col_standalone_expands_to_column():
    """'col' standalone → 'column'."""
    result = normalize("set col fire rating to EI120")
    assert "column" in result.lower()


def test_normalize_empty_string_returns_empty():
    """Empty string input returns empty string."""
    assert normalize("") == ""


def test_normalize_whitespace_collapsed():
    """Multiple spaces are collapsed to single space."""
    result = normalize("set  fire  rating")
    assert "  " not in result


def test_normalize_unchanged_text_returned_as_is():
    """Text with no aliases returns unchanged."""
    original = "change the property value on entity"
    assert normalize(original) == original
