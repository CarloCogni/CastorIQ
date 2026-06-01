# islam/scheduling/services/evm_engine.py
"""EVM Intelligence Layer — Sprint 1.

Three analytics modules, each independently callable or as one composite call:

    compute_schedule_intelligence(project_id) -> dict

Exposed via GET /islam/projects/<pk>/schedule/intelligence/.

Module 1 — Critical Path summary
    Reads CPM fields already persisted on Task by compute_critical_path().
    Triggers a fresh CPM run if the fields are absent.  Returns the list
    of critical activities with float, dates, and status.

Module 2 — Earned Schedule (ES)
    Time-based schedule efficiency derived from the S-curve produced by
    compute_evm().  Formulae follow the PMI Earned Schedule Practice
    Standard:

        AT   = Actual Time elapsed (project_start → data_date)
        ES   = interpolated point on the PV curve where PV% = today's EV%
        SPI(t) = ES / AT           (>1 = ahead, <1 = behind)
        SV(t)  = ES − AT           (days ahead or behind schedule)
        IEAC(t) = planned_duration / SPI(t)

Module 3 — Schedule Risk Score per WBS Package
    Groups tasks by construction stage (the available WBS proxy) and
    scores each group 0-100 from three weighted components:

        Float utilisation (35%) — critical + near-critical fraction
        Progress gap     (35%) — earned% below planned%
        Overrun rate     (30%) — tasks past planned end, still incomplete

    Bands:  Green ≤ 30 · Amber 31-60 · Red ≥ 61
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


# ── Public entry point ────────────────────────────────────────────────────────


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


# ── Module 1: Critical Path ───────────────────────────────────────────────────


def _build_cpm_summary(project_id: str) -> dict:
    """Return CPM summary from Task CPM fields, triggering a recompute if absent."""
    from islam.scheduling.models import Task

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


# ── Module 2: Earned Schedule ─────────────────────────────────────────────────


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

    project_start = date.fromisoformat(evm_data["project_start"])
    project_end = date.fromisoformat(evm_data["project_end"])
    as_of = date.fromisoformat(evm_data["as_of"])

    # AT: elapsed days from project start to data date
    at_days = max((as_of - project_start).days, 0)
    project_duration_days = max((project_end - project_start).days, 1)

    # EV as % of BAC — clamped to [0, 100]
    current_ev_pct = min(100.0, (ev_abs / bac) * 100)

    # ES: interpolated date on PV curve where PV% = current EV%
    es_date = _find_es_date(pv_series, current_ev_pct, project_start, project_end)
    es_days = max((es_date - project_start).days, 0)

    # SPI(t): guard AT = 0 (project has not started yet)
    spi_t = round(es_days / at_days, 3) if at_days > 0 else 1.0

    # SV(t) in calendar days (negative = behind)
    sv_t_days = es_days - at_days

    # IEAC(t) = planned duration / SPI(t), capped at 3× to avoid outliers
    if spi_t > 0.01:
        ieac_days = min(round(project_duration_days / spi_t), project_duration_days * 3)
    else:
        ieac_days = project_duration_days * 3

    ieac_date = (project_start + timedelta(days=ieac_days)).isoformat()

    if at_days == 0:
        interpretation = "Project has not started."
    elif spi_t >= 1.05:
        interpretation = f"Ahead of schedule by ~{abs(sv_t_days)} days."
    elif spi_t >= 0.95:
        interpretation = "On schedule."
    elif spi_t >= 0.80:
        interpretation = f"Slightly behind — ~{abs(sv_t_days)}-day delay projected."
    else:
        interpretation = f"Significantly behind — ~{abs(sv_t_days)}-day delay projected."

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


# ── Module 3: Schedule Risk per WBS ──────────────────────────────────────────


def _compute_wbs_risk(project_id: str, evm_data: dict) -> list[dict]:
    """Return a risk card for each construction stage (WBS proxy).

    Risk score components:
        Float utilisation (35%) — critical_pct * 90 + near_critical_pct * 30
        Progress gap     (35%) — (planned% - earned%) * 4  [25% gap → 100]
        Overrun rate     (30%) — overrun_fraction * 150    [67% overrun → 100]

    All component sub-scores are clamped to [0, 100] before weighting.
    """
    from islam.scheduling.models import Task

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

    # Group by construction stage; unmapped tasks go to "_unassigned"
    groups: dict[str, list] = {}
    for t in tasks:
        groups.setdefault(t.stage or "_unassigned", []).append(t)

    result = []
    for stage, group in groups.items():
        n = len(group)

        # ── Float risk ────────────────────────────────────────────────────
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

        # ── Progress gap ─────────────────────────────────────────────────
        planned_sum = sum(_planned_pct_at(t, today) for t in group)
        earned_sum = sum(_earned_pct_at(t, today) for t in group)
        planned_pct_avg = round(planned_sum / n * 100, 1)
        earned_pct_avg = round(earned_sum / n * 100, 1)
        gap_pct = max(0.0, planned_pct_avg - earned_pct_avg)
        progress_risk = min(100.0, gap_pct * 4)

        # ── Overrun risk ─────────────────────────────────────────────────
        overrun_tasks = [t for t in group if t.status != "complete" and today > t.end_date]
        overrun_frac = len(overrun_tasks) / n
        overrun_risk = min(100.0, overrun_frac * 150)

        # ── Composite score ───────────────────────────────────────────────
        score = round(float_risk * 0.35 + progress_risk * 0.35 + overrun_risk * 0.30)
        band = "green" if score <= 30 else "amber" if score <= 60 else "red"

        # ── Narrative drivers ────────────────────────────────────────────
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
