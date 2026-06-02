# islam/scheduling/services/trend_engine.py
"""Trend Analysis Engine — SPI/CPI historical trends, TCPI, Schedule Recovery.

Operates on the weekly S-curve series produced by compute_evm().  All maths
is pure Python; no additional DB queries when evm_data is provided by the
caller (e.g. controls_chat passes it in to avoid a second round-trip).

Public entry point:
    compute_trend_analysis(project_id, evm_data=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_STABLE_THRESHOLD = 0.003  # SPI change/week below which trend is "stable"
_REGRESSION_WINDOW = 12  # max weeks of history used for regression
_CPI_TROUBLE_THRESHOLD = 0.90
_TCPI_RED = 1.10
_TCPI_AMBER = 1.00


# ── Math helpers ──────────────────────────────────────────────────────────────


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least-squares linear fit.  Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    return slope, (sy - slope * sx) / n


def _direction(slope: float) -> str:
    if slope > _STABLE_THRESHOLD:
        return "improving"
    if slope < -_STABLE_THRESHOLD:
        return "declining"
    return "stable"


def _consecutive_decline(values: list[float]) -> int:
    """Weeks of consecutive decline from the most recent point."""
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] < values[i - 1]:
            count += 1
        else:
            break
    return count


def _consecutive_improve(values: list[float]) -> int:
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] > values[i - 1]:
            count += 1
        else:
            break
    return count


# ── Series extraction ─────────────────────────────────────────────────────────


def _build_performance_series(evm_data: dict) -> tuple[list[dict], list[dict]]:
    """Derive SPI and CPI weekly series from EVM S-curve data.

    Only weeks where PV > 0.5 % of BAC are included to avoid division noise
    at project start.  Returns at most the last _REGRESSION_WINDOW points.

    Returns:
        spi_series  — [{date, spi, pv_pct, ev_pct}]
        cpi_series  — [{date, cpi, ev_pct, ac_pct}]
    """
    pv_by_date = {p["date"]: p["pct"] for p in evm_data["series"]["pv"]}
    ac_by_date = {p["date"]: p["pct"] for p in evm_data["series"]["ac"]}

    spi_series: list[dict] = []
    cpi_series: list[dict] = []

    for pt in evm_data["series"]["ev"]:
        d = pt["date"]
        ev_pct = pt["pct"]
        pv_pct = pv_by_date.get(d, 0.0)
        ac_pct = ac_by_date.get(d, 0.0)

        if pv_pct > 0.5:
            spi_series.append(
                {
                    "date": d,
                    "spi": round(ev_pct / pv_pct, 4),
                    "pv_pct": pv_pct,
                    "ev_pct": ev_pct,
                }
            )

        if ac_pct > 0.5:
            cpi_series.append(
                {
                    "date": d,
                    "cpi": round(ev_pct / ac_pct, 4),
                    "ev_pct": ev_pct,
                    "ac_pct": ac_pct,
                }
            )

    return spi_series[-_REGRESSION_WINDOW:], cpi_series[-_REGRESSION_WINDOW:]


# ── Series analysis ───────────────────────────────────────────────────────────


def _analyse_series(series: list[dict], key: str) -> dict:
    """Regression + trend metadata for a weekly series.

    Args:
        series: list of {date, <key>, ...}
        key:    "spi" or "cpi"
    """
    if len(series) < 3:
        return {"has_trend": False, "reason": "insufficient_data"}

    values = [pt[key] for pt in series]
    n = len(values)
    slope, intercept = _linreg(list(range(n)), values)
    current = values[-1]
    direction = _direction(slope)

    def forecast(weeks_ahead: float) -> float:
        raw = intercept + slope * ((n - 1) + weeks_ahead)
        return round(max(0.0, min(3.0, raw)), 3)

    return {
        "has_trend": True,
        "current": round(current, 3),
        "slope": round(slope, 5),
        "direction": direction,
        "consecutive_decline": _consecutive_decline(values),
        "consecutive_improve": _consecutive_improve(values),
        "forecast_4w": forecast(4),
        "forecast_8w": forecast(8),
        "forecast_12w": forecast(12),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


# ── TCPI ──────────────────────────────────────────────────────────────────────


def _compute_tcpi(evm_data: dict) -> dict:
    """To-Complete Performance Index = (BAC − EV) / (BAC − AC).

    TCPI > 1.10 → budget recovery extremely unlikely.
    TCPI > CPI  → current efficiency insufficient to meet budget.
    """
    bac = evm_data["bac"]
    ev = evm_data["ev"]
    ac = evm_data["ac"]
    cpi = evm_data["cpi"]

    budget_remaining = bac - ac
    if budget_remaining <= 0:
        return {
            "value": None,
            "status": "red",
            "verdict": "Budget exhausted — AC has reached or exceeded BAC.",
        }

    tcpi = round((bac - ev) / budget_remaining, 3)

    if tcpi > _TCPI_RED:
        gap_pct = round((tcpi - cpi) / cpi * 100, 0) if cpi > 0 else None
        gap_str = f" ({gap_pct:.0f}% above current CPI)" if gap_pct else ""
        return {
            "value": tcpi,
            "status": "red",
            "verdict": (
                f"TCPI {tcpi:.2f} vs CPI {cpi:.2f}{gap_str} — extremely unlikely to recover budget."
            ),
        }

    if tcpi > _TCPI_AMBER:
        return {
            "value": tcpi,
            "status": "amber",
            "verdict": (
                f"TCPI {tcpi:.2f} vs CPI {cpi:.2f} — "
                "current cost efficiency is insufficient to meet budget."
            ),
        }

    return {
        "value": tcpi,
        "status": "green",
        "verdict": (f"TCPI {tcpi:.2f} vs CPI {cpi:.2f} — within reach of current performance."),
    }


# ── Schedule Recovery Index ───────────────────────────────────────────────────


def _compute_recovery(evm_data: dict, spi_trend: dict) -> dict:
    """Schedule Recovery Index — required SPI for remaining work vs current trend.

    TSPI (Time Schedule Performance Index) = (BAC − EV) / (BAC − PV).
    Analogous to TCPI but for earned value vs planned value.
    """
    bac = evm_data["bac"]
    ev = evm_data["ev"]
    pv = evm_data["pv"]
    current_spi = evm_data["spi"]

    remaining_to_earn = bac - ev
    remaining_planned = bac - pv

    if remaining_to_earn <= 0:
        return {
            "tspi": 0.0,
            "current_spi": current_spi,
            "gap": 0.0,
            "weeks_remaining": 0,
            "weekly_improvement_needed": 0.0,
            "achievable": True,
            "verdict": "Project complete — no schedule recovery needed.",
        }

    if remaining_planned <= 0:
        return {
            "tspi": None,
            "current_spi": current_spi,
            "gap": None,
            "weeks_remaining": 0,
            "weekly_improvement_needed": None,
            "achievable": False,
            "verdict": (
                "Project is past its planned completion date. "
                "Recovery requires replanning the baseline."
            ),
        }

    tspi = round(remaining_to_earn / remaining_planned, 3)
    project_end = date.fromisoformat(evm_data["project_end"])
    as_of = date.fromisoformat(evm_data["as_of"])
    weeks_remaining = max((project_end - as_of).days / 7.0, 0.0)

    gap = round(tspi - current_spi, 3)
    weekly_improvement = round(gap / weeks_remaining, 4) if weeks_remaining > 0.5 else None

    # Achievability: compare required weekly improvement to historical SPI velocity
    historical_slope = spi_trend.get("slope", 0.0) if spi_trend.get("has_trend") else 0.0

    if tspi <= 1.0:
        return {
            "tspi": tspi,
            "current_spi": current_spi,
            "gap": gap,
            "weeks_remaining": round(weeks_remaining, 1),
            "weekly_improvement_needed": weekly_improvement,
            "achievable": True,
            "verdict": (
                f"No acceleration needed — TSPI {tspi:.2f} is ≤ 1.00. "
                f"Maintain current SPI of {current_spi:.2f}."
            ),
        }

    if gap <= 0:
        return {
            "tspi": tspi,
            "current_spi": current_spi,
            "gap": gap,
            "weeks_remaining": round(weeks_remaining, 1),
            "weekly_improvement_needed": weekly_improvement,
            "achievable": True,
            "verdict": (f"Current SPI {current_spi:.2f} already meets required TSPI {tspi:.2f}."),
        }

    # Need improvement — assess against historical trend velocity
    if (
        weekly_improvement is not None
        and weekly_improvement <= max(abs(historical_slope), 0.001) * 2
    ):
        achievable = True
        verdict = (
            f"Recovery challenging but feasible — SPI must improve "
            f"+{weekly_improvement:.3f}/week over {weeks_remaining:.0f} weeks "
            f"to reach TSPI target {tspi:.2f}."
        )
    else:
        achievable = False
        trend_note = (
            f" Current trend is {historical_slope:+.3f}/week." if spi_trend.get("has_trend") else ""
        )
        if weekly_improvement is not None:
            verdict = (
                f"Recovery at risk — requires +{weekly_improvement:.3f}/week SPI improvement "
                f"over {weeks_remaining:.0f} weeks (TSPI target {tspi:.2f}).{trend_note}"
            )
        else:
            verdict = f"Recovery requires TSPI {tspi:.2f} but insufficient time remains."

    return {
        "tspi": tspi,
        "current_spi": current_spi,
        "gap": gap,
        "weeks_remaining": round(weeks_remaining, 1),
        "weekly_improvement_needed": weekly_improvement,
        "achievable": achievable,
        "verdict": verdict,
    }


# ── Chat summary ──────────────────────────────────────────────────────────────


def _build_summary_lines(
    spi_trend: dict,
    cpi_trend: dict,
    tcpi: dict,
    recovery: dict,
) -> list[str]:
    """Concise bullet lines for the Project Controls Chat system prompt."""
    lines: list[str] = []

    if spi_trend.get("has_trend"):
        t = spi_trend
        decl = t["consecutive_decline"]
        impr = t["consecutive_improve"]
        if t["direction"] == "declining" and decl >= 3:
            lines.append(
                f"SPI declining for {decl} consecutive weeks "
                f"(now {t['current']:.3f}, 4-week forecast {t['forecast_4w']:.3f}) — deteriorating trend"
            )
        elif t["direction"] == "declining":
            lines.append(
                f"SPI declining (slope {t['slope']:+.4f}/week), currently {t['current']:.3f}, "
                f"forecast {t['forecast_4w']:.3f} in 4 weeks"
            )
        elif t["direction"] == "improving" and impr >= 2:
            lines.append(
                f"SPI recovering for {impr} consecutive weeks "
                f"(now {t['current']:.3f}, 4-week forecast {t['forecast_4w']:.3f})"
            )
        elif t["direction"] == "improving":
            lines.append(
                f"SPI improving (slope {t['slope']:+.4f}/week), currently {t['current']:.3f}"
            )
        else:
            lines.append(f"SPI stable at ~{t['current']:.3f} (slope ≈ 0)")

    if cpi_trend.get("has_trend"):
        t = cpi_trend
        if t["current"] < _CPI_TROUBLE_THRESHOLD:
            lines.append(
                f"CPI {t['current']:.3f} below 0.90 industry threshold — "
                f"cost performance is in trouble ({t['direction']})"
            )
        elif t["direction"] != "stable":
            lines.append(f"CPI {t['current']:.3f} ({t['direction']}, slope {t['slope']:+.4f}/week)")

    lines.append(tcpi["verdict"])
    lines.append(recovery["verdict"])

    return lines


# ── Public entry point ────────────────────────────────────────────────────────


def compute_trend_analysis(project_id: str, evm_data: dict | None = None) -> dict:
    """Return trend analysis for *project_id*.

    Args:
        project_id: UUID string of the project.
        evm_data:   Pre-computed dict from compute_evm().  Pass it in when
                    calling from controls_chat to avoid a second DB round-trip.

    Returns:
        has_data          — bool
        weeks_of_data     — int
        spi_series        — [{date, spi, pv_pct, ev_pct}]  last 12 weeks
        cpi_series        — [{date, cpi, ev_pct, ac_pct}]  last 12 weeks
        spi_trend         — regression + forecast dict
        cpi_trend         — regression + forecast dict
        tcpi              — {value, status, verdict}
        recovery          — {tspi, gap, weeks_remaining, achievable, verdict, …}
        summary_lines     — [str]  concise bullets for chat context
    """
    if evm_data is None:
        from .evm import compute_evm

        evm_data = compute_evm(project_id)

    if not evm_data.get("has_data"):
        return {"has_data": False}

    if len(evm_data["series"]["ev"]) < 2:
        return {"has_data": False, "reason": "insufficient_history"}

    spi_series, cpi_series = _build_performance_series(evm_data)

    spi_trend = _analyse_series(spi_series, "spi")
    cpi_trend = _analyse_series(cpi_series, "cpi")

    tcpi = _compute_tcpi(evm_data)
    recovery = _compute_recovery(evm_data, spi_trend)
    summary_lines = _build_summary_lines(spi_trend, cpi_trend, tcpi, recovery)

    logger.info(
        "Trend analysis — project %s: SPI %s slope=%.4f TCPI=%s recoverable=%s",
        project_id,
        spi_trend.get("direction", "n/a"),
        spi_trend.get("slope", 0.0),
        tcpi.get("value"),
        recovery.get("achievable"),
    )

    return {
        "has_data": True,
        "weeks_of_data": len(spi_series),
        "spi_series": spi_series,
        "cpi_series": cpi_series,
        "spi_trend": spi_trend,
        "cpi_trend": cpi_trend,
        "tcpi": tcpi,
        "recovery": recovery,
        "summary_lines": summary_lines,
    }
