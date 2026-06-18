# scheduling/services/pct_normalize.py
"""Normalize schedule import progress values to the Task model 0–1 range."""

from __future__ import annotations


def normalize_pct_complete(value: str | int | float | None) -> float | None:
    """Convert P6/MSP/CSV percent-complete input to Task model storage (0–1).

    Rules:
      - None / blank / non-numeric → None (no progress data)
      - Negative → 0.0
      - > 100 → 1.0
      - (1.0, 100.0] → divide by 100 (0–100 scale)
      - [0.0, 1.0] → kept as-is (already normalized; idempotent)
      - Exactly 1 / 1.0 → 1.0 (100% complete, not 1%)
    """
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip().rstrip("%").strip().replace(",", "")
        if not cleaned:
            return None
        try:
            numeric = float(cleaned)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        numeric = float(value)
    else:
        return None

    if numeric < 0:
        return 0.0
    if numeric > 100:
        return 1.0
    if numeric > 1.0:
        numeric = numeric / 100.0
    return round(max(0.0, min(1.0, numeric)), 4)
