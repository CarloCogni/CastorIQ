# islam/scheduling/services/critical_path.py
"""Critical Path Method (CPM) — forward/backward pass over TaskDependency graph."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _duration(task) -> int:
    """Calendar days inclusive."""
    return max((task.end_date - task.start_date).days + 1, 1)


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
    from islam.scheduling.models import Task, TaskDependency

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .order_by("start_date")
    )
    if not tasks:
        return {"critical_task_ids": [], "project_duration": 0, "task_data": {}}

    task_map: dict[str, Task] = {str(t.pk): t for t in tasks}
    task_ids = set(task_map)

    deps = list(
        TaskDependency.objects.filter(
            predecessor__project_id=project_id,
            successor__project_id=project_id,
        ).values("predecessor_id", "successor_id", "dep_type", "lag_days")
    )

    # Build adjacency for topological sort (successor_id → [predecessor_id])
    pred_of: dict[str, list[dict]] = defaultdict(list)
    succ_of: dict[str, list[dict]] = defaultdict(list)
    for d in deps:
        pid, sid = str(d["predecessor_id"]), str(d["successor_id"])
        if pid in task_ids and sid in task_ids:
            pred_of[sid].append(d)
            succ_of[pid].append(d)

    # Kahn's algorithm topological sort
    in_degree: dict[str, int] = {tid: 0 for tid in task_ids}
    for tid in task_ids:
        in_degree[tid] = len(pred_of[tid])

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

    # If there's a cycle (in_degree > 0 remaining), fall back to date-ordered list
    remaining = [tid for tid, d in in_degree.items() if d > 0]
    if remaining:
        logger.warning("CPM: cycle detected for project %s; falling back to date order", project_id)
        topo.extend(sorted(remaining, key=lambda tid: task_map[tid].start_date))

    # ------------------------------------------------------------------
    # Forward pass — compute early_start / early_finish
    # ------------------------------------------------------------------
    project_start = min(t.start_date for t in tasks)

    early_start: dict[str, date] = {}
    early_finish: dict[str, date] = {}

    for tid in topo:
        task = task_map[tid]
        dur = _duration(task)

        if not pred_of[tid]:
            es = task.start_date
        else:
            es = project_start
            for d in pred_of[tid]:
                pid = str(d["predecessor_id"])
                lag = d["lag_days"]
                dt = d["dep_type"]
                pef = early_finish.get(pid, task_map[pid].end_date)
                pes = early_start.get(pid, task_map[pid].start_date)
                if dt == "FS":
                    candidate = pef + timedelta(days=1 + lag)
                elif dt == "SS":
                    candidate = pes + timedelta(days=lag)
                elif dt == "FF":
                    candidate = pef + timedelta(days=lag) - timedelta(days=dur - 1)
                else:  # SF
                    candidate = pes + timedelta(days=lag) - timedelta(days=dur - 1)
                if candidate > es:
                    es = candidate

        early_start[tid] = es
        early_finish[tid] = es + timedelta(days=dur - 1)

    project_end = max(early_finish.values())
    project_duration = (project_end - project_start).days + 1

    # ------------------------------------------------------------------
    # Backward pass — compute late_finish / late_start
    # ------------------------------------------------------------------
    late_finish: dict[str, date] = {}
    late_start: dict[str, date] = {}

    for tid in reversed(topo):
        task = task_map[tid]
        dur = _duration(task)

        if not succ_of[tid]:
            lf = project_end
        else:
            lf = project_end
            for d in succ_of[tid]:
                sid = str(d["successor_id"])
                lag = d["lag_days"]
                dt = d["dep_type"]
                sls = late_start.get(sid, task_map[sid].start_date)
                slf = late_finish.get(sid, task_map[sid].end_date)
                if dt == "FS":
                    candidate = sls - timedelta(days=1 + lag)
                elif dt == "SS":
                    candidate = sls - timedelta(days=lag) + timedelta(days=dur - 1)
                elif dt == "FF":
                    candidate = slf - timedelta(days=lag)
                else:  # SF
                    candidate = slf - timedelta(days=lag) + timedelta(days=dur - 1)
                if candidate < lf:
                    lf = candidate

        late_finish[tid] = lf
        late_start[tid] = lf - timedelta(days=dur - 1)

    # ------------------------------------------------------------------
    # Float + is_critical
    # ------------------------------------------------------------------
    total_float: dict[str, int] = {}
    is_critical: dict[str, bool] = {}
    for tid in task_ids:
        tf = (late_start[tid] - early_start[tid]).days
        total_float[tid] = max(tf, 0)
        is_critical[tid] = total_float[tid] == 0

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
        "CPM complete — project %s: %d tasks, %d critical, %d days",
        project_id,
        len(tasks),
        len(critical_ids),
        project_duration,
    )

    return {
        "critical_task_ids": critical_ids,
        "project_duration": project_duration,
        "task_data": task_data,
    }
