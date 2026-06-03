# castor/scheduling/services/critical_path.py
"""Critical Path Method (CPM) — calendar-aware forward/backward pass.

Enhancements over the original implementation:

* Working-day arithmetic — durations are counted in working days using the
  calendar assigned to each activity (P6Calendar).  The default calendar
  treats every day as a working day so behaviour is unchanged when no P6
  calendar data is available.

* SNET constraints (Start On or After) — the earliest start date from the
  network logic is clamped to the constraint date (advanced to the next
  working day on the activity's calendar if needed).

* MFO / Mandatory Finish constraints — the latest finish from the backward
  pass is capped at the constraint date.  This may produce negative float
  for activities whose planned finish already exceeds the deadline.

* Total float is now stored as a signed integer: negative values indicate
  a mandatory-finish overrun and are more useful to the planner than a
  clamped zero.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Calendar arithmetic — imported from shared utilities ──────────────────────
# All calendar helpers live in calendar_utils.py so delay_rootcause, anomaly_detect,
# and dcma_check can share them without importing critical_path.
from .calendar_utils import (  # noqa: E402
    _DEFAULT_CAL,
    _add_working_days,
    _count_working_days,
    _load_project_calendars,
    _next_working_day,
    _prev_working_day,
    _subtract_working_days,
)

# ── Duration helper ───────────────────────────────────────────────────────────


def _duration(task, cal: dict) -> int:
    """Duration in working days on *cal* between task.start_date and task.end_date."""
    return _count_working_days(task.start_date, task.end_date, cal)


# ── Public entry point ────────────────────────────────────────────────────────


def compute_critical_path(project_id: str) -> dict:
    """Run CPM for all physical tasks in *project_id* and persist results.

    Returns
    -------
    dict with keys:
        critical_task_ids  — list[str] UUIDs of critical tasks
        project_duration   — int total calendar days
        task_data          — {str(task_id): {early_start, early_finish,
                              late_start, late_finish, total_float, is_critical}}
    """
    from castor.scheduling.models import Task, TaskDependency

    from .utils import get_project_data_date

    data_date, _ = get_project_data_date(project_id)

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .order_by("start_date")
    )
    if not tasks:
        return {"critical_task_ids": [], "project_duration": 0, "task_data": {}}

    # ── Build calendar lookup ────────────────────────────────────────────
    cal_defs = _load_project_calendars(project_id)
    task_cal: dict[str, dict] = {}
    for t in tasks:
        cal_id = getattr(t, "calendar_object_id", "") or ""
        task_cal[str(t.pk)] = cal_defs.get(cal_id, _DEFAULT_CAL)

    task_map: dict[str, Task] = {str(t.pk): t for t in tasks}
    task_ids = set(task_map)

    deps = list(
        TaskDependency.objects.filter(
            predecessor__project_id=project_id,
            successor__project_id=project_id,
        ).values("predecessor_id", "successor_id", "dep_type", "lag_days")
    )

    # Adjacency lists for topological sort
    pred_of: dict[str, list[dict]] = defaultdict(list)
    succ_of: dict[str, list[dict]] = defaultdict(list)
    for d in deps:
        pid, sid = str(d["predecessor_id"]), str(d["successor_id"])
        if pid in task_ids and sid in task_ids:
            pred_of[sid].append(d)
            succ_of[pid].append(d)

    # Kahn's algorithm topological sort
    in_degree: dict[str, int] = {tid: len(pred_of[tid]) for tid in task_ids}
    queue: deque[str] = deque(t for t, d in in_degree.items() if d == 0)
    topo: list[str] = []
    while queue:
        tid = queue.popleft()
        topo.append(tid)
        for d in succ_of[tid]:
            sid = str(d["successor_id"])
            in_degree[sid] -= 1
            if in_degree[sid] == 0:
                queue.append(sid)

    if len(topo) < len(task_ids):
        remaining = [tid for tid, d in in_degree.items() if d > 0]
        logger.warning("CPM: cycle detected for project %s; falling back to date order", project_id)
        topo.extend(sorted(remaining, key=lambda tid: task_map[tid].start_date))

    # ------------------------------------------------------------------
    # Forward pass — progress-aware early_start / early_finish
    #
    # Three cases, processed in topological order so predecessors are
    # always resolved before successors:
    #
    #   COMPLETE   → anchor at actual dates; successor sees actual_end as
    #                the predecessor finish — no phantom delay propagation.
    #   IN-PROGRESS → anchor early_start at actual_start; early_finish at
    #                 data_date + remaining (earned-pct priority: physical →
    #                 duration_pct → linear elapsed).
    #   NOT-STARTED → standard CPM forward pass from predecessor early_finish
    #                 dates, floored at data_date so nothing is placed in the
    #                 past relative to the status snapshot.
    # ------------------------------------------------------------------
    project_start = min(t.start_date for t in tasks)

    early_start: dict[str, date] = {}
    early_finish: dict[str, date] = {}

    for tid in topo:
        task = task_map[tid]
        cal = task_cal[tid]

        # ── COMPLETE: fix at actual dates ─────────────────────────────
        if task.status == "complete" and task.actual_end:
            early_start[tid] = task.actual_start or task.start_date
            early_finish[tid] = task.actual_end
            continue

        # ── IN-PROGRESS: anchor start; finish = data_date + remaining ─
        if task.actual_start and not task.actual_end:
            dur = _duration(task, cal)
            phys = task.physical_percent_complete
            dur_pct = task.duration_percent_complete
            if phys is not None and float(phys) > 0:
                pct_done = min(float(phys), 1.0)
            elif dur_pct is not None and float(dur_pct) > 0:
                pct_done = min(float(dur_pct), 1.0)
            else:
                elapsed = _count_working_days(task.actual_start, data_date, cal)
                pct_done = min(elapsed / max(dur, 1), 1.0)
            remaining = max(round(dur * (1.0 - pct_done)), 1)
            early_start[tid] = task.actual_start
            early_finish[tid] = _add_working_days(data_date, remaining - 1, cal)
            continue

        # ── NOT-STARTED: standard forward pass, floored at data_date ──
        dur = _duration(task, cal)

        if not pred_of[tid]:
            es = _next_working_day(task.start_date, cal)
        else:
            es = project_start
            for d in pred_of[tid]:
                pid = str(d["predecessor_id"])
                lag = d["lag_days"] or 0
                dt = d["dep_type"]
                pef = early_finish.get(pid, task_map[pid].end_date)
                pes = early_start.get(pid, task_map[pid].start_date)
                succ_cal = cal  # successor's calendar governs next-working-day

                if dt == "FS":
                    candidate = _next_working_day(pef + timedelta(days=1 + lag), succ_cal)
                elif dt == "SS":
                    candidate = _next_working_day(pes + timedelta(days=lag), succ_cal)
                elif dt == "FF":
                    ef_raw = pef + timedelta(days=lag)
                    candidate = _subtract_working_days(ef_raw, dur - 1, succ_cal)
                else:  # SF
                    ef_raw = pes + timedelta(days=lag)
                    candidate = _subtract_working_days(ef_raw, dur - 1, succ_cal)

                if candidate > es:
                    es = candidate

        # Floor at data_date: nothing can start before the status snapshot.
        es = max(es, data_date)

        # Apply SNET constraint: activity cannot start before constraint_date.
        constraint_type = getattr(task, "constraint_type", "") or ""
        constraint_date = getattr(task, "constraint_date", None)
        if constraint_date and constraint_type in (
            "Start On or After",
            "Mandatory Start",
            "Start On",
        ):
            snet = _next_working_day(constraint_date, cal)
            if snet > es:
                es = snet

        early_start[tid] = es
        early_finish[tid] = _add_working_days(es, dur - 1, cal)

    project_end = max(early_finish.values())
    project_duration = (project_end - project_start).days + 1

    # ------------------------------------------------------------------
    # Backward pass — late_finish / late_start (working-day arithmetic)
    #
    # COMPLETE tasks are anchored symmetrically: late_start = actual_start,
    # late_finish = actual_end.  They are already done; the backward pass
    # cannot ask them to have started "later."
    # ------------------------------------------------------------------
    late_finish: dict[str, date] = {}
    late_start: dict[str, date] = {}

    for tid in reversed(topo):
        task = task_map[tid]
        cal = task_cal[tid]

        # ── COMPLETE: anchor at actual dates ──────────────────────────
        if task.status == "complete" and task.actual_end:
            late_finish[tid] = task.actual_end
            late_start[tid] = task.actual_start or task.start_date
            continue

        # ── IN-PROGRESS: late_start is actual_start (already running) ─
        if task.actual_start and not task.actual_end:
            late_finish[tid] = early_finish[tid]  # cannot finish later than CPM says
            late_start[tid] = task.actual_start
            continue

        # ── NOT-STARTED: standard backward pass ───────────────────────
        dur = _duration(task, cal)

        if not succ_of[tid]:
            lf = project_end
        else:
            lf = project_end
            for d in succ_of[tid]:
                sid = str(d["successor_id"])
                lag = d["lag_days"] or 0
                dt = d["dep_type"]
                sls = late_start.get(sid, task_map[sid].start_date)
                slf = late_finish.get(sid, task_map[sid].end_date)
                pred_cal = cal  # predecessor's calendar governs prev-working-day

                if dt == "FS":
                    candidate = _prev_working_day(sls - timedelta(days=1 + lag), pred_cal)
                elif dt == "SS":
                    candidate = _add_working_days(
                        _prev_working_day(sls - timedelta(days=lag), pred_cal), dur - 1, pred_cal
                    )
                elif dt == "FF":
                    candidate = slf - timedelta(days=lag)
                else:  # SF
                    candidate = _add_working_days(sls - timedelta(days=lag), dur - 1, pred_cal)

                if candidate < lf:
                    lf = candidate

        # Apply MFO constraint: hard deadline — cap late_finish regardless of network.
        constraint_type = getattr(task, "constraint_type", "") or ""
        constraint_date = getattr(task, "constraint_date", None)
        if constraint_date and constraint_type in (
            "Mandatory Finish",
            "Finish On",
            "Finish On or Before",
        ):
            mfo = _prev_working_day(constraint_date, cal)
            if mfo < lf:
                lf = mfo

        late_finish[tid] = lf
        late_start[tid] = _subtract_working_days(lf, dur - 1, cal)

    # ------------------------------------------------------------------
    # Float + is_critical  (signed — negative = overrun vs MFO deadline)
    #
    # Completed tasks always get is_critical=False — they cannot delay the
    # project further and should not inflate the critical-path count or
    # affect DCMA / WBS risk scoring.  Their float value is set to 0
    # (they finished; no schedule slack is meaningful for them).
    # ------------------------------------------------------------------
    total_float: dict[str, int] = {}
    is_critical: dict[str, bool] = {}
    for tid in task_ids:
        task = task_map[tid]
        if task.status == "complete":
            total_float[tid] = 0
            is_critical[tid] = False
        else:
            tf = (late_start[tid] - early_start[tid]).days
            total_float[tid] = tf  # signed: negative means MFO overrun
            is_critical[tid] = tf <= 0

    # ------------------------------------------------------------------
    # Persist to DB
    # ------------------------------------------------------------------
    with transaction.atomic():
        for tid, task in task_map.items():
            Task.objects.filter(pk=tid).update(
                early_start=early_start[tid],
                early_finish=early_finish[tid],
                late_start=late_start[tid],
                late_finish=late_finish[tid],
                total_float=total_float[tid],
                is_critical=is_critical[tid],
            )

    critical_ids = [tid for tid, crit in is_critical.items() if crit]

    task_data = {
        tid: {
            "early_start": early_start[tid].isoformat(),
            "early_finish": early_finish[tid].isoformat(),
            "late_start": late_start[tid].isoformat(),
            "late_finish": late_finish[tid].isoformat(),
            "total_float": total_float[tid],
            "is_critical": is_critical[tid],
        }
        for tid in task_ids
    }

    logger.info(
        "CPM complete — project %s: %d tasks, %d critical, %d calendar days, "
        "%d calendars loaded, %d constrained",
        project_id,
        len(tasks),
        len(critical_ids),
        project_duration,
        len(cal_defs),
        sum(
            1
            for t in tasks
            if getattr(t, "constraint_type", "") and getattr(t, "constraint_date", None)
        ),
    )

    return {
        "critical_task_ids": critical_ids,
        "project_duration": project_duration,
        "task_data": task_data,
    }
