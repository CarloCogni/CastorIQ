# islam/scheduling/services/evm.py
"""Earned Value Management (EVM) — PV, EV, AC, SPI, CPI, S-curve series."""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _qto_task_costs(project_id: str, tasks: list) -> dict[str, float]:
    """Return QTO-derived cost estimate per task (keyed by task PK string).

    Only considers tasks with no schedule cost.  Requires QTOCache to exist
    and unit costs to be configured for at least some element types.
    Returns an empty dict if any prerequisite is missing.
    """
    try:
        from islam.ifc_insights.models import QTOCache
        from islam.scheduling.models import TaskEntityBinding
    except ImportError:
        return {}

    cache = QTOCache.objects.filter(project_id=project_id).first()
    if not cache:
        return {}

    # Build entity → cost lookup (quantity × unit_cost) from the per-entity cache
    entity_costs: dict[str, float] = {}
    for item in cache.items_json:
        gid = item.get("global_id")
        qty = item.get("quantity")
        uc = item.get("unit_cost")
        if gid and qty is not None and uc is not None:
            entity_costs[gid] = float(qty) * float(uc)

    if not entity_costs:
        return {}

    no_cost_pks = {str(t.pk) for t in tasks if not float(t.cost or 0)}
    if not no_cost_pks:
        return {}

    result: dict[str, float] = {}
    for binding in TaskEntityBinding.objects.filter(task_id__in=no_cost_pks).values(
        "task_id", "entity_global_id"
    ):
        cost = entity_costs.get(binding["entity_global_id"], 0.0)
        if cost > 0:
            pk_str = str(binding["task_id"])
            result[pk_str] = result.get(pk_str, 0.0) + cost

    return result


def _planned_pct_at(task, d: date) -> float:
    """Linear schedule progress 0-1 for task planned dates at date *d*."""
    if d >= task.end_date:
        return 1.0
    if d < task.start_date:
        return 0.0
    dur = max((task.end_date - task.start_date).days, 1)
    return (d - task.start_date).days / dur


def _earned_pct_at(task, d: date) -> float:
    """Actual earned progress 0-1 for task at date *d*.

    When actual_start is recorded it gates all EV (nothing earned before work
    begins).  When only status="complete" is set (no actual dates), we treat
    planned end_date as the completion reference so EV stays at 0 before that
    date — preventing complete tasks from inflating the EV curve at project
    start.
    """
    if task.actual_start:
        if task.actual_start > d:
            return 0.0
        if task.status == "complete" or (task.actual_end and task.actual_end <= d):
            return 1.0
        dur = max((task.end_date - task.actual_start).days, 1)
        return max(0.0, min(1.0, (d - task.actual_start).days / dur))

    # No actual_start recorded
    if task.status == "complete":
        return 1.0 if d >= task.end_date else 0.0
    return 0.0


_DELAY_BUCKETS = [
    {"label": "On Time", "min": 0, "max": 0, "color": "#16a34a"},
    {"label": "1–7 days", "min": 1, "max": 7, "color": "#84cc16"},
    {"label": "8–14 days", "min": 8, "max": 14, "color": "#d97706"},
    {"label": "15–30 days", "min": 15, "max": 30, "color": "#ea580c"},
    {"label": "31+ days", "min": 31, "max": None, "color": "#dc2626"},
]


def _task_delay_days(task, today: date) -> int | None:
    """Days a task is delayed as of *today*.  None = not yet due (skip)."""
    if task.start_date > today:
        return None  # future task
    if task.status == "complete":
        return max(0, (task.actual_end - task.end_date).days) if task.actual_end else 0
    if today > task.end_date:
        return (today - task.end_date).days
    if today > task.start_date and not task.actual_start:
        return (today - task.start_date).days
    return 0


def compute_delay_distribution(project_id: str) -> dict:
    """Distribution of tasks across delay buckets for the Delay Chart.

    Returns:
        has_data      — bool
        buckets       — list[{label, color, count, pct, sample_tasks}]
        total         — int (tasks in scope, excludes not-yet-due)
        not_yet_due   — int
    """
    from islam.scheduling.models import Task

    today = date.today()
    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    counts: list[int] = [0] * len(_DELAY_BUCKETS)
    samples: list[list[str]] = [[] for _ in _DELAY_BUCKETS]
    not_yet_due = 0

    for t in tasks:
        delay = _task_delay_days(t, today)
        if delay is None:
            not_yet_due += 1
            continue
        for i, b in enumerate(_DELAY_BUCKETS):
            if b["max"] is None:
                if delay >= b["min"]:
                    counts[i] += 1
                    if len(samples[i]) < 3:
                        samples[i].append(t.name)
                    break
            elif b["min"] <= delay <= b["max"]:
                counts[i] += 1
                if len(samples[i]) < 3:
                    samples[i].append(t.name)
                break

    total = sum(counts)
    buckets = []
    for i, b in enumerate(_DELAY_BUCKETS):
        buckets.append(
            {
                "label": b["label"],
                "color": b["color"],
                "count": counts[i],
                "pct": round(counts[i] / total * 100, 1) if total else 0,
                "sample_tasks": samples[i],
            }
        )

    return {
        "has_data": True,
        "buckets": buckets,
        "total": total,
        "not_yet_due": not_yet_due,
    }


_STAGE_ORDER = ["substructure", "structure", "envelope", "mep", "finishes", "external", ""]
_STAGE_LABELS = {
    "substructure": "Substructure",
    "structure": "Structure",
    "envelope": "Envelope",
    "mep": "MEP",
    "finishes": "Finishes",
    "external": "External Works",
    "": "Unassigned",
}


def compute_wbs_heatmap(project_id: str) -> list[dict]:
    """Per-stage performance metrics for the WBS heatmap.

    Each entry: stage, label, task_count, completed, planned_pct, earned_pct,
    spi, color.  Uses equal-weight (task count) SPI so no cost data is needed.
    """
    from islam.scheduling.models import Task

    today = date.today()
    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return []

    groups: dict[str, list] = {}
    for t in tasks:
        groups.setdefault(t.stage or "", []).append(t)

    result = []
    for stage, stage_tasks in groups.items():
        n = len(stage_tasks)
        completed = sum(1 for t in stage_tasks if t.status == "complete")
        planned_sum = sum(_planned_pct_at(t, today) for t in stage_tasks)
        earned_sum = sum(_earned_pct_at(t, today) for t in stage_tasks)

        planned_pct = round(planned_sum / n * 100, 1)
        earned_pct = round(earned_sum / n * 100, 1)
        spi = round(earned_sum / planned_sum, 2) if planned_sum > 0 else 1.0

        if planned_pct == 0 and earned_pct == 0:
            color = "#6b7280"
        elif spi >= 1.0:
            color = "#16a34a"
        elif spi >= 0.85:
            color = "#d97706"
        else:
            color = "#dc2626"

        result.append(
            {
                "stage": stage,
                "label": _STAGE_LABELS.get(stage, stage.title()),
                "task_count": n,
                "completed": completed,
                "planned_pct": planned_pct,
                "earned_pct": earned_pct,
                "spi": spi,
                "color": color,
            }
        )

    result.sort(key=lambda r: _STAGE_ORDER.index(r["stage"]) if r["stage"] in _STAGE_ORDER else 99)
    return result


def compute_evm(project_id: str, as_of_date: date | None = None) -> dict:
    """Compute EVM metrics and weekly S-curve series for *project_id*.

    Returns a dict with:
        has_data       — bool
        bac            — float (Budget at Completion)
        pv / ev / ac   — float (as-of values)
        spi / cpi      — float
        eac / vac      — float
        sv / cv        — float
        use_cost       — bool (True = cost-based, False = duration-based)
        as_of          — ISO date string
        project_start / project_end — ISO date strings
        series         — {pv: [{date, pct}], ev: [...], ac: [...]}
                         pct is 0-100 as fraction of BAC for chart rendering
    """
    from islam.scheduling.models import Task

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    today = as_of_date or date.today()

    # ------------------------------------------------------------------
    # Value basis: schedule costs → QTO estimates → duration weighting
    # ------------------------------------------------------------------
    sched_costs = {str(t.pk): float(t.cost or 0) for t in tasks}
    total_sched = sum(sched_costs.values())

    qto_costs = _qto_task_costs(str(project_id), tasks)

    values: dict[str, float] = {}
    used_qto = False
    for t in tasks:
        pk = str(t.pk)
        sc = sched_costs.get(pk, 0.0)
        if sc > 0:
            values[pk] = sc
        elif pk in qto_costs:
            values[pk] = qto_costs[pk]
            used_qto = True
        else:
            values[pk] = 0.0

    total_value = sum(values.values())
    use_cost = total_value > 0

    if use_cost:
        bac = total_value
        if total_sched > 0 and used_qto:
            cost_basis = "schedule costs + QTO estimates"
        elif total_sched > 0:
            cost_basis = "schedule costs"
        else:
            cost_basis = "QTO estimates"
    else:
        # Pure duration-weighted fallback
        values = {str(t.pk): float(max((t.end_date - t.start_date).days + 1, 1)) for t in tasks}
        bac = sum(values.values())
        cost_basis = "task durations"

    project_start = min(t.start_date for t in tasks)
    project_end = max(t.end_date for t in tasks)

    # ------------------------------------------------------------------
    # Weekly date spine from project_start to max(project_end, today)
    # ------------------------------------------------------------------
    spine_end = max(project_end, today)
    dates: list[date] = []
    cur = project_start
    while cur <= spine_end:
        dates.append(cur)
        cur += timedelta(weeks=1)
    if spine_end not in dates:
        dates.append(spine_end)
    dates.sort()

    # ------------------------------------------------------------------
    # Build series
    # ------------------------------------------------------------------
    pv_series: list[dict] = []
    ev_series: list[dict] = []
    ac_series: list[dict] = []

    for d in dates:
        pv_abs = sum(_planned_pct_at(t, d) * values[str(t.pk)] for t in tasks)
        pv_series.append({"date": d.isoformat(), "pct": round(pv_abs / bac * 100, 2) if bac else 0})

        if d <= today:
            ev_abs = sum(_earned_pct_at(t, d) * values[str(t.pk)] for t in tasks)
            ac_abs = ev_abs * 1.05
            ev_series.append(
                {"date": d.isoformat(), "pct": round(ev_abs / bac * 100, 2) if bac else 0}
            )
            ac_series.append(
                {"date": d.isoformat(), "pct": round(ac_abs / bac * 100, 2) if bac else 0}
            )

    # ------------------------------------------------------------------
    # As-of KPI metrics
    # ------------------------------------------------------------------
    pv_today = sum(_planned_pct_at(t, today) * values[str(t.pk)] for t in tasks)
    ev_today = sum(_earned_pct_at(t, today) * values[str(t.pk)] for t in tasks)
    ac_today = ev_today * 1.05

    spi = round(ev_today / pv_today, 3) if pv_today > 0 else 1.0
    cpi = round(ev_today / ac_today, 3) if ac_today > 0 else 1.0
    eac = round(bac / cpi, 2) if cpi > 0 else bac
    vac = round(bac - eac, 2)
    sv = round(ev_today - pv_today, 2)
    cv = round(ev_today - ac_today, 2)

    logger.info(
        "EVM — project %s: BAC=%.0f SPI=%.2f CPI=%.2f EAC=%.0f",
        project_id,
        bac,
        spi,
        cpi,
        eac,
    )

    return {
        "has_data": True,
        "bac": round(bac, 2),
        "pv": round(pv_today, 2),
        "ev": round(ev_today, 2),
        "ac": round(ac_today, 2),
        "spi": spi,
        "cpi": cpi,
        "eac": eac,
        "vac": vac,
        "sv": sv,
        "cv": cv,
        "use_cost": use_cost,
        "cost_basis": cost_basis,
        "as_of": today.isoformat(),
        "project_start": project_start.isoformat(),
        "project_end": project_end.isoformat(),
        "series": {
            "pv": pv_series,
            "ev": ev_series,
            "ac": ac_series,
        },
    }
