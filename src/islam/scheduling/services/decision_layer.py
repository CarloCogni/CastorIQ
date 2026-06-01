# islam/scheduling/services/decision_layer.py
"""Decision Layer — executive plain-language summary from existing service data.

Assembles three blocks:
  Block 1  Where is the project headed?   (EVM + delay root-cause + Monte Carlo)
  Block 2  Is the schedule built correctly?  (DCMA + anomaly detection)
  Block 3  What needs attention today?    (delay root-causes + ML watchlist, ranked)

STRICT: every figure traces to a real query from an existing service.
        No invented numbers. Missing sources produce "n/a", never a guess.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _months_diff(later: date, earlier: date) -> float:
    """Signed months between two dates. Positive = later is after earlier."""
    return (later - earlier).days / 30.44


def _forecast_finish(spi: float, start: date, baseline: date, as_of: date) -> date:
    """SPI-adjusted forecast completion date (mirrors the JS formula in evm.html)."""
    spi = max(float(spi), 0.05)
    elapsed = (as_of - start).days
    if as_of < baseline:
        remaining = max((baseline - as_of).days, 0)
        forecast_days = elapsed + remaining / spi
    else:
        forecast_days = elapsed / spi
    return start + timedelta(days=max(0, round(forecast_days)))


def _fmt_month(d: date) -> str:
    """'Jan 2027' — readable month label."""
    return d.strftime("%b %Y")


def _severity(spi: float) -> str:
    if spi >= 0.95:
        return "green"
    if spi >= 0.80:
        return "amber"
    return "red"


def _dcma_severity(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "amber"
    return "red"


# ── Block builders ────────────────────────────────────────────────────────────


def _build_block1(evm: dict, drc: dict, mc: dict) -> dict:
    """Performance block: where is the project headed?

    Sources: compute_evm(), run_delay_rootcause(), compute_monte_carlo().
    """
    if not evm.get("has_data"):
        return {
            "severity": "amber",
            "sentence": "EVM data not yet available — import a schedule to see performance.",
            "spi": None,
            "cpi": None,
            "baseline_finish": None,
            "forecast_finish": None,
            "months_late": None,
            "biggest_driver": None,
            "mc_p80": None,
            "mc_p80_delta_days": None,
        }

    spi = float(evm["spi"])
    cpi_raw = evm.get("cpi")
    cpi = float(cpi_raw) if cpi_raw is not None else None
    start = date.fromisoformat(evm["project_start"])
    baseline = date.fromisoformat(evm["project_end"])
    as_of = date.fromisoformat(evm["as_of"])

    forecast = _forecast_finish(spi, start, baseline, as_of)
    months_late = round(_months_diff(forecast, baseline), 1)

    # ── Performance label ─────────────────────────────────────────────────
    if spi >= 1.02:
        perf_label = "Ahead of schedule"
    elif spi >= 0.95:
        perf_label = "On schedule"
    elif spi >= 0.80:
        perf_label = "Behind schedule"
    else:
        perf_label = "Significantly behind schedule"

    # ── Schedule-variance sentence ────────────────────────────────────────
    if months_late > 0.5:
        var_str = f"Forecast finish {_fmt_month(forecast)} — {math.ceil(months_late)} months past baseline."
    elif months_late < -0.5:
        var_str = f"Forecast finish {_fmt_month(forecast)} — {math.floor(abs(months_late))} months ahead of baseline."
    else:
        var_str = f"Forecast finish {_fmt_month(forecast)} — on or near baseline ({_fmt_month(baseline)})."

    # ── Biggest delay driver ──────────────────────────────────────────────
    biggest_driver = None
    driver_str = ""
    if drc.get("has_data"):
        rcs = drc.get("root_causes", [])
        if rcs:
            top = rcs[0]  # already sorted by downstream_count desc
            biggest_driver = {
                "name": top["name"],
                "task_pk": top["task_pk"],
                "downstream_count": top["downstream_count"],
                "finish_variance_days": top["finish_variance_days"],
                "trade": top.get("trade", ""),
            }
            name = top["name"]
            name_short = name[:55] + "…" if len(name) > 55 else name
            driver_str = (
                f' Largest delay driver: "{name_short}"'
                f" affecting {top['downstream_count']} downstream activities."
            )

    # ── Monte Carlo P80 ───────────────────────────────────────────────────
    mc_p80 = None
    mc_p80_delta = None
    mc_str = ""
    if mc.get("has_data"):
        mc_p80 = mc["p80"]
        mc_p80_delta = mc["p80_delta"]
        p80_date = date.fromisoformat(mc_p80)
        mc_str = f" P80 scenario: {_fmt_month(p80_date)}."

    if cpi is not None:
        perf_detail = f"SPI {spi:.2f}, CPI {cpi:.2f}"
    else:
        perf_detail = f"SPI {spi:.2f} — cost data not imported"
    sentence = f"{perf_label} ({perf_detail}). {var_str}{driver_str}{mc_str}"

    return {
        "severity": _severity(spi),
        "sentence": sentence,
        "spi": spi,
        "cpi": cpi,
        "baseline_finish": baseline.isoformat(),
        "forecast_finish": forecast.isoformat(),
        "months_late": months_late,
        "biggest_driver": biggest_driver,
        "mc_p80": mc_p80,
        "mc_p80_delta_days": mc_p80_delta,
    }


def _build_block2(dcma: dict, anomaly: dict) -> dict:
    """Quality block: is the schedule built correctly?

    Sources: run_dcma_check(), detect_anomalies().
    """
    if not dcma.get("has_data"):
        return {
            "severity": "amber",
            "sentence": "DCMA data not yet available.",
            "dcma_score": None,
            "logic_total": 0,
            "fs_violations": 0,
            "unrealistic_baselines": 0,
            "worst_task": None,
        }

    score = int(dcma["score"])

    # Count FS violations and unrealistic baselines from anomaly detect
    fs_violations = 0
    unrealistic_baselines = 0
    worst_task: str | None = None

    if anomaly.get("has_data"):
        logic_items = anomaly.get("logic_anomalies", [])
        for item in logic_items:
            atype = item.get("anomaly_type", "")
            if atype == "fs_violation":
                fs_violations += 1
            elif atype == "unrealistic_baseline":
                unrealistic_baselines += 1
        # Worst = first item (services already sort by severity desc)
        if logic_items:
            worst_task = logic_items[0]["name"]

    logic_total = fs_violations + unrealistic_baselines

    # ── Quality label ─────────────────────────────────────────────────────
    if score >= 80:
        q_label = "Good"
    elif score >= 60:
        q_label = "Needs Attention"
    else:
        q_label = "At Risk"

    # ── Error breakdown ───────────────────────────────────────────────────
    error_parts: list[str] = []
    if fs_violations:
        error_parts.append(f"{fs_violations} FS violation{'s' if fs_violations != 1 else ''}")
    if unrealistic_baselines:
        error_parts.append(
            f"{unrealistic_baselines} unrealistic baseline{'s' if unrealistic_baselines != 1 else ''}"
        )
    errors_str = ", ".join(error_parts) if error_parts else "none flagged"

    if logic_total == 0:
        errors_detail = "No logic/data errors flagged."
    elif logic_total == 1:
        errors_detail = f"1 logic/data error found ({errors_str})."
    else:
        errors_detail = f"{logic_total} logic/data errors found ({errors_str})."

    worst_str = ""
    if worst_task:
        name_short = worst_task[:55] + "…" if len(worst_task) > 55 else worst_task
        worst_str = f' Worst: "{name_short}".'

    sentence = f"Schedule quality {score}/100 ({q_label}). {errors_detail}{worst_str}"

    return {
        "severity": _dcma_severity(score),
        "sentence": sentence,
        "dcma_score": score,
        "logic_total": logic_total,
        "fs_violations": fs_violations,
        "unrealistic_baselines": unrealistic_baselines,
        "worst_task": worst_task,
    }


def _display_name(name: str, activity_code: str) -> str:
    """'Task name (ACTIVITY-CODE)' — code is the unique disambiguator."""
    code = (activity_code or "").strip()
    return f"{name} ({code})" if code else name


def _build_block3(drc: dict, ml: dict) -> dict:
    """Priority block: top-5 items needing attention today.

    Sources: run_delay_rootcause() root_causes, run_completion_ml() watchlist.

    Slot reservation — guarantees both kinds surface:
      Slots 1-3: top delay root-causes by downstream_count (already sorted desc).
      Slots 4-5: top ML forward-risk items (near-critical first, lowest P first).
    Dedup by task_pk: if a delay task also appears in ML watchlist it occupies
    its delay slot only; the next-best ML item fills the gap.

    Activity code is appended to every name so identical task names are
    disambiguated by their unique identifier.
    """
    seen_pks: set[str] = set()

    # ── Slots 1-3: delay root-causes ─────────────────────────────────────
    delay_slots: list[dict] = []
    if drc.get("has_data"):
        for rc in drc.get("root_causes", []):
            if len(delay_slots) >= 3:
                break
            pk = rc["task_pk"]
            if pk in seen_pks:
                continue
            seen_pks.add(pk)
            dc = rc["downstream_count"]
            fv = rc["finish_variance_days"]
            reason = f"drives {dc} downstream activit{'y' if dc == 1 else 'ies'}"
            if fv:
                reason += f" ({fv}d own delay)"
            delay_slots.append(
                {
                    "task_pk": pk,
                    "name": _display_name(rc["name"], rc.get("activity_code", "")),
                    "reason": reason,
                    "metric": f"{dc} downstream",
                    "source": "delay",
                    "section_anchor": "evm-drc-section",
                }
            )

    # ── Slots 4-5: ML forward-risk — near-critical only (float ≤ 10 wd) ─────
    # Tasks with comfortably positive float must not appear under "AT-RISK".
    # near_critical flag is set by completion_ml.py at the same threshold (10 wd).
    ml_slots: list[dict] = []
    if ml.get("has_data"):
        for w in ml.get("watchlist", []):
            if len(ml_slots) >= 2:
                break
            if not w.get("near_critical", False):
                continue  # skip tasks with float > 10 wd
            pk = w["task_pk"]
            if pk in seen_pks:
                continue
            seen_pks.add(pk)
            prob = w["probability_on_time"]
            tf = w["total_float"]
            pct = round(prob * 100)
            float_str = f", float {tf}d" if tf < 9000 else ""
            reason = f"P(on-time) = {pct}%{float_str}"
            ml_slots.append(
                {
                    "task_pk": pk,
                    "name": _display_name(w["name"], w.get("activity_code", "")),
                    "reason": reason,
                    "metric": f"P = {pct}%",
                    "source": "ml",
                    "section_anchor": "evm-ml-section",
                }
            )

    ranked = [{**item, "rank": i} for i, item in enumerate(delay_slots + ml_slots, start=1)]
    return {"items": ranked}


# ── Public entry point ────────────────────────────────────────────────────────


def compute_decision_summary(project_id: str) -> dict:
    """Assemble the three decision blocks from existing services.

    All service calls are defensive — if any fails, that block degrades gracefully
    to an 'n/a' state rather than propagating the error.

    Returns:
        has_data   — bool
        block1     — performance / heading
        block2     — schedule quality
        block3     — top-5 priority items
        as_of      — ISO date
    """
    from .anomaly_detect import detect_anomalies
    from .completion_ml import run_completion_ml
    from .dcma_check import run_dcma_check
    from .delay_rootcause import run_delay_rootcause
    from .evm import compute_evm
    from .monte_carlo import compute_monte_carlo

    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("Decision layer: %s failed for project %s", fn.__name__, project_id)
            return {"has_data": False}

    evm = _safe(compute_evm, project_id)
    drc = _safe(run_delay_rootcause, project_id)
    mc = _safe(compute_monte_carlo, project_id)
    dcma = _safe(run_dcma_check, project_id)
    anomaly = _safe(detect_anomalies, project_id)
    ml = _safe(run_completion_ml, project_id)

    if not evm.get("has_data") and not dcma.get("has_data") and not ml.get("has_data"):
        return {"has_data": False}

    block1 = _build_block1(evm, drc, mc)
    block2 = _build_block2(dcma, anomaly)
    block3 = _build_block3(drc, ml)

    logger.info(
        "Decision summary — project %s: block1=%s block2=%s items=%d",
        project_id,
        block1["severity"],
        block2["severity"],
        len(block3["items"]),
    )

    return {
        "has_data": True,
        "block1": block1,
        "block2": block2,
        "block3": block3,
        "as_of": evm.get("as_of") or date.today().isoformat(),
    }
