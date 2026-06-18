# castor/scheduling/services/anomaly_detect.py
"""Anomaly detection — single-snapshot rule + robust-statistics outliers.

Detects stalls (elapsed ≥ 2× planned), cross-sectional MAD outliers within CSI trade
peer groups (planned_duration / total_float / finish_variance axes), and structural
logic errors (mid-network orphans, FS violations, circular dependencies).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from datetime import date, timedelta

import numpy as np

from .utils import get_project_data_date

logger = logging.getLogger(__name__)

_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")
_CSI_NAMES: dict[str, str] = {
    "01": "General Requirements",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "07": "Thermal/Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "13": "Special Constr.",
    "14": "Conveying",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety",
    "31": "Earthwork",
}

_MIN_PEER_N: int = 5  # minimum peer group size for cross-sectional stats
_OUTLIER_Z: float = 5.0  # robust z-score threshold — 5σ to cut noise on delayed projects
_STALL_MIN_RATIO: float = 2.0  # only flag when elapsed >= 2× planned duration
_MIN_PEER_DURATION_DAYS: int = 3  # exclude sub-3-day stubs from cross-sectional peer groups
_MAX_PEER_DURATION_DAYS: int = 365  # exclude multi-year hammock tasks from peer groups
_FS_MIN_VIOLATION_DAYS: int = 90  # ignore FS violations smaller than 90 calendar days
_START_WINDOW_DAYS: int = 7  # tasks within 7 days of project start are legitimate starts
# CSI construction trade codes (03-33, excluding 01 General Requirements and XX/unknown)
_CONSTRUCTION_CSI: frozenset[str] = frozenset(
    {
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09",
        "10",
        "11",
        "12",
        "13",
        "14",
        "21",
        "22",
        "23",
        "24",
        "25",
        "26",
        "27",
        "28",
        "29",
        "31",
        "32",
        "33",
    }
)
# Planned durations ≤ this many days on a real construction task = baseline data quality issue.
# unrealistic plans, not blocked tasks. 5d+ is considered a plausible planned duration.
_UNREALISTIC_DURATION_MAX_DAYS: int = 4


def _csi(activity_code: str) -> str:
    """Extract two-digit CSI division from activity_code."""
    m = _CSI_RE.search(activity_code or "")
    return m.group(1) if m else "XX"


def _trade_name(csi: str) -> str:
    return _CSI_NAMES.get(csi, f"Div {csi}")


def _robust_z(value: float, median: float, mad: float) -> float:
    """Robust z-score: |x − median| / (MAD × 1.4826).

    1.4826 is the consistency factor making MAD equivalent to σ for Gaussian data.
    Returns 0 when MAD is zero (constant peer group — no outlier possible).
    """
    if mad == 0.0:
        return 0.0
    return abs(value - median) / (mad * 1.4826)


def _classify_stall_anomalies(
    tasks: list, data_date: date, cal_map: dict | None = None
) -> tuple[list[dict], list[dict]]:
    """Split started+incomplete overrunning tasks into two honestly-named buckets.

    running_long:
        planned_duration >= 5d and elapsed >= 2× planned.
        These are genuine execution problems — the plan was realistic
        but the task is taking far longer than expected.

    unrealistic_baseline:
        planned_duration <= 4d on a construction-trade activity (CSI 03–33).
        The extreme overrun ratio is driven by an unrealistically short baseline
        (1-4d for real construction work), not by the task being blocked.
        This is a DATA QUALITY issue with the schedule baseline.
        Verified: all 105 affected tasks have cost data and construction CSI codes;
        gray-zone 3-4d tasks showed identical patterns to the 1-2d group (190–250× overruns).
    """
    running_long: list[dict] = []
    unrealistic_baseline: list[dict] = []

    from .calendar_utils import task_cal, working_day_diff

    for t in tasks:
        if not t.actual_start or t.actual_end:
            continue
        if not t.start_date or not t.end_date:
            continue
        cal = task_cal(t, cal_map) if cal_map else None
        if cal is not None:
            planned = max(working_day_diff(t.start_date, t.end_date, cal), 1)
            elapsed = working_day_diff(t.actual_start, data_date, cal)
        else:
            planned = max((t.end_date - t.start_date).days, 1)
            elapsed = (data_date - t.actual_start).days
        ratio = elapsed / planned
        if ratio < _STALL_MIN_RATIO:
            continue
        overrun_ratio = round(ratio, 2)
        csi = _csi(t.activity_code or "")

        if planned <= _UNREALISTIC_DURATION_MAX_DAYS and csi in _CONSTRUCTION_CSI:
            unrealistic_baseline.append(
                {
                    "task_pk": str(t.pk),
                    "name": t.name,
                    "trade": _trade_name(csi),
                    "csi": csi,
                    "stage": t.stage or "unassigned",
                    "anomaly_type": "unrealistic_baseline",
                    "metric": "planned_duration_too_short",
                    "value": planned,
                    "peer_median": None,
                    "peer_mad": None,
                    "elapsed_days": elapsed,
                    "planned_duration_days": planned,
                    "overrun_ratio": overrun_ratio,
                    "actual_start": t.actual_start.isoformat(),
                    "planned_finish": t.end_date.isoformat(),
                    "explanation": (
                        f"Construction task planned as {planned}d"
                        f" — baseline duration unrealistic for this scope"
                        f" (started {elapsed}d ago, {overrun_ratio}× overrun)"
                    ),
                }
            )
        elif planned >= 5:
            running_long.append(
                {
                    "task_pk": str(t.pk),
                    "name": t.name,
                    "trade": _trade_name(csi),
                    "csi": csi,
                    "stage": t.stage or "unassigned",
                    "anomaly_type": "running_long",
                    "metric": "elapsed_vs_planned_duration",
                    "value": elapsed,
                    "peer_median": planned,
                    "peer_mad": None,
                    "elapsed_days": elapsed,
                    "planned_duration_days": planned,
                    "overrun_ratio": overrun_ratio,
                    "actual_start": t.actual_start.isoformat(),
                    "planned_finish": t.end_date.isoformat(),
                    "explanation": (
                        f"Started {elapsed}d ago, planned {planned}d"
                        f" — {overrun_ratio}× overrun, not complete"
                    ),
                }
            )
        # tasks with planned_duration 3-4d that are NOT construction CSI fall through
        # (non-construction 3-4d: too ambiguous to classify; not flagged)

    running_long.sort(key=lambda x: -x["overrun_ratio"])
    unrealistic_baseline.sort(key=lambda x: -x["overrun_ratio"])
    return running_long, unrealistic_baseline


def _detect_cross_sectional(
    tasks: list, override_map: dict | None = None, cal_map: dict | None = None
) -> list[dict]:
    """Flag tasks that are robust z-score outliers within their CSI trade peer group.

    Three axes, each producing separate flags when the threshold is exceeded:
      planned_duration  — all tasks with valid start/end dates
      total_float       — tasks where total_float is set
      finish_variance   — completed tasks, in working days
                          (positive = late, negative = early)
    """
    from .calendar_utils import task_cal, working_day_diff

    by_csi: dict[str, list] = defaultdict(list)
    for t in tasks:
        if t.start_date and t.end_date:
            cal = task_cal(t, cal_map) if cal_map else None
            dur = (
                working_day_diff(t.start_date, t.end_date, cal)
                if cal is not None
                else (t.end_date - t.start_date).days
            )
            if _MIN_PEER_DURATION_DAYS <= dur <= _MAX_PEER_DURATION_DAYS:
                effective_csi = (
                    override_map.get(str(t.pk)) or _csi(t.activity_code or "")
                    if override_map
                    else _csi(t.activity_code or "")
                )
                by_csi[effective_csi].append(t)

    results = []

    for csi, group in by_csi.items():
        if len(group) < _MIN_PEER_N:
            continue
        n_group = len(group)
        trade = _trade_name(csi)

        # Axis: planned_duration (working days)
        def _dur(t):
            cal = task_cal(t, cal_map) if cal_map else None
            return (
                working_day_diff(t.start_date, t.end_date, cal)
                if cal is not None
                else (t.end_date - t.start_date).days
            )

        durations = np.array([_dur(t) for t in group], dtype=float)
        dur_med = float(np.median(durations))
        dur_mad = float(np.median(np.abs(durations - dur_med)))
        for t, dur in zip(group, durations):
            z = _robust_z(float(dur), dur_med, dur_mad)
            if z >= _OUTLIER_Z:
                results.append(
                    {
                        "task_pk": str(t.pk),
                        "name": t.name,
                        "trade": trade,
                        "csi": csi,
                        "stage": t.stage or "unassigned",
                        "anomaly_type": "statistical_outlier",
                        "axis": "planned_duration",
                        "metric": "planned_duration",
                        "value": int(dur),
                        "peer_median": round(dur_med, 1),
                        "peer_mad": round(dur_mad, 1),
                        "peer_n": n_group,
                        "robust_z": round(z, 2),
                        "explanation": (
                            f"planned_duration={int(dur)}d vs peer median={round(dur_med, 1)}d"
                            f" (robust z={round(z, 2)}, n={n_group} peers in CSI {csi})"
                        ),
                    }
                )

        # Axis: total_float
        float_pairs = [(t, float(t.total_float)) for t in group if t.total_float is not None]
        if len(float_pairs) >= _MIN_PEER_N:
            float_vals = np.array([f for _, f in float_pairs], dtype=float)
            float_med = float(np.median(float_vals))
            float_mad = float(np.median(np.abs(float_vals - float_med)))
            for t, fval in float_pairs:
                z = _robust_z(fval, float_med, float_mad)
                if z >= _OUTLIER_Z:
                    results.append(
                        {
                            "task_pk": str(t.pk),
                            "name": t.name,
                            "trade": trade,
                            "csi": csi,
                            "stage": t.stage or "unassigned",
                            "anomaly_type": "statistical_outlier",
                            "axis": "total_float",
                            "metric": "total_float",
                            "value": int(fval),
                            "peer_median": round(float_med, 1),
                            "peer_mad": round(float_mad, 1),
                            "peer_n": len(float_pairs),
                            "robust_z": round(z, 2),
                            "explanation": (
                                f"total_float={int(fval):+d}d vs peer median={round(float_med, 1):+.1f}d"
                                f" (robust z={round(z, 2)}, n={len(float_pairs)} peers)"
                            ),
                        }
                    )

        # Axis: finish_variance (completed tasks, working days)
        def _fv(t):
            cal = task_cal(t, cal_map) if cal_map else None
            return (
                working_day_diff(t.end_date, t.actual_end, cal)
                if cal is not None
                else (t.actual_end - t.end_date).days
            )

        done_pairs = [(t, _fv(t)) for t in group if t.actual_end]
        if len(done_pairs) >= _MIN_PEER_N:
            fv_vals = np.array([v for _, v in done_pairs], dtype=float)
            fv_med = float(np.median(fv_vals))
            fv_mad = float(np.median(np.abs(fv_vals - fv_med)))
            for t, fv in done_pairs:
                z = _robust_z(float(fv), fv_med, fv_mad)
                if z >= _OUTLIER_Z:
                    results.append(
                        {
                            "task_pk": str(t.pk),
                            "name": t.name,
                            "trade": trade,
                            "csi": csi,
                            "stage": t.stage or "unassigned",
                            "anomaly_type": "statistical_outlier",
                            "axis": "finish_variance",
                            "metric": "finish_variance",
                            "value": int(fv),
                            "peer_median": round(fv_med, 1),
                            "peer_mad": round(fv_mad, 1),
                            "peer_n": len(done_pairs),
                            "robust_z": round(z, 2),
                            "explanation": (
                                f"finish_variance={int(fv):+d}d vs peer median={round(fv_med, 1):+.1f}d"
                                f" (robust z={round(z, 2)}, n={len(done_pairs)} completed peers)"
                            ),
                        }
                    )

    results.sort(key=lambda x: -x["robust_z"])
    return results


def _find_cycles_kahn(adj: dict[str, list[str]]) -> set[str]:
    """Return task PKs involved in cycles via Kahn's topological sort.

    Any node that cannot be removed (its in-degree never reaches zero) is part of a cycle.
    Iterative — safe for large projects without hitting Python's recursion limit.
    """
    in_degree: dict[str, int] = {pk: 0 for pk in adj}
    for nbrs in adj.values():
        for nbr in nbrs:
            if nbr in in_degree:
                in_degree[nbr] += 1

    queue: deque[str] = deque(pk for pk, d in in_degree.items() if d == 0)
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        visited.add(node)
        for nbr in adj.get(node, []):
            if nbr in in_degree:
                in_degree[nbr] -= 1
                if in_degree[nbr] == 0:
                    queue.append(nbr)
    return set(adj.keys()) - visited


def _detect_logic_anomalies(tasks: list, deps: list) -> list[dict]:
    """Detect structural schedule errors across three categories.

    3a. Mid-network orphans — tasks with successors but no predecessors that don't
        sit at the project start (they're floating, unconstrained mid-schedule).
    3b. FS violations       — FS-linked successors whose planned start_date is
        earlier than the predecessor's planned end_date (+ any lag).
    3c. Circular dependencies — tasks in dependency cycles (CPM unsolvable).
    """
    results: list[dict] = []
    task_map: dict[str, object] = {str(t.pk): t for t in tasks}

    has_predecessor: set[str] = set()
    has_successor: set[str] = set()
    successor_count: dict[str, int] = {}
    adj: dict[str, list[str]] = {str(t.pk): [] for t in tasks}
    fs_deps: list = []

    for d in deps:
        pred_pk = str(d.predecessor_id)
        succ_pk = str(d.successor_id)
        has_predecessor.add(succ_pk)
        has_successor.add(pred_pk)
        successor_count[pred_pk] = successor_count.get(pred_pk, 0) + 1
        adj.setdefault(pred_pk, []).append(succ_pk)
        if d.dep_type == "FS":
            fs_deps.append(d)

    min_start = min((t.start_date for t in tasks if t.start_date), default=date.min)
    cutoff = min_start + timedelta(days=_START_WINDOW_DAYS)
    for t in tasks:
        pk = str(t.pk)
        if pk not in has_successor or pk in has_predecessor:
            continue
        if t.start_date and t.start_date <= cutoff:
            continue  # legitimate project-start task
        csi = _csi(t.activity_code or "")
        n_succ = successor_count.get(pk, 0)
        days_after_start = (t.start_date - min_start).days if t.start_date else 0
        results.append(
            {
                "task_pk": pk,
                "name": t.name,
                "trade": _trade_name(csi),
                "csi": csi,
                "stage": t.stage or "unassigned",
                "anomaly_type": "mid_network_orphan",
                "metric": "no_predecessors_with_successors",
                "value": n_succ,
                "peer_median": None,
                "peer_mad": None,
                "explanation": (
                    f"Has {n_succ} successor(s) but no predecessors"
                    f" — starts {days_after_start}d after project start"
                    f", floating mid-network"
                ),
            }
        )

    fs_violations: list[dict] = []
    for d in fs_deps:
        pred_pk = str(d.predecessor_id)
        succ_pk = str(d.successor_id)
        pred = task_map.get(pred_pk)
        succ = task_map.get(succ_pk)
        if not pred or not succ:
            continue
        if not pred.end_date or not succ.start_date:  # type: ignore[union-attr]
            continue
        expected_start = pred.end_date + timedelta(days=d.lag_days)  # type: ignore[union-attr]
        violation_days = (expected_start - succ.start_date).days  # type: ignore[union-attr]
        if violation_days < _FS_MIN_VIOLATION_DAYS:
            continue
        csi = _csi(succ.activity_code or "")  # type: ignore[union-attr]
        lag_str = (
            f"+{d.lag_days}d" if d.lag_days > 0 else (f"{d.lag_days}d" if d.lag_days < 0 else "")
        )
        fs_violations.append(
            {
                "task_pk": succ_pk,
                "name": succ.name,  # type: ignore[union-attr]
                "trade": _trade_name(csi),
                "csi": csi,
                "stage": succ.stage or "unassigned",  # type: ignore[union-attr]
                "anomaly_type": "fs_violation",
                "metric": "successor_starts_before_predecessor_finishes",
                "predecessor_name": pred.name,  # type: ignore[union-attr]
                "predecessor_finish": pred.end_date.isoformat(),  # type: ignore[union-attr]
                "task_start": succ.start_date.isoformat(),  # type: ignore[union-attr]
                "lag_days": d.lag_days,
                "value": violation_days,
                "peer_median": None,
                "peer_mad": None,
                "violation_days": violation_days,
                "explanation": (
                    f"Starts {violation_days}d before FS{lag_str} predecessor"
                    f" '{pred.name[:45]}' finishes ({pred.end_date.isoformat()})"  # type: ignore[union-attr]
                ),
            }
        )
    fs_violations.sort(key=lambda x: -x["value"])
    results.extend(fs_violations)

    for pk in _find_cycles_kahn(adj):
        t = task_map.get(pk)
        if not t:
            continue
        csi = _csi(t.activity_code or "")  # type: ignore[union-attr]
        results.append(
            {
                "task_pk": pk,
                "name": t.name,  # type: ignore[union-attr]
                "trade": _trade_name(csi),
                "csi": csi,
                "stage": t.stage or "unassigned",  # type: ignore[union-attr]
                "anomaly_type": "circular_dependency",
                "metric": "cycle_detected",
                "value": None,
                "peer_median": None,
                "peer_mad": None,
                "explanation": (
                    "Part of a circular dependency chain — CPM cannot be solved for this task"
                ),
            }
        )

    return results


def detect_anomalies(project_id: str, override_map: dict | None = None) -> dict:
    """Run all anomaly detectors on the current schedule snapshot.

    Returns:
        has_data             — bool
        method               — plain-English description of the detection method
        as_of                — ISO date (latest actual_end of completed tasks, or today)
        summary              — counts per type, unique tasks flagged, % of total
        running_long         — execution overruns on realistic plans (planned >= 5d, elapsed >= 2×)
        statistical_outliers — cross-sectional outliers, sorted by robust_z desc
        logic_anomalies      — data-quality + structural: unrealistic baselines, orphans,
                               FS violations, cycles.  unrealistic_baseline items appear first.
    """
    from scheduling.models import Task, TaskDependency

    from .calendar_utils import calendar_basis_note, load_project_calendars

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .only(
            "pk",
            "name",
            "activity_code",
            "calendar_object_id",
            "stage",
            "status",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "total_float",
        )
    )
    if not tasks:
        return {"has_data": False, "reason": "No tasks found."}

    cal_map = load_project_calendars(project_id)

    data_date, _ = get_project_data_date(project_id)

    task_pks = [str(t.pk) for t in tasks]
    deps = list(
        TaskDependency.objects.filter(
            predecessor_id__in=task_pks,
            successor_id__in=task_pks,
        ).only("predecessor_id", "successor_id", "dep_type", "lag_days")
    )

    running_long, unrealistic_baseline = _classify_stall_anomalies(
        tasks, data_date, cal_map=cal_map
    )
    outliers = _detect_cross_sectional(tasks, override_map=override_map, cal_map=cal_map)
    logic_structural = _detect_logic_anomalies(tasks, deps)

    # unrealistic_baseline is a data-quality issue: group with structural logic anomalies.
    # unrealistic_baseline items sort first (already sorted by overrun_ratio desc).
    logic_all = unrealistic_baseline + logic_structural

    all_flagged_pks = (
        {r["task_pk"] for r in running_long}
        | {r["task_pk"] for r in outliers}
        | {r["task_pk"] for r in logic_all}
    )
    total_tasks = len(tasks)
    total_flagged = len(all_flagged_pks)

    logger.info(
        "Anomaly detection — project %s: n_tasks=%d running_long=%d"
        " unrealistic_baseline=%d outliers=%d logic=%d unique_flagged=%d",
        project_id,
        total_tasks,
        len(running_long),
        len(unrealistic_baseline),
        len(outliers),
        len(logic_structural),
        total_flagged,
    )

    return {
        "has_data": True,
        "method": "rule + robust-statistics outlier detection on a single snapshot",
        "as_of": data_date.isoformat(),
        "summary": {
            "total_tasks": total_tasks,
            "total_flagged": total_flagged,
            "flagged_pct": round(total_flagged / total_tasks * 100, 1) if total_tasks else 0.0,
            "by_type": {
                "running_long": len(running_long),
                "unrealistic_baseline": len(unrealistic_baseline),
                "statistical_outlier": len(outliers),
                "logic_anomaly": len(logic_structural),
            },
        },
        "running_long": running_long,
        "statistical_outliers": outliers,
        "logic_anomalies": logic_all,
        "using_audit_overrides": bool(override_map),
        "n_overridden": len(override_map) if override_map else 0,
        "calendar_basis": calendar_basis_note(cal_map),
    }
