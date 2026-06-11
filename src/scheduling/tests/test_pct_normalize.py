# scheduling/tests/test_pct_normalize.py
"""Unit tests for shared import progress normalization."""

from __future__ import annotations

import pytest

from scheduling.services.pct_normalize import normalize_pct_complete


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("abc", None),
        (40, 0.4),
        ("40", 0.4),
        ("40%", 0.4),
        (0.4, 0.4),
        ("0.4", 0.4),
        (100, 1.0),
        ("100%", 1.0),
        (1, 1.0),
        ("1", 1.0),
        ("1.0", 1.0),
        (0, 0.0),
        ("0", 0.0),
        (-5, 0.0),
        ("-5", 0.0),
        (101, 1.0),
        ("101", 1.0),
        (0.6035, 0.6035),
        (" 50 ", 0.5),
        ("50.0%", 0.5),
    ],
)
def test_normalize_pct_complete(raw, expected):
    result = normalize_pct_complete(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=1e-4)


def test_normalize_pct_complete_is_idempotent():
    assert normalize_pct_complete(normalize_pct_complete(0.4)) == pytest.approx(0.4, abs=1e-4)
