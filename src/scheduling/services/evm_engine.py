# castor/scheduling/services/evm_engine.py
"""EVM Intelligence Layer — Critical Path summary, Earned Schedule, and WBS risk scores.

Exposed via GET /castor/projects/<pk>/schedule/intelligence/. Earned Schedule follows
the PMI ES Practice Standard (ES interpolated from PV curve, SPI(t) = ES/AT,
IEAC(t) = planned_duration/SPI(t)). WBS risk weights: float utilisation 35%,
progress gap 35%, overrun rate 30%.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_STAGE_LABELS: dict[str, str] = {
    "substructure": "Substructure",
    "structure": "Structure",
    "envelope": "Envelope",
    "mep": "MEP",
    "finishes": "Finishes",
    "external": "External Works",
    "_unassigned": "Unassigned",
}


def compute_schedule_intelligence(project_id: str) -> dict:
    """Return the full intelligence payload for *project_id*.

    Keys:
        has_data        — bool
        critical_path   — CPM summary + critical task list
        earned_schedule — ES metrics (SPI(t), SV(t), IEAC)
        wbs_risk_scores — per-stage risk cards, sorted by score descending
    """
    from .evm import compute_evm

    evm_data = compute_evm(project_id)

    cpm = _build_cpm_summary(project_id)
    es = _compute_earned_schedule(evm_data) if evm_data.get("has_data") else {"has_data": False}
    wbs = _compute_wbs_risk(project_id, evm_data)

    return {
        "has_data": bool(evm_data.get("has_data") or cpm.get("computed")),
        "critical_path": cpm,
        "earned_schedule": es,
        "wbs_risk_scores": wbs,
    }


def _build_cpm_summary(project_id: str) -> dict:
    """Return CPM summary from Task CPM fields, triggering a recompute if absent."""
    from castor.scheduling.models import Task

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .order_by("start_date")
    )

    if not tasks:
        return {"computed": False, "reason": "no tasks", "critical_tasks": []}

    if not any(t.total_float is not None for t in tasks):
        try:
            from .critical_path import compute_critical_path

            compute_critical_path(project_id)
            tasks = list(
                Task.objects.filter(project_id=project_id, is_non_physical=False)
                .exclude(start_date=None)
                .exclude(end_date=None)
                .order_by("start_date")
            )
        except Exception as exc:
            logger.warning("CPM auto-run failed for project %s: %s", project_id, exc)
            return {"computed": False, "reason": str(exc), "critical_tasks": []}

    critical_tasks = [t for t in tasks if t.is_critical]
    project_start = min(t.start_date for t in tasks)
    project_end = max(t.end_date for t in tasks)

    return {
        "computed": True,
        "critical_task_count": len(critical_tasks),
        "total_task_count": len(tasks),
        "project_duration_days": (project_end - project_start).days + 1,
        "project_start": project_start.isoformat(),
        "project_end": project_end.isoformat(),
        "critical_tasks": [
            {
                "task_id": str(t.pk),
                "activity_code": t.activity_code,
                "name": t.name,
                "start_date": t.start_date.isoformat(),
                "end_date": t.end_date.isoformat(),
                "total_float": t.total_float,
                "stage": t.stage or "",
                "status": t.status,
                "actual_start": t.actual_start.isoformat() if t.actual_start else None,
                "actual_end": t.actual_end.isoformat() if t.actual_end else None,
            }
            for t in critical_tasks
        ],
    }


def _compute_earned_schedule(evm_data: dict) -> dict:
    """Derive Earned Schedule (ES) metrics from compute_evm() output.

    ES is the time-based analogue of Earned Value: instead of comparing cost
    to budget, it compares time earned to time elapsed.
    """
    if not evm_data.get("has_data"):
        return {"has_data": False}

    bac = evm_data["bac"]
    ev_abs = evm_data["ev"]
    pv_series = evm_data["series"]["pv"]

    if bac <= 0 or not pv_series:
        return {"has_data": False, "reason": "no BAC or PV series"}

    # EV=0: SPI(t), SV(t), and IEAC are all degenerate — disclose rather than
    # display zeros or a capped placeholder.
    if ev_abs == 0:
        return {
            "has_data": False,
            "reason": "no earned value recorded",
            "note": "Earned Schedule requires a recorded progress update.",
        }

    project_start = date.fromisoformat(evm_data["project_start"])
    project_end = date.fromisoformat(evm_data["project_end"])
    as_of = date.fromisoformat(evm_data["as_of"])

    # AT: elapsed calendar days from project start to data date (PMI ES standard)
    at_days = max((as_of - project_start).days, 0)
    project_duration_days = max((project_end - project_start).days, 1)

    # EV as % of BAC — clamped to [0, 100]
    current_ev_pct = min(100.0, (ev_abs / bac) * 100)

    # ES: interpolated date on PV curve where PV% = current EV%
    es_date = _find_es_date(pv_series, current_ev_pct, project_start, project_end)
    es_days = max((es_date - project_start).days, 0)

    # SPI(t): guard AT = 0 (project has not started yet)
    spi_t = round(es_days / at_days, 3) if at_days > 0 else 1.0

    # SV(t) in calendar days — explicitly calendar, not working days
    sv_t_days = es_days - at_days

    # IEAC(t) — sane-horizon guard replaces the bare 3× cap.
    # Forecasts beyond baseline + 3×planned_duration are formula artefacts
    # (same logic as the _forecast_finish fix in decision_layer.py).
    sane_horizon = project_end + timedelta(days=project_duration_days * 3)
    ieac_days: int | None = None
    ieac_date: str | None = None
    ieac_suppressed_reason: str | None = None

    if spi_t <= 0:
        ieac_suppressed_reason = (
            f"Progress too low to forecast a reliable finish date (SPI(t) {spi_t:.3f})."
        )
    else:
        raw_ieac = round(project_duration_days / spi_t)
        candidate = project_start + timedelta(days=raw_ieac)
        if candidate > sane_horizon:
            ieac_suppressed_reason = (
                f"Progress too low to forecast a reliable finish date (SPI(t) {spi_t:.3f})."
            )
        else:
            ieac_days = raw_ieac
            ieac_date = candidate.isoformat()

    if at_days == 0:
        interpretation = "Project has not started."
    elif spi_t >= 1.05:
        interpretation = f"Ahead of schedule by ~{abs(sv_t_days)} calendar days."
    elif spi_t >= 0.95:
        interpretation = "On schedule."
    elif spi_t >= 0.80:
        interpretation = f"Slightly behind — ~{abs(sv_t_days)}-calendar-day delay projected."
    else:
        interpretation = f"Significantly behind — ~{abs(sv_t_days)}-calendar-day delay projected."

    return {
        "has_data": True,
        "at_days": at_days,
        "es_days": es_days,
        "es_date": es_date.isoformat(),
        "spi_t": spi_t,
        "sv_t_days": sv_t_days,
        "project_duration_days": project_duration_days,
        "ieac_days": ieac_days,
        "ieac_date": ieac_date,
        "ieac_suppressed_reason": ieac_suppressed_reason,
        "current_ev_pct": round(current_ev_pct, 1),
        "as_of": evm_data["as_of"],
        "project_start": evm_data["project_start"],
        "project_end": evm_data["project_end"],
        "interpretation": interpretation,
    }


def _find_es_date(
    pv_series: list[dict], ev_pct: float, project_start: date, project_end: date
) -> date:
    """Linearly interpolate the PV curve to find the date where PV% = ev_pct."""
    if ev_pct <= 0 or not pv_series:
        return project_start

    # EV has passed the last PV point → ahead of schedule; return that date
    if ev_pct >= pv_series[-1]["pct"]:
        return date.fromisoformat(pv_series[-1]["date"])

    # EV below the first PV point → ES is before the first recorded PV date
    if pv_series[0]["pct"] > 0 and ev_pct < pv_series[0]["pct"]:
        return project_start

    for i in range(len(pv_series) - 1):
        p0, p1 = pv_series[i], pv_series[i + 1]
        if p0["pct"] <= ev_pct <= p1["pct"]:
            span = p1["pct"] - p0["pct"]
            if span == 0:
                return date.fromisoformat(p0["date"])
            t = (ev_pct - p0["pct"]) / span
            d0 = date.fromisoformat(p0["date"])
            d1 = date.fromisoformat(p1["date"])
            return d0 + timedelta(days=round((d1 - d0).days * t))

    return project_end


def _compute_wbs_risk(project_id: str, evm_data: dict) -> list[dict]:
    """Return a risk card for each construction stage (WBS proxy).

    Risk score components:
        Float utilisation (35%) — critical_pct * 90 + near_critical_pct * 30
        Progress gap     (35%) — (planned% - earned%) * 4  [25% gap → 100]
        Overrun rate     (30%) — overrun_fraction * 150    [67% overrun → 100]

    All component sub-scores are clamped to [0, 100] before weighting.
    """
    from castor.scheduling.models import Task

    from .calendar_utils import load_project_calendars, task_cal
    from .evm import _earned_pct_at, _planned_pct_at

    # Use the same as-of date that evm_data was computed with — never date.today().
    today = date.fromisoformat(evm_data["as_of"])

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return []

    cal_map = load_project_calendars(project_id)
    task_cals = {str(t.pk): task_cal(t, cal_map) for t in tasks} if cal_map else {}

    # Group by construction stage; unmapped tasks go to "_unassigned"
    groups: dict[str, list] = {}
    for t in tasks:
        groups.setdefault(t.stage or "_unassigned", []).append(t)

    result = []
    for stage, group in groups.items():
        n = len(group)

        with_float = [t for t in group if t.total_float is not None]
        if with_float:
            critical_pct = sum(1 for t in with_float if t.is_critical) / n
            near_critical_pct = (
                sum(1 for t in with_float if not t.is_critical and 0 < t.total_float <= 5) / n
            )
            avg_float_days: float | None = round(
                sum(t.total_float for t in with_float) / len(with_float), 1
            )
            float_risk = min(100.0, critical_pct * 90 + near_critical_pct * 30)
        else:
            critical_pct = near_critical_pct = float_risk = 0.0
            avg_float_days = None

        planned_sum = sum(_planned_pct_at(t, today, task_cals.get(str(t.pk))) for t in group)
        earned_sum = sum(_earned_pct_at(t, today, task_cals.get(str(t.pk))) for t in group)
        planned_pct_avg = round(planned_sum / n * 100, 1)
        earned_pct_avg = round(earned_sum / n * 100, 1)
        gap_pct = max(0.0, planned_pct_avg - earned_pct_avg)
        progress_risk = min(100.0, gap_pct * 4)

        overrun_tasks = [t for t in group if t.status != "complete" and today > t.end_date]
        overrun_frac = len(overrun_tasks) / n
        overrun_risk = min(100.0, overrun_frac * 150)

        score = round(float_risk * 0.35 + progress_risk * 0.35 + overrun_risk * 0.30)
        band = "green" if score <= 30 else "amber" if score <= 60 else "red"

        drivers: list[str] = []
        if critical_pct >= 0.3:
            drivers.append(f"{round(critical_pct * 100)}% of tasks on critical path")
        if near_critical_pct >= 0.2:
            drivers.append(f"{round(near_critical_pct * 100)}% near-critical (float ≤ 5d)")
        if gap_pct >= 10:
            drivers.append(f"{round(gap_pct, 1)}% progress behind plan")
        if overrun_frac >= 0.15:
            drivers.append(f"{round(overrun_frac * 100)}% of tasks past planned end")
        if not drivers:
            drivers = ["No significant risk drivers identified"]

        completed = sum(1 for t in group if t.status == "complete")

        result.append(
            {
                "wbs_id": stage,
                "label": _STAGE_LABELS.get(stage, stage.replace("_", " ").title()),
                "task_count": n,
                "risk_score": score,
                "risk_band": band,
                "drivers": drivers,
                "metrics": {
                    "critical_task_pct": round(critical_pct * 100, 1),
                    "near_critical_pct": round(near_critical_pct * 100, 1),
                    "avg_float_days": avg_float_days,
                    "planned_pct": planned_pct_avg,
                    "earned_pct": earned_pct_avg,
                    "progress_gap_pct": round(gap_pct, 1),
                    "overrun_task_count": len(overrun_tasks),
                    "overrun_task_pct": round(overrun_frac * 100, 1),
                    "completed": completed,
                },
            }
        )

    result.sort(key=lambda x: x["risk_score"], reverse=True)
    return result
