# islam/scheduling/services/monte_carlo.py
"""Monte Carlo Schedule Simulation.

Runs N forward-pass simulations through the task dependency network,
sampling each incomplete task's remaining duration from a PERT distribution
(Beta-scaled by optimistic 0.8×, most-likely 1×, pessimistic 1.5× of
remaining calendar days).

Performance model
-----------------
* Only the MAX_SIM_TASKS most critical incomplete tasks are simulated
  (sorted by total_float ASC).  Tasks outside this set are treated as fixed
  anchors finishing on their planned end_date.
* Complete tasks are fixed at their actual_end (or end_date).
* External predecessor contributions are pre-baked into each task's
  min_start floor so the hot simulation loop only touches in-simulation edges.
* Working with integer ordinals throughout — no date arithmetic inside the loop.
* Only FS dependencies carry the +1 day gap.  SS/FF/SF are approximated as FS
  without the gap; this covers ~95 % of real-schedule dependency semantics.

Public entry point
------------------
    compute_monte_carlo(project_id, n_simulations=1000) -> dict
"""

from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict, deque
from datetime import date

from .utils import get_project_data_date

logger = logging.getLogger(__name__)

MAX_SIM_TASKS = 500
_PERT_OPT = 0.8
_PERT_PESS = 1.5


# ── PERT sampler ──────────────────────────────────────────────────────────────


def _pert_int(a: float, m: float, b: float, _bv=random.betavariate) -> int:
    """Sample a PERT-distributed duration (integer days).

    Uses the Beta distribution parameterised from the PERT mean/variance
    formulae.  Falls back to *m* when the spread is negligible.
    """
    if b - a < 0.5:
        return max(int(m + 0.5), 1)
    mean = (a + 4 * m + b) / 6.0
    alpha = max(6 * (mean - a) / (b - a), 0.5)
    beta_ = max(6 * (b - mean) / (b - a), 0.5)
    raw = a + _bv(alpha, beta_) * (b - a)
    return max(int(raw + 0.5), 1)


# ── Data loading ──────────────────────────────────────────────────────────────


def _load_data(project_id: str) -> tuple[list[dict], list[dict]]:
    """Return (task_rows, dep_rows) for *project_id*."""
    from islam.scheduling.models import Task, TaskDependency

    task_rows = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .values(
            "id",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "status",
            "total_float",
            "early_start",  # CPM earliest start; sets the optimistic floor for sim tasks
            "early_finish",  # CPM earliest finish; used as anchor for non-sim tasks
        )
    )

    dep_rows = list(
        TaskDependency.objects.filter(
            predecessor__project_id=project_id,
            successor__project_id=project_id,
        ).values("predecessor_id", "successor_id", "dep_type", "lag_days")
    )

    return task_rows, dep_rows


# ── Simulation set + per-task precomputation ──────────────────────────────────


def _build_sim_inputs(
    task_rows: list[dict],
    dep_rows: list[dict],
    today_ord: int,
) -> tuple[dict[str, dict], list[str]]:
    """Pre-compute per-task simulation parameters and topological order.

    Returns:
        sim_info   — {task_id: task_param_dict}
        topo_order — task IDs in topological order (simulation-set only)
    """
    all_tasks: dict[str, dict] = {str(r["id"]): r for r in task_rows}

    # ── Task selection ─────────────────────────────────────────────────────
    # Split the budget evenly:
    #   Half — most overdue (lowest / most-negative total_float): captures tasks
    #          already exceeding their baseline and driving current schedule delay.
    #   Half — latest CPM early_finish: captures terminal milestones (e.g.
    #          "Project Hand over") that determine the actual project end date
    #          even when they have zero or small float in absolute terms.
    # Without the second half, terminal tasks are treated as fixed anchors at
    # their (stale) planned end_date, causing severe underestimation of P80/P95.
    incomplete = [r for r in task_rows if r["status"] != "complete"]
    n_half = MAX_SIM_TASKS // 2

    by_float = sorted(
        incomplete,
        key=lambda r: (r["total_float"] is None, r["total_float"] or 0),
    )[:n_half]

    by_ef = sorted(
        [r for r in incomplete if r.get("early_finish")],
        key=lambda r: r["early_finish"],
        reverse=True,
    )[:n_half]

    # Merge and de-duplicate (dict preserves insertion order, last write wins)
    merged = {str(r["id"]): r for r in by_float}
    merged.update({str(r["id"]): r for r in by_ef})
    sim_set: set[str] = set(merged.keys())

    # ── Predecessor lookup ────────────────────────────────────────────────
    pred_map: dict[str, list[dict]] = defaultdict(list)
    for dep in dep_rows:
        succ_id = str(dep["successor_id"])
        pred_id = str(dep["predecessor_id"])
        if succ_id in all_tasks and pred_id in all_tasks:
            pred_map[succ_id].append(
                {
                    "pred_id": pred_id,
                    "dep_type": dep["dep_type"],
                    "lag": dep["lag_days"] or 0,
                }
            )

    def anchor_ord(tid: str) -> int:
        """Fixed finish ordinal for a non-simulated task.

        Priority:
          1. Complete with actual_end  → actual_end
          2. Incomplete with CPM early_finish → max(early_finish, today) so we
             never anchor in the past relative to today (a task hasn't finished
             yet, so the earliest it can finish is today).
          3. Fallback                  → end_date (planned)
        """
        t = all_tasks.get(tid)
        if t is None:
            return today_ord
        if t["status"] == "complete" and t["actual_end"]:
            return t["actual_end"].toordinal()
        ef = t.get("early_finish")
        if ef:
            return max(ef.toordinal(), today_ord)
        return t["end_date"].toordinal()

    # ── Build sim_info ────────────────────────────────────────────────────
    sim_info: dict[str, dict] = {}

    for r in task_rows:
        tid = str(r["id"])

        if r["status"] == "complete":
            sim_info[tid] = {"fixed_finish": anchor_ord(tid)}
            continue

        if tid not in sim_set:
            # Non-sim incomplete: fixed at CPM early_finish (if available) or planned end_date.
            sim_info[tid] = {"fixed_finish": anchor_ord(tid)}
            continue

        # ── Duration parameters ────────────────────────────────────────
        # PERT is applied to the task's OWN planned execution time (planned_dur),
        # NOT to time-remaining from today.  The CPM early_start floor handles
        # start-date positioning; planned_dur represents only the task's inherent
        # work content.  Using (end_date - today) would double-count delays for
        # tasks whose actual_start was recorded early but whose network start is
        # still far in the future (common in behind-schedule P6 schedules).
        planned_dur = max((r["end_date"] - r["start_date"]).days, 1)

        # Pessimistic multiplier: inflate if the task started late (already behind
        # its own baseline), capped at 2.5× planned duration.
        if r["actual_start"] and r["actual_start"] > r["start_date"]:
            late_days = (r["actual_start"] - r["start_date"]).days
            late_factor = min(late_days / planned_dur, 1.0)
            pess_mult = _PERT_PESS + late_factor
        else:
            pess_mult = _PERT_PESS

        opt = max(planned_dur * _PERT_OPT, 1.0)
        ml = float(planned_dur)
        pess = planned_dur * pess_mult

        # ── min_start: incorporate external predecessor anchors ────────
        # CPM early_start is the network-aware optimistic floor.  Using it
        # means PERT uncertainty is applied AROUND the CPM estimate, not from
        # today.  Predecessor propagation can still push the start later in
        # pessimistic simulations (which is correct), but never before the
        # CPM's own calculation.
        cpm_es = r.get("early_start")
        cpm_floor = max(cpm_es.toordinal(), today_ord) if cpm_es else today_ord
        min_start = max(r["start_date"].toordinal(), cpm_floor)
        # sim_preds_fs — FS predecessors: constrain successor START
        # sim_preds_ff — FF predecessors: constrain successor FINISH
        # SS/SF predecessors are baked into min_start using the predecessor's
        # CPM early_start (not its finish), which correctly models "start
        # together" semantics without serialising parallel activities.
        sim_preds_fs: list[tuple[str, int]] = []  # (pred_id, lag)
        sim_preds_ff: list[tuple[str, int]] = []  # (pred_id, lag)

        for dep in pred_map.get(tid, []):
            pred_id = dep["pred_id"]
            lag = dep["lag"]
            dep_type = dep["dep_type"]

            if pred_id in sim_set:
                if dep_type == "FS":
                    sim_preds_fs.append((pred_id, lag))
                elif dep_type == "FF":
                    sim_preds_ff.append((pred_id, lag))
                else:
                    # SS / SF: bake predecessor's CPM early_start into min_start
                    pred_r = all_tasks.get(pred_id, {})
                    pred_es = pred_r.get("early_start")
                    if pred_es:
                        floor = max(pred_es.toordinal(), today_ord) + lag
                        if floor > min_start:
                            min_start = floor
            else:
                # External anchor (complete or non-sim incomplete)
                if dep_type in ("FS", "FF"):
                    floor = anchor_ord(pred_id) + lag + (1 if dep_type == "FS" else 0)
                else:
                    # SS/SF: use predecessor's CPM early_start as floor
                    pred_r = all_tasks.get(pred_id, {})
                    pred_es = pred_r.get("early_start")
                    anchor = max(pred_es.toordinal(), today_ord) if pred_es else anchor_ord(pred_id)
                    floor = anchor + lag
                if floor > min_start:
                    min_start = floor

        sim_info[tid] = {
            "fixed_finish": None,
            "opt": opt,
            "ml": ml,
            "pess": pess,
            "min_start": min_start,
            "sim_preds_fs": sim_preds_fs,
            "sim_preds_ff": sim_preds_ff,
        }

    # ── Topological sort (Kahn's) over sim_set ────────────────────────────
    in_degree: dict[str, int] = {tid: 0 for tid in sim_set}
    succ_of: dict[str, list[str]] = defaultdict(list)

    for tid in sim_set:
        # Topological ordering only cares about FS deps (FF deps don't affect start order)
        for pred_id, _lag in sim_info[tid].get("sim_preds_fs", []):
            if pred_id in sim_set:
                in_degree[tid] += 1
                succ_of[pred_id].append(tid)

    queue: deque[str] = deque(tid for tid, d in in_degree.items() if d == 0)
    topo_order: list[str] = []

    while queue:
        tid = queue.popleft()
        topo_order.append(tid)
        for succ in succ_of[tid]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    # Append any cycle members (invalid schedules) at the end
    placed = set(topo_order)
    for tid in sim_set:
        if tid not in placed:
            topo_order.append(tid)

    return sim_info, topo_order


# ── Hot simulation loop ───────────────────────────────────────────────────────


def _run_simulations(
    sim_info: dict[str, dict],
    topo_order: list[str],
    n: int,
    today_ord: int = 0,
) -> list[int]:
    """Run *n* Monte Carlo forward-passes.  Returns sorted completion ordinals."""
    _bv = random.betavariate  # local binding avoids attribute lookup in hot loop

    # Pre-extract per-task tuples for the tightest inner loop possible
    task_params = [
        (
            tid,
            sim_info[tid]["fixed_finish"],
            sim_info[tid].get("opt", 0.0),
            sim_info[tid].get("ml", 0.0),
            sim_info[tid].get("pess", 0.0),
            sim_info[tid].get("min_start", 0),
            sim_info[tid].get("sim_preds_fs", []),
            sim_info[tid].get("sim_preds_ff", []),
        )
        for tid in topo_order
    ]

    # Pre-compute the max fixed_finish across all non-sim tasks.  This
    # represents the deterministic floor imposed by tasks that are fixed anchors
    # (not simulated) — typically intermediate tasks with moderate float.  It
    # ensures project completion never falls below what the CPM already dictates
    # for non-simulated portions of the schedule.
    fixed_floor = max(
        (
            info["fixed_finish"]
            for info in sim_info.values()
            if info.get("fixed_finish") is not None
        ),
        default=today_ord,
    )

    results: list[int] = []

    for _ in range(n):
        finish: dict[str, int] = {}

        for tid, fixed, opt, ml, pess, min_s, preds_fs, preds_ff in task_params:
            if fixed is not None:
                finish[tid] = fixed
                continue

            # FS predecessors constrain start
            start = min_s
            for pred_id, lag in preds_fs:
                pf = finish.get(pred_id, 0)
                candidate = pf + lag + 1
                if candidate > start:
                    start = candidate

            # PERT sample — inlined for speed
            b_minus_a = pess - opt
            if b_minus_a > 0.5:
                mean = (opt + 4 * ml + pess) / 6.0
                al = max(6 * (mean - opt) / b_minus_a, 0.5)
                be = max(6 * (pess - mean) / b_minus_a, 0.5)
                dur = max(int(opt + _bv(al, be) * b_minus_a + 0.5), 1)
            else:
                dur = max(int(ml + 0.5), 1)

            tf = start + dur

            # FF predecessors constrain finish (successor finishes with predecessor)
            for pred_id, lag in preds_ff:
                pf = finish.get(pred_id, 0)
                ff_floor = pf + lag
                if ff_floor > tf:
                    tf = ff_floor

            finish[tid] = tf

        sim_max = max(finish.values()) if finish else today_ord
        results.append(max(sim_max, fixed_floor))

    results.sort()
    return results


# ── Histogram + percentile helpers ───────────────────────────────────────────


def _percentile(sorted_results: list[int], pct: float) -> date:
    idx = min(int(len(sorted_results) * pct), len(sorted_results) - 1)
    return date.fromordinal(sorted_results[idx])


def _build_histogram(sorted_results: list[int]) -> list[dict]:
    """Build an adaptive histogram from sorted ordinal results.

    Bucket granularity scales with the spread:
        spread ≤  90 days  →  weekly  (7-day buckets)
        spread ≤ 365 days  →  monthly
        spread >  365 days →  quarterly
    """
    n = len(sorted_results)
    if not n:
        return []

    spread = sorted_results[-1] - sorted_results[0]

    if spread <= 90:
        # Weekly buckets
        bucket_days = 7
        base = sorted_results[0]
        buckets_raw: dict[int, int] = {}
        for r in sorted_results:
            bucket = base + ((r - base) // bucket_days) * bucket_days
            buckets_raw[bucket] = buckets_raw.get(bucket, 0) + 1
        cumulative = 0
        result = []
        for bs in sorted(buckets_raw):
            count = buckets_raw[bs]
            cumulative += count
            label_d = date.fromordinal(bs)
            result.append(
                {
                    "label": f"{label_d.day} {label_d.strftime('%b')}",
                    "date": label_d.isoformat(),
                    "count": count,
                    "pct": round(count / n * 100, 1),
                    "cumulative_pct": round(cumulative / n * 100, 1),
                }
            )
        return result

    if spread <= 365:
        # Monthly buckets
        def to_month(ord_: int) -> tuple[int, int]:
            d = date.fromordinal(ord_)
            return (d.year, d.month)

        month_counts = Counter(to_month(r) for r in sorted_results)
        cumulative = 0
        result = []
        for (y, m), count in sorted(month_counts.items()):
            cumulative += count
            result.append(
                {
                    "label": date(y, m, 1).strftime("%b %Y"),
                    "date": date(y, m, 1).isoformat(),
                    "count": count,
                    "pct": round(count / n * 100, 1),
                    "cumulative_pct": round(cumulative / n * 100, 1),
                }
            )
        return result

    # Quarterly buckets
    def to_quarter(ord_: int) -> tuple[int, int]:
        d = date.fromordinal(ord_)
        return (d.year, (d.month - 1) // 3 + 1)

    q_counts = Counter(to_quarter(r) for r in sorted_results)
    cumulative = 0
    result = []
    for (y, q), count in sorted(q_counts.items()):
        cumulative += count
        result.append(
            {
                "label": f"Q{q} {y}",
                "date": date(y, (q - 1) * 3 + 1, 1).isoformat(),
                "count": count,
                "pct": round(count / n * 100, 1),
                "cumulative_pct": round(cumulative / n * 100, 1),
            }
        )
    return result


# ── Public entry point ────────────────────────────────────────────────────────


def compute_monte_carlo(project_id: str, n_simulations: int = 1000) -> dict:
    """Run Monte Carlo schedule simulation for *project_id*.

    Args:
        project_id:    UUID string.
        n_simulations: Number of forward-pass iterations (default 1000).

    Returns dict with keys:
        has_data          — bool
        n_simulations     — int (actual count run)
        tasks_simulated   — int (incomplete tasks in simulation set)
        baseline_end      — ISO date string
        p50 / p80 / p95   — ISO date strings
        p50_delta / p80_delta / p95_delta — int (days vs baseline, + = late)
        histogram         — list[{label, date, count, pct, cumulative_pct}]
        summary_line      — str (for chat context)
    """
    today, _ = get_project_data_date(project_id)
    today_ord = today.toordinal()

    task_rows, dep_rows = _load_data(project_id)
    if not task_rows:
        return {"has_data": False}

    baseline_end = max(r["end_date"] for r in task_rows)

    incomplete_count = sum(1 for r in task_rows if r["status"] != "complete")
    if incomplete_count == 0:
        return {"has_data": False, "reason": "all_tasks_complete"}

    sim_info, topo_order = _build_sim_inputs(task_rows, dep_rows, today_ord)

    if not topo_order:
        return {"has_data": False, "reason": "no_critical_path_tasks"}

    results = _run_simulations(sim_info, topo_order, n_simulations, today_ord)

    if not results:
        return {"has_data": False, "reason": "simulation_produced_no_results"}

    p50 = _percentile(results, 0.50)
    p80 = _percentile(results, 0.80)
    p95 = _percentile(results, 0.95)
    histogram = _build_histogram(results)

    def delta(d: date) -> int:
        return (d - baseline_end).days

    # Build summary line
    delta80 = delta(p80)
    if delta80 > 0:
        delta_str = f"{delta80} days after baseline"
    elif delta80 < 0:
        delta_str = f"{abs(delta80)} days before baseline"
    else:
        delta_str = "on baseline"

    summary_line = (
        f"Monte Carlo P80 completion: {p80.strftime('%B %Y')} "
        f"({delta_str}, 80% confidence) | "
        f"P50: {p50.strftime('%b %Y')} | P95: {p95.strftime('%b %Y')}"
    )

    tasks_simulated = len(topo_order)

    logger.info(
        "Monte Carlo — project %s: %d sims, %d tasks, P50=%s P80=%s P95=%s",
        project_id,
        n_simulations,
        tasks_simulated,
        p50.isoformat(),
        p80.isoformat(),
        p95.isoformat(),
    )

    return {
        "has_data": True,
        "n_simulations": n_simulations,
        "tasks_simulated": tasks_simulated,
        "baseline_end": baseline_end.isoformat(),
        "p50": p50.isoformat(),
        "p80": p80.isoformat(),
        "p95": p95.isoformat(),
        "p50_delta": delta(p50),
        "p80_delta": delta(p80),
        "p95_delta": delta(p95),
        "histogram": histogram,
        "summary_line": summary_line,
    }
