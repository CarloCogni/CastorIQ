# castor/scheduling/services/evm.py
"""Earned Value Management (EVM) — PV, EV, AC, SPI, CPI, S-curve series.

Input-integrity principle
-------------------------
Every metric declares its required inputs.  If inputs are absent or
incomplete, the metric is disabled with a plain explanation — never
fabricated and never silently derived from a proxy.

Metric requirements
-------------------
  BAC / PV / EV / SPI / SV
      Require planned cost on activities (task.cost from resource
      assignments or QTO estimates).  If cost covers only a subset of
      tasks, the values are reported WITH a coverage notice.

  AC / CPI / CV / EAC / VAC
      Require REAL actual cost from P6ResourceAssignment.actual_cost.
      If none is imported, these metrics are DISABLED — never estimated
      from a formula such as EV × k.

  SPI / PV / EV
      Also require actual progress data (actual_start / actual_end /
      status).  If 0% actuals exist, EV = 0 and SPI is reported as-is
      (not fabricated).

Duration fallback
-----------------
  When no cost exists at all, a duration-weighted proxy is computed and
  returned with cost_basis="task durations" and use_cost=False.  Callers
  and the UI must surface this clearly — these are NOT monetary EVM numbers.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .calendar_utils import load_project_calendars, task_cal, working_day_diff
from .utils import get_project_data_date

logger = logging.getLogger(__name__)


# ── Actual-cost loader ────────────────────────────────────────────────────────


def _load_actual_costs(task_pks: list[str]) -> dict[str, float]:
    """Return {str(task_pk): total_actual_cost} from P6ResourceAssignment.

    Sums all resource types (labor, material, equipment, expense) per task.
    Returns {} if no ResourceAssignment rows exist or none have actual_cost > 0.
    Never raises.
    """
    from castor.scheduling.models import P6ResourceAssignment

    result: dict[str, float] = {}
    try:
        for ra in P6ResourceAssignment.objects.filter(
            task_id__in=task_pks, actual_cost__gt=0
        ).values("task_id", "actual_cost"):
            pk = str(ra["task_id"])
            result[pk] = result.get(pk, 0.0) + float(ra["actual_cost"] or 0)
    except Exception as exc:
        logger.debug("_load_actual_costs: %s", exc)
    return result


# ── Progress helpers ──────────────────────────────────────────────────────────


def _qto_task_costs(project_id: str, tasks: list) -> dict[str, float]:
    """Return QTO-derived cost estimate per task (keyed by task PK string).

    Only considers tasks with no schedule cost.  Requires QTOCache to exist
    and unit costs to be configured for at least some element types.
    Returns an empty dict if any prerequisite is missing.
    """
    try:
        from castor.ifc_insights.models import QTOCache
        from castor.scheduling.models import TaskEntityBinding
    except ImportError:
        return {}

    cache = QTOCache.objects.filter(project_id=project_id).first()
    if not cache:
        return {}

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


def _planned_pct_at(task, d: date, cal=None) -> float:
    """Linear schedule progress 0-1 for task planned dates at date *d*.

    *cal* is the task's runtime calendar dict from calendar_utils.  When
    provided, spans are measured in working days using ``working_day_diff``
    (half-open (start, end] — same semantics as ``.days``).  Falls back to
    calendar-day arithmetic when *cal* is None.
    """
    if d >= task.end_date:
        return 1.0
    if d < task.start_date:
        return 0.0
    if cal is not None:
        dur = max(working_day_diff(task.start_date, task.end_date, cal), 1)
        return max(0.0, min(1.0, working_day_diff(task.start_date, d, cal) / dur))
    dur = max((task.end_date - task.start_date).days, 1)
    return (d - task.start_date).days / dur


def _earned_pct_at(task, d: date, cal=None) -> float:
    """Actual earned progress 0-1 for task at date *d*.

    Priority for in-progress tasks (actual_start recorded, not yet complete):
      1. task.physical_percent_complete — planner-entered physical progress (0..1).
         Used when 0 < value ≤ 1.
      2. task.duration_percent_complete — P6 duration-based progress (0..1).
         Used when 0 < value ≤ 1.
      3. Linear elapsed fallback — uses working days when *cal* is provided,
         calendar days otherwise.  Affects only the 9 IBS tasks with no P6 pct.

    Snapshot semantics: percent-complete values are today's snapshot from the
    last P6 export and are used for all d ≥ actual_start, producing a
    step-function in historical series.  The KPI scalar at d=today is accurate.

    When actual_start is missing and status="complete", EV is gated at planned
    end_date so EV stays 0 before that date.
    """
    if task.actual_start:
        if task.actual_start > d:
            return 0.0
        if task.status == "complete" or (task.actual_end and task.actual_end <= d):
            return 1.0

        # In-progress: prefer real P6 % complete over linear elapsed
        phys = task.physical_percent_complete
        if phys is not None and phys > 0:
            return float(min(phys, 1.0))

        dur_pct = task.duration_percent_complete
        if dur_pct is not None and dur_pct > 0:
            return float(min(dur_pct, 1.0))

        # Linear elapsed fallback (CSV imports and tasks with no P6 pct data)
        if cal is not None:
            dur = max(working_day_diff(task.actual_start, task.end_date, cal), 1)
            return max(0.0, min(1.0, working_day_diff(task.actual_start, d, cal) / dur))
        dur = max((task.end_date - task.actual_start).days, 1)
        return max(0.0, min(1.0, (d - task.actual_start).days / dur))

    if task.status == "complete":
        # No actual dates: use start_date as the earliest defensible anchor.
        # end_date would return 0 when end_date > as_of (e.g. CSV imports with
        # pre-marked-complete tasks whose planned end is still in the future).
        return 1.0 if d >= task.start_date else 0.0
    return 0.0


_DELAY_BUCKETS = [
    {"label": "On Time", "min": 0, "max": 0, "color": "#16a34a"},
    {"label": "1–7 days", "min": 1, "max": 7, "color": "#84cc16"},
    {"label": "8–14 days", "min": 8, "max": 14, "color": "#d97706"},
    {"label": "15–30 days", "min": 15, "max": 30, "color": "#ea580c"},
    {"label": "31+ days", "min": 31, "max": None, "color": "#dc2626"},
]


def _task_delay_days(task, today: date, cal=None) -> int | None:
    """Working-day delay for *task* as of *today*.  None = not yet due (skip).

    Uses ``working_day_diff`` (half-open (d1, d2]) when *cal* is provided —
    the same function delay_rootcause uses — so both panels report identical
    figures.  Falls back to calendar-day ``.days`` when *cal* is None.
    """
    if task.start_date > today:
        return None
    if task.status == "complete":
        if task.actual_end:
            if cal:
                return max(0, working_day_diff(task.end_date, task.actual_end, cal))
            return max(0, (task.actual_end - task.end_date).days)
        return 0
    if today > task.end_date:
        if cal:
            return working_day_diff(task.end_date, today, cal)
        return (today - task.end_date).days
    if today > task.start_date and not task.actual_start:
        if cal:
            return working_day_diff(task.start_date, today, cal)
        return (today - task.start_date).days
    return 0


# ── Delay distribution ────────────────────────────────────────────────────────


def compute_delay_distribution(project_id: str) -> dict:
    """Distribution of tasks across delay buckets for the Delay Chart."""
    from castor.scheduling.models import Task

    today, _ = get_project_data_date(project_id)
    cal_map = load_project_calendars(project_id)
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
        delay = _task_delay_days(t, today, task_cal(t, cal_map) if cal_map else None)
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


# ── WBS heatmap ───────────────────────────────────────────────────────────────

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

    Uses equal-weight (task count) SPI — no cost data required.
    """
    from castor.scheduling.models import Task

    today, _ = get_project_data_date(project_id)
    cal_map = load_project_calendars(project_id)
    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return []

    task_cals = {str(t.pk): task_cal(t, cal_map) for t in tasks} if cal_map else {}

    groups: dict[str, list] = {}
    for t in tasks:
        groups.setdefault(t.stage or "", []).append(t)

    result = []
    for stage, stage_tasks in groups.items():
        n = len(stage_tasks)
        completed = sum(1 for t in stage_tasks if t.status == "complete")
        planned_sum = sum(_planned_pct_at(t, today, task_cals.get(str(t.pk))) for t in stage_tasks)
        earned_sum = sum(_earned_pct_at(t, today, task_cals.get(str(t.pk))) for t in stage_tasks)

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


# ── Main EVM computation ──────────────────────────────────────────────────────


def compute_evm(project_id: str, as_of_date: date | None = None) -> dict:
    """Compute EVM metrics and weekly S-curve series for *project_id*.

    Integrity contract
    ------------------
    AC, CPI, CV, EAC, VAC require real actual cost from P6ResourceAssignment.
    If no actual cost is imported these metrics are set to None in the response
    and ac_available=False.  The caller/UI must surface a clear notice.

    Returns a dict with:
        has_data           — bool
        bac                — float
        pv / ev            — float (None when no actuals recorded)
        ac                 — float | None  (None when actual cost absent)
        spi                — float (1.0 when no PV, so not fabricated)
        cpi / cv / eac / vac  — float | None  (None when ac absent)
        sv                 — float
        use_cost           — bool (False = duration proxy, not monetary EVM)
        cost_basis         — str
        cost_coverage_pct  — float (% tasks with planned cost)
        ac_available       — bool
        ac_coverage_pct    — float | None (% cost-bearing tasks with actual cost)
        ac_disabled_reason — str | None
        as_of / project_start / project_end — ISO date strings
        series             — {pv, ev, ac}  (ac absent when not available)
    """
    from castor.scheduling.models import Task

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    today = as_of_date or get_project_data_date(project_id)[0]
    total_tasks = len(tasks)

    # Per-task calendar for working-day span calculations (PV, EV fallback)
    cal_map = load_project_calendars(project_id)
    task_cals: dict[str, object] = (
        {str(t.pk): task_cal(t, cal_map) for t in tasks} if cal_map else {}
    )

    # ── Planned-value basis: schedule costs → QTO → duration proxy ────────
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
    tasks_with_cost = sum(1 for v in values.values() if v > 0)
    cost_coverage_pct = round(tasks_with_cost / total_tasks * 100, 1) if total_tasks else 0.0

    if use_cost:
        bac = total_value
        if total_sched > 0 and used_qto:
            cost_basis = "schedule costs + QTO estimates"
        elif total_sched > 0:
            cost_basis = "schedule costs"
        else:
            cost_basis = "QTO estimates"
    else:
        # Duration proxy — not monetary EVM; caller must surface this clearly
        values = {str(t.pk): float(max((t.end_date - t.start_date).days + 1, 1)) for t in tasks}
        bac = sum(values.values())
        cost_basis = "task durations"

    # ── Real actual costs from P6ResourceAssignment ───────────────────────
    task_pks = [str(t.pk) for t in tasks]
    actual_costs = _load_actual_costs(task_pks)
    ac_total = sum(actual_costs.values())
    ac_available = ac_total > 0

    if ac_available:
        tasks_with_actual = len(actual_costs)
        tasks_with_cost_n = max(tasks_with_cost, 1)
        ac_coverage_pct: float | None = round(tasks_with_actual / tasks_with_cost_n * 100, 1)
        ac_disabled_reason: str | None = None
    else:
        ac_coverage_pct = None
        ac_disabled_reason = (
            "Actual cost not imported — CPI, CV, EAC, VAC disabled. "
            "Import resource assignments with actual cost to enable cost-performance metrics."
        )

    project_start = min(t.start_date for t in tasks)
    project_end = max(t.end_date for t in tasks)

    # ── Date spine ────────────────────────────────────────────────────────
    spine_end = max(project_end, today)
    dates: list[date] = []
    cur = project_start
    while cur <= spine_end:
        dates.append(cur)
        cur += timedelta(weeks=1)
    if spine_end not in dates:
        dates.append(spine_end)
    dates.sort()

    # ── AC time series (real, cumulative by task completion date) ─────────
    # Each task's actual_cost is attributed to:
    #   - actual_end date (completed tasks)
    #   - today (active tasks that have started on or before as-of)
    # Tasks with actual_start > today are excluded — mirrors the EV gate in
    # _earned_pct_at (which returns 0 when actual_start > d).  A task whose
    # start date was recorded beyond the P6 DataDate cannot have real costs
    # as-of that date.
    ac_attribution: list[tuple[date, float]] = []
    if ac_available:
        for t in tasks:
            ta = actual_costs.get(str(t.pk), 0.0)
            if ta <= 0.0:
                continue
            if t.status == "complete" and t.actual_end:
                ac_attribution.append((t.actual_end, ta))
            elif t.actual_start and t.actual_start <= today:
                ac_attribution.append((today, ta))
        ac_attribution.sort()

    # ── Build series ──────────────────────────────────────────────────────
    pv_series: list[dict] = []
    ev_series: list[dict] = []
    ac_series: list[dict] = []

    ac_idx = 0
    cum_ac = 0.0

    for d in dates:
        pv_abs = sum(
            _planned_pct_at(t, d, task_cals.get(str(t.pk))) * values[str(t.pk)] for t in tasks
        )
        pv_series.append({"date": d.isoformat(), "pct": round(pv_abs / bac * 100, 2) if bac else 0})

        if d <= today:
            ev_abs = sum(
                _earned_pct_at(t, d, task_cals.get(str(t.pk))) * values[str(t.pk)] for t in tasks
            )
            ev_series.append(
                {"date": d.isoformat(), "pct": round(ev_abs / bac * 100, 2) if bac else 0}
            )

            if ac_available:
                while ac_idx < len(ac_attribution) and ac_attribution[ac_idx][0] <= d:
                    cum_ac += ac_attribution[ac_idx][1]
                    ac_idx += 1
                ac_series.append(
                    {"date": d.isoformat(), "pct": round(cum_ac / bac * 100, 2) if bac else 0}
                )

    # ── As-of KPI scalars ─────────────────────────────────────────────────
    pv_today = sum(
        _planned_pct_at(t, today, task_cals.get(str(t.pk))) * values[str(t.pk)] for t in tasks
    )
    ev_today = sum(
        _earned_pct_at(t, today, task_cals.get(str(t.pk))) * values[str(t.pk)] for t in tasks
    )

    spi = round(ev_today / pv_today, 3) if pv_today > 0 else 1.0
    sv = round(ev_today - pv_today, 2)

    if ac_available:
        # Gate on actual_start <= today — same rule as _earned_pct_at.
        # ac_available stays on the raw sum (intent: "is any AC imported?").
        ac_today: float | None = sum(
            ta
            for t in tasks
            if (ta := actual_costs.get(str(t.pk), 0.0)) > 0
            and not (t.actual_start and t.actual_start > today)
        )
        cpi: float | None = round(ev_today / ac_today, 3) if ac_today > 0 else 1.0
        cv: float | None = round(ev_today - ac_today, 2)
        eac: float | None = round(bac / cpi, 2) if (cpi and cpi > 0) else bac
        vac: float | None = round(bac - eac, 2) if eac is not None else None
    else:
        ac_today = None
        cpi = None
        cv = None
        eac = None
        vac = None

    logger.info(
        "EVM — project %s: BAC=%.0f SPI=%.2f CPI=%s EAC=%s ac_available=%s cost_coverage=%.0f%%",
        project_id,
        bac,
        spi,
        f"{cpi:.2f}" if cpi is not None else "N/A",
        f"{eac:.0f}" if eac is not None else "N/A",
        ac_available,
        cost_coverage_pct,
    )

    series: dict = {"pv": pv_series, "ev": ev_series}
    if ac_available:
        series["ac"] = ac_series

    # Count active tasks past their planned end that have no real % data and are
    # therefore credited 100% EV by the linear clamp.  The UI surfaces a warning
    # when this count is > 0 because SPI will be overstated.
    overdue_linear_capped = sum(
        1
        for t in tasks
        if t.actual_start
        and t.actual_start <= today
        and t.end_date < today
        and t.status != "complete"
        and not (t.actual_end and t.actual_end <= today)
        and t.physical_percent_complete is None
        and t.duration_percent_complete is None
    )

    return {
        "has_data": True,
        "bac": round(bac, 2),
        "pv": round(pv_today, 2),
        "ev": round(ev_today, 2),
        "ac": round(ac_today, 2) if ac_today is not None else None,
        "spi": spi,
        "cpi": cpi,
        "eac": eac,
        "vac": vac,
        "sv": sv,
        "cv": cv,
        "use_cost": use_cost,
        "cost_basis": cost_basis,
        "cost_coverage_pct": cost_coverage_pct,
        "ac_available": ac_available,
        "ac_coverage_pct": ac_coverage_pct,
        "ac_disabled_reason": ac_disabled_reason,
        "overdue_linear_capped": overdue_linear_capped,
        "as_of": today.isoformat(),
        "project_start": project_start.isoformat(),
        "project_end": project_end.isoformat(),
        "series": series,
    }
