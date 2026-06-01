# islam/scheduling/services/cashflow.py
"""Monthly Cash Flow Forecast.

Distributes P6ResourceAssignment costs (planned / actual / remaining) into
calendar-month buckets using linear time-proportional allocation, then builds
cumulative S-curves and key financing metrics.

Cost sources (in priority order):
  1. P6ResourceAssignment.planned_cost / actual_cost / remaining_cost — imported
     from Primavera P6 XML; authoritative for projects with resource data.
  2. Task.cost fallback — used when no resource assignments exist.
     Planned cost only.  Actual cost is NOT synthesised for in-progress tasks —
     it is left as zero to avoid fabricating spend figures the data does not support.
     Completed tasks get actual = planned (task is closed; full cost assumed spent).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _next_month(d: date) -> date:
    """First day of the month after *d*."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _distribute_monthly(
    monthly: dict[str, float],
    cost: float,
    start: date,
    end: date,
) -> None:
    """Add *cost* distributed linearly over [start, end] into *monthly* in-place."""
    if cost == 0 or end < start:
        return
    total_days = (end - start).days + 1
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = _next_month(cur)
        month_end = nxt - timedelta(days=1)
        overlap_start = max(start, cur)
        overlap_end = min(end, month_end)
        if overlap_start <= overlap_end:
            overlap_days = (overlap_end - overlap_start).days + 1
            monthly[_month_key(cur)] = (
                monthly.get(_month_key(cur), 0.0) + cost * overlap_days / total_days
            )
        cur = nxt


def _build_spine(
    planned: dict[str, float],
    actual: dict[str, float],
    forecast: dict[str, float],
) -> list[str]:
    """Sorted month-key spine covering all three series."""
    all_keys = set(planned) | set(actual) | set(forecast)
    return sorted(all_keys)


# ── Public API ────────────────────────────────────────────────────────────────


def compute_cashflow(project_id: str) -> dict:
    """Compute monthly cash flow for *project_id*.

    Returns:
        has_data        — bool
        months          — list[dict] one per calendar month
        metrics         — dict with BAC, AC, remaining, peak month, burn rates
        source          — "p6_assignments" | "task_cost"
        as_of           — ISO date string
    """
    from django.db.models import Sum

    from islam.scheduling.models import P6ResourceAssignment, Task

    today = date.today()

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    task_map = {str(t.pk): t for t in tasks}

    # ── Cost source: P6 assignments ───────────────────────────────────────
    ra_qs = (
        P6ResourceAssignment.objects.filter(project_id=project_id, is_pending=False)
        .values("task_id")
        .annotate(
            planned=Sum("planned_cost"),
            actual=Sum("actual_cost"),
            remaining=Sum("remaining_cost"),
        )
    )

    task_costs: dict[str, dict[str, float]] = {}
    for row in ra_qs:
        pk = str(row["task_id"])
        if pk not in task_map:
            continue
        task_costs[pk] = {
            "planned": float(row["planned"] or 0),
            "actual": float(row["actual"] or 0),
            "remaining": float(row["remaining"] or 0),
        }

    source = "p6_assignments"

    # ── Fallback: Task.cost ───────────────────────────────────────────────
    if not task_costs:
        source = "task_cost"
        for t in tasks:
            pc = float(t.cost or 0)
            if pc == 0:
                continue
            # Actual cost: real value for complete tasks; zero for all others —
            # never synthesised from planned × elapsed (A10 integrity rule).
            if t.status == "complete":
                ac, rc = pc, 0.0
            else:
                ac, rc = 0.0, pc
            task_costs[str(t.pk)] = {"planned": pc, "actual": ac, "remaining": rc}

    if not task_costs:
        return {"has_data": False}

    # ── Build monthly buckets ─────────────────────────────────────────────
    planned_m: dict[str, float] = {}
    actual_m: dict[str, float] = {}
    forecast_m: dict[str, float] = {}

    for pk, costs in task_costs.items():
        task = task_map[pk]

        # 1. Planned — distribute over planned start → end
        _distribute_monthly(planned_m, costs["planned"], task.start_date, task.end_date)

        # 2. Actual — distribute over actual date range
        if costs["actual"] > 0:
            if task.actual_start:
                actual_end = task.actual_end if task.actual_end else today
                # Guard inverted dates from P6 data quality issues
                if actual_end < task.actual_start:
                    actual_end = task.actual_start
                _distribute_monthly(actual_m, costs["actual"], task.actual_start, actual_end)
            else:
                # No actual dates — allocate to planned date range up to today
                end_cap = min(task.end_date, today)
                if task.start_date <= end_cap:
                    _distribute_monthly(actual_m, costs["actual"], task.start_date, end_cap)

        # 3. Forecast — remaining cost from today forward via CPM dates
        if costs["remaining"] > 0 and task.status != "complete":
            fc_start = task.early_start or task.start_date
            fc_end = task.early_finish or task.end_date
            # Forecast window starts from max(today, schedule start)
            fc_start = max(fc_start, today)
            if fc_start <= fc_end:
                _distribute_monthly(forecast_m, costs["remaining"], fc_start, fc_end)

    # ── Build spine and month series ──────────────────────────────────────
    spine = _build_spine(planned_m, actual_m, forecast_m)
    if not spine:
        return {"has_data": False}

    cum_planned = 0.0
    cum_actual = 0.0
    cum_progress = 0.0  # actual to date then actual+forecast going forward
    today_key = _month_key(today)

    month_rows = []
    for mk in spine:
        p = round(planned_m.get(mk, 0.0), 2)
        a = round(actual_m.get(mk, 0.0), 2)
        f = round(forecast_m.get(mk, 0.0), 2)

        cum_planned += p
        cum_actual += a

        if mk <= today_key:
            cum_progress += a
        else:
            cum_progress += f

        month_rows.append(
            {
                "month": mk,
                "planned": p,
                "actual": a,
                "forecast": f,
                "cumulative_planned": round(cum_planned, 2),
                "cumulative_actual": round(cum_actual, 2),
                "cumulative_progress": round(cum_progress, 2),
            }
        )

    # ── Key metrics ───────────────────────────────────────────────────────
    bac = sum(c["planned"] for c in task_costs.values())
    ac_total = sum(c["actual"] for c in task_costs.values())
    remaining_total = sum(c["remaining"] for c in task_costs.values())

    # Peak month (highest planned spend)
    peak_month = max(planned_m, key=lambda k: planned_m[k]) if planned_m else None
    peak_value = round(planned_m[peak_month], 2) if peak_month else 0.0

    # Burn rate: average monthly spend over the last 3 completed months
    past_keys = sorted(k for k in spine if k < today_key)[-3:]
    burn_planned = round(sum(planned_m.get(k, 0) for k in past_keys) / max(len(past_keys), 1), 2)
    burn_actual = round(sum(actual_m.get(k, 0) for k in past_keys) / max(len(past_keys), 1), 2)

    logger.info(
        "Cashflow — project %s: BAC=%.0f AC=%.0f remaining=%.0f peak=%s source=%s",
        project_id,
        bac,
        ac_total,
        remaining_total,
        peak_month,
        source,
    )

    return {
        "has_data": True,
        "months": month_rows,
        "metrics": {
            "bac": round(bac, 2),
            "ac": round(ac_total, 2),
            "remaining": round(remaining_total, 2),
            "peak_month": peak_month,
            "peak_value": peak_value,
            "burn_rate_planned": burn_planned,
            "burn_rate_actual": burn_actual,
            "spent_pct": round(ac_total / bac * 100, 1) if bac else 0.0,
        },
        "source": source,
        "as_of": today.isoformat(),
    }
