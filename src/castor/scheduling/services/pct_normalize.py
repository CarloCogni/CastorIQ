# castor/scheduling/services/pct_normalize.py
"""Normalise schedule percent-complete values to the 0–1 range stored on Task."""

from __future__ import annotations


def normalize_pct_complete(value: float | int | None) -> float | None:
    """Normalise a P6/CSV percent-complete value to 0–1 for Task model fields.

    Accepts both common export scales:
      - 0–100 (e.g. 40, 40.0, 100) → 0.40, 1.0
      - 0–1   (e.g. 0.40, 1.0)     → unchanged (capped at 1.0)

    Negative inputs clamp to 0.0. Values above 100 cap at 1.0.
    None and non-numeric inputs return None.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return 0.0
    if v > 100:
        return 1.0
    if v > 1.0:
        v = v / 100.0
    return round(max(0.0, min(1.0, v)), 4)
