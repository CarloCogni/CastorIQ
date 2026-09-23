# castor/scheduling/services/evm_forecast.py
"""Shared SPI forecast guards and EVM performance-mode labels.

Single source for the SPI-adjusted finish formula used by compute_evm(),
decision_layer, and report_generator.  No client-side SPI floor — EV and
sane-horizon guards suppress unrealistic extrapolations instead.
"""

from __future__ import annotations

from datetime import date, timedelta


def forecast_finish_date(spi: float, start: date, baseline: date, as_of: date) -> date:
    """SPI-adjusted forecast completion date (unguarded — apply guards in compute_spi_forecast)."""
    elapsed = (as_of - start).days
    if as_of < baseline:
        remaining = max((baseline - as_of).days, 0)
        forecast_days = elapsed + remaining / spi
    else:
        forecast_days = elapsed / spi
    return start + timedelta(days=max(0, round(forecast_days)))


def _parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def compute_spi_forecast(
    *,
    spi: float,
    ev: float,
    project_start: str | date,
    project_end: str | date,
    as_of: str | date,
) -> dict:
    """Return guarded SPI forecast payload for API and report consumers.

    Returns:
        date           — ISO date string or None when suppressed
        suppressed     — bool
        reason         — str | None human-readable suppression message
        variance_days  — int | None (baseline − forecast; positive = early)
    """
    start = _parse_date(project_start)
    baseline = _parse_date(project_end)
    today = _parse_date(as_of)
    sane_horizon = baseline + timedelta(days=(baseline - start).days * 3)

    if ev <= 0:
        return {
            "date": None,
            "suppressed": True,
            "reason": "Insufficient progress to forecast a finish date — no earned value recorded.",
            "variance_days": None,
        }

    if spi <= 0:
        return {
            "date": None,
            "suppressed": True,
            "reason": f"Progress too low to forecast a reliable finish date (SPI {spi:.2f}).",
            "variance_days": None,
        }

    candidate = forecast_finish_date(spi, start, baseline, today)
    if candidate > sane_horizon:
        return {
            "date": None,
            "suppressed": True,
            "reason": f"Progress too low to forecast a reliable finish date (SPI {spi:.2f}).",
            "variance_days": None,
        }

    return {
        "date": candidate.isoformat(),
        "suppressed": False,
        "reason": None,
        "variance_days": (baseline - candidate).days,
    }


def performance_mode_fields(use_cost: bool, cost_basis: str) -> dict:
    """Derive performance-mode flags from existing EVM cost basis (no new inputs)."""
    if use_cost:
        labels = {
            "schedule costs": "Monetary EVM — BAC weighted by schedule task costs.",
            "QTO estimates": "Monetary EVM — BAC weighted by QTO quantity estimates.",
            "schedule costs + QTO estimates": (
                "Monetary EVM — schedule costs supplemented with QTO estimates for uncosted tasks."
            ),
        }
        return {
            "is_monetary_evm": True,
            "performance_mode": "cost_evm",
            "performance_mode_label": labels.get(
                cost_basis, "Monetary EVM — BAC weighted by task costs."
            ),
        }

    return {
        "is_monetary_evm": False,
        "performance_mode": "schedule_performance",
        "performance_mode_label": (
            "Schedule performance mode — BAC weighted by task durations; "
            "SPI reflects progress vs plan, not monetary EVM."
        ),
    }
