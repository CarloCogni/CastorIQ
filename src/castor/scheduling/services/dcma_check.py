# castor/scheduling/services/dcma_check.py
"""DCMA 14-Point Schedule Quality Assessment.

Implements the Defense Contract Management Agency (DCMA) 14-point
schedule health checks — the industry standard for CPM schedule quality
on construction and infrastructure programmes.

Each check returns a status (pass/warning/fail), a measured value,
and the DCMA target threshold.  The overall health score (0–100) is
the mean of per-check scores (pass=1.0, warning=0.5, fail=0.0).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

_MFO_TYPES = frozenset({"Mandatory Finish", "Finish On", "Finish On or Before"})
_MSO_TYPES = frozenset({"Mandatory Start", "Start On"})
_HARD_CONSTRAINT_TYPES = _MFO_TYPES | _MSO_TYPES


def _working_days(start: date, end: date, cal: dict | None = None) -> int:
    """Count working days from *start* to *end* inclusive using *cal*.

    Falls back to Mon–Fri-only arithmetic when cal is None (non-P6 imports).
    """
    if cal is not None:
        from .calendar_utils import count_working_days

        return count_working_days(start, end, cal)
    # Legacy Mon–Fri fallback (used only when no calendar data is available)
    if end < start:
        return 0
    delta = (end - start).days + 1
    full_weeks, remainder = divmod(delta, 7)
    working = full_weeks * 5
    dow = start.weekday()  # 0=Mon, 6=Sun
    for i in range(remainder):
        if (dow + i) % 7 < 5:
            working += 1
    return working


def _check_pct(value: float, pass_threshold: float, warn_threshold: float) -> tuple[str, float]:
    """Return (status, score) for a lower-is-better percentage check."""
    if value <= pass_threshold:
        return "pass", 1.0
    if value <= warn_threshold:
        return "warning", 0.5
    return "fail", 0.0


def _check_ratio(value: float, pass_threshold: float, warn_threshold: float) -> tuple[str, float]:
    """Return (status, score) for a higher-is-better ratio check."""
    if value >= pass_threshold:
        return "pass", 1.0
    if value >= warn_threshold:
        return "warning", 0.5
    return "fail", 0.0


@dataclass
class _Check:
    id: str
    name: str
    description: str
    value: float | int | str
    target: str
    unit: str
    status: str
    severity: str
    score: float
    flagged_items: list[str] = field(default_factory=list)


def run_dcma_check(project_id: str) -> dict:
    """Run DCMA 14-point schedule quality assessment for *project_id*.

    Returns:
        has_data    — bool
        score       — int 0-100
        score_color — "green" / "amber" / "red"
        checks      — list[dict] — one entry per check
        summary     — {pass, warning, fail, total}
        task_count  — int
        dep_count   — int
        as_of       — ISO date string
    """
    from castor.scheduling.models import P6ResourceAssignment, Task, TaskDependency

    from .calendar_utils import calendar_basis_note, load_project_calendars, task_cal
    from .utils import get_project_data_date

    today, _ = get_project_data_date(project_id)

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    cal_map = load_project_calendars(project_id)

    task_count = len(tasks)
    task_pks = {str(t.pk) for t in tasks}

    # All dependencies for this project's tasks
    all_deps = list(
        TaskDependency.objects.filter(predecessor__project_id=project_id).values(
            "predecessor_id", "successor_id", "dep_type", "lag_days"
        )
    )
    dep_count = len(all_deps)

    # CPM-run indicator — checks 6/7/12/13 are degraded without it
    float_tasks = [t for t in tasks if t.total_float is not None]
    cpm_run = len(float_tasks) > 0

    checks: list[_Check] = []

    # ── 1. Logic — open-end activities ───────────────────────────────────
    tasks_with_predecessors = {str(d["successor_id"]) for d in all_deps}
    tasks_with_successors = {str(d["predecessor_id"]) for d in all_deps}

    no_pred = [t for t in tasks if str(t.pk) not in tasks_with_predecessors]
    no_succ = [t for t in tasks if str(t.pk) not in tasks_with_successors]

    # Allow 1 natural start milestone and 1 natural finish milestone
    violations = max(len(no_pred) - 1, 0) + max(len(no_succ) - 1, 0)
    logic_pct = round(violations / task_count * 100, 1)
    status, score = _check_pct(logic_pct, 5.0, 15.0)

    flagged = [t.name for t in (no_pred[1:4] + no_succ[1:4])]
    checks.append(
        _Check(
            id="logic",
            name="Logic",
            description="Tasks missing a predecessor or successor",
            value=logic_pct,
            target="< 5%",
            unit="%",
            status=status,
            severity="critical" if status == "fail" else "medium",
            score=score,
            flagged_items=flagged,
        )
    )

    # ── 2. Leads — negative-lag relationships ────────────────────────────
    leads = [d for d in all_deps if d["lag_days"] < 0]
    lead_count = len(leads)

    if lead_count == 0:
        lead_status, lead_score = "pass", 1.0
    elif lead_count <= 5:
        lead_status, lead_score = "warning", 0.5
    else:
        lead_status, lead_score = "fail", 0.0

    checks.append(
        _Check(
            id="leads",
            name="Leads",
            description="Relationships with negative lag (schedule compression)",
            value=lead_count,
            target="0",
            unit="count",
            status=lead_status,
            severity="high" if lead_status == "fail" else "medium",
            score=lead_score,
        )
    )

    # ── 3. Lags — positive-lag relationships ─────────────────────────────
    lags = [d for d in all_deps if d["lag_days"] > 0]
    lag_pct = round(len(lags) / dep_count * 100, 1) if dep_count else 0.0
    status, score = _check_pct(lag_pct, 5.0, 15.0)

    checks.append(
        _Check(
            id="lags",
            name="Lags",
            description="Relationships with positive lag",
            value=lag_pct,
            target="< 5%",
            unit="%",
            status=status,
            severity="medium",
            score=score,
        )
    )

    # ── 4. Relationship Types — non-Finish-to-Start ───────────────────────
    non_fs = [d for d in all_deps if d["dep_type"] != "FS"]
    non_fs_pct = round(len(non_fs) / dep_count * 100, 1) if dep_count else 0.0
    status, score = _check_pct(non_fs_pct, 10.0, 30.0)

    checks.append(
        _Check(
            id="relationship_types",
            name="Relationship Types",
            description="Relationships that are not Finish-to-Start",
            value=non_fs_pct,
            target="< 10%",
            unit="%",
            status=status,
            severity="low",
            score=score,
        )
    )

    # ── 5. Hard Constraints — MFO / MSO ──────────────────────────────────
    hard_constrained = [t for t in tasks if (t.constraint_type or "") in _HARD_CONSTRAINT_TYPES]
    hc_pct = round(len(hard_constrained) / task_count * 100, 1)
    status, score = _check_pct(hc_pct, 5.0, 15.0)

    checks.append(
        _Check(
            id="hard_constraints",
            name="Hard Constraints",
            description="Tasks with MFO or MSO hard date constraints",
            value=hc_pct,
            target="< 5%",
            unit="%",
            status=status,
            severity="medium",
            score=score,
            flagged_items=[t.name for t in hard_constrained[:4]],
        )
    )

    # ── 6. High Float — TF > 44 working days (incomplete tasks only) ────────
    # DCMA applies to the open schedule — completed tasks retain historical float
    # values that are no longer meaningful for schedule quality assessment.
    if not cpm_run:
        checks.append(
            _Check(
                id="high_float",
                name="High Float",
                description="Incomplete tasks with total float > 44 working days",
                value="N/A",
                target="< 5%",
                unit="%",
                status="warning",
                severity="low",
                score=0.5,
            )
        )
    else:
        open_float_tasks = [t for t in float_tasks if t.status != "complete"]
        high_float = [t for t in open_float_tasks if t.total_float > 44]
        hf_denom = len(open_float_tasks) or 1
        hf_pct = round(len(high_float) / hf_denom * 100, 1)
        status, score = _check_pct(hf_pct, 5.0, 15.0)
        checks.append(
            _Check(
                id="high_float",
                name="High Float",
                description="Incomplete tasks with total float > 44 working days",
                value=hf_pct,
                target="< 5%",
                unit="%",
                status=status,
                severity="medium",
                score=score,
            )
        )

    # ── 7. Negative Float — TF < 0 ───────────────────────────────────────
    if not cpm_run:
        checks.append(
            _Check(
                id="negative_float",
                name="Negative Float",
                description="Tasks with total float < 0 (schedule overrun)",
                value="N/A",
                target="0",
                unit="count",
                status="warning",
                severity="low",
                score=0.5,
            )
        )
    else:
        neg_float_tasks = [t for t in float_tasks if t.total_float < 0]
        nf_count = len(neg_float_tasks)
        if nf_count == 0:
            nf_status, nf_score = "pass", 1.0
        elif nf_count <= 3:
            nf_status, nf_score = "warning", 0.5
        else:
            nf_status, nf_score = "fail", 0.0

        sorted_neg = sorted(neg_float_tasks, key=lambda x: x.total_float)
        checks.append(
            _Check(
                id="negative_float",
                name="Negative Float",
                description="Tasks with total float < 0 (schedule overrun)",
                value=nf_count,
                target="0",
                unit="count",
                status=nf_status,
                severity="critical"
                if nf_status == "fail"
                else "high"
                if nf_status == "warning"
                else "low",
                score=nf_score,
                flagged_items=[f"{t.name} ({t.total_float:+d}d)" for t in sorted_neg[:4]],
            )
        )

    # ── 8. High Duration — > 44 working days ─────────────────────────────
    def _task_dur_wd(t):
        return _working_days(t.start_date, t.end_date, task_cal(t, cal_map) if cal_map else None)

    high_dur = [t for t in tasks if _task_dur_wd(t) > 44]
    hd_pct = round(len(high_dur) / task_count * 100, 1)
    status, score = _check_pct(hd_pct, 5.0, 15.0)

    dur_samples = sorted(high_dur, key=_task_dur_wd, reverse=True)
    checks.append(
        _Check(
            id="high_duration",
            name="High Duration",
            description="Tasks with duration > 44 working days",
            value=hd_pct,
            target="< 5%",
            unit="%",
            status=status,
            severity="medium",
            score=score,
            flagged_items=[f"{t.name} ({_task_dur_wd(t)}wd)" for t in dur_samples[:4]],
        )
    )

    # ── 9. Invalid Dates ──────────────────────────────────────────────────
    invalid = []
    for t in tasks:
        if t.end_date < t.start_date:
            invalid.append(t)
        elif t.actual_start and t.actual_start > today:
            invalid.append(t)
        elif t.actual_end and t.actual_end > today:
            invalid.append(t)
        elif t.actual_start and t.actual_end and t.actual_end < t.actual_start:
            invalid.append(t)

    inv_count = len(invalid)
    if inv_count == 0:
        inv_status, inv_score = "pass", 1.0
    elif inv_count <= 3:
        inv_status, inv_score = "warning", 0.5
    else:
        inv_status, inv_score = "fail", 0.0

    checks.append(
        _Check(
            id="invalid_dates",
            name="Invalid Dates",
            description="Tasks with actuals in future or end before start",
            value=inv_count,
            target="0",
            unit="count",
            status=inv_status,
            severity="high",
            score=inv_score,
            flagged_items=[t.name for t in invalid[:4]],
        )
    )

    # ── 10. Resources — no cost or resource assignment ────────────────────
    resource_task_pks = set(
        str(pk)
        for pk in P6ResourceAssignment.objects.filter(task_id__in=task_pks)
        .values_list("task_id", flat=True)
        .distinct()
    )
    no_resource = [
        t for t in tasks if not float(t.cost or 0) and str(t.pk) not in resource_task_pks
    ]
    res_pct = round(len(no_resource) / task_count * 100, 1)
    status, score = _check_pct(res_pct, 5.0, 20.0)

    checks.append(
        _Check(
            id="resources",
            name="Resources",
            description="Tasks without cost or resource assignment",
            value=res_pct,
            target="< 5%",
            unit="%",
            status=status,
            severity="medium",
            score=score,
        )
    )

    # ── 11. Missed Tasks — past planned finish, not complete ──────────────
    due_tasks = [t for t in tasks if t.end_date <= today]
    missed = [t for t in due_tasks if t.status != "complete"]

    if not due_tasks:
        missed_pct = 0.0
        missed_status, missed_score = "warning", 0.5
    else:
        missed_pct = round(len(missed) / len(due_tasks) * 100, 1)
        missed_status, missed_score = _check_pct(missed_pct, 5.0, 15.0)

    checks.append(
        _Check(
            id="missed_tasks",
            name="Missed Tasks",
            description="Tasks past planned finish but not marked complete",
            value=missed_pct if due_tasks else "N/A",
            target="< 5%",
            unit="%" if due_tasks else "",
            status=missed_status,
            severity="high" if missed_status == "fail" else "medium",
            score=missed_score,
            flagged_items=[
                f"{t.name} (due {t.end_date})" for t in sorted(missed, key=lambda x: x.end_date)[:4]
            ],
        )
    )

    # ── 12. Critical Path Test — connected critical chain ─────────────────
    critical_tasks = [t for t in tasks if t.is_critical]
    crit_pks = {str(t.pk) for t in critical_tasks}

    if not critical_tasks or not cpm_run:
        cp_status, cp_score = "fail", 0.0
        cp_value = "No critical path — run CPM"
    else:
        crit_deps = [
            d
            for d in all_deps
            if str(d["predecessor_id"]) in crit_pks and str(d["successor_id"]) in crit_pks
        ]
        # Weak-connectivity check (undirected)
        adj: dict[str, set[str]] = {pk: set() for pk in crit_pks}
        for d in crit_deps:
            p, s = str(d["predecessor_id"]), str(d["successor_id"])
            adj[p].add(s)
            adj[s].add(p)

        root = next(iter(crit_pks))
        visited: set[str] = set()
        queue = [root]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            queue.extend(adj.get(node, []))

        unreachable = len(crit_pks - visited)
        if unreachable == 0:
            cp_status, cp_score = "pass", 1.0
            cp_value = f"Continuous ({len(critical_tasks)} tasks)"
        elif unreachable <= 2:
            cp_status, cp_score = "warning", 0.5
            cp_value = f"Broken ({unreachable} disconnected)"
        else:
            cp_status, cp_score = "fail", 0.0
            cp_value = f"Fragmented ({unreachable} disconnected)"

    checks.append(
        _Check(
            id="critical_path_test",
            name="Critical Path Test",
            description="Continuous critical chain from project start to finish",
            value=cp_value,
            target="Continuous",
            unit="",
            status=cp_status,
            severity="critical"
            if cp_status == "fail"
            else "high"
            if cp_status == "warning"
            else "low",
            score=cp_score,
        )
    )

    # ── 13. CPLI — Critical Path Length Index ─────────────────────────────
    # CPLI = (CPL + TF_finish) / CPL
    # CPL        = remaining working days today → project planned end
    # TF_finish  = total float of the latest-finishing critical task
    #              (the project-completion node). This is the only float
    #              that represents the overall schedule buffer against the
    #              project end date. Using min(TF) is wrong — it picks up
    #              intermediate MFO deadlines and produces negative CPLI,
    #              which is mathematically valid but meaningless as an index.
    if not cpm_run or not critical_tasks:
        cpli_value: float | str = "N/A"
        cpli_status, cpli_score = "warning", 0.5
    else:
        crit_with_float = [t for t in critical_tasks if t.total_float is not None]
        # Latest-finishing critical task = project completion node
        finish_node = max(crit_with_float, key=lambda t: t.end_date)
        project_end = max(t.end_date for t in tasks)
        # Use the finish node's own calendar so cpl and tf_finish share one basis.
        finish_cal = task_cal(finish_node, cal_map) if cal_map else None
        cpl = max(_working_days(today, project_end, finish_cal), 1)
        tf_finish = finish_node.total_float  # TF of project end node (already wd from CPM)
        cpli_raw = (cpl + tf_finish) / cpl
        cpli_value = round(cpli_raw, 3)
        cpli_status, cpli_score = _check_ratio(cpli_raw, 0.95, 0.80)

    checks.append(
        _Check(
            id="cpli",
            name="CPLI",
            description="Critical Path Length Index — schedule realism",
            value=cpli_value,
            target="≥ 0.95",
            unit="index",
            status=cpli_status,
            severity="critical"
            if cpli_status == "fail"
            else "high"
            if cpli_status == "warning"
            else "low",
            score=cpli_score,
        )
    )

    # ── 14. BEI — Baseline Execution Index ───────────────────────────────
    # BEI = activities completed on time / activities planned to complete by data date
    planned_done = [t for t in tasks if t.end_date <= today]
    actual_done = [t for t in planned_done if t.status == "complete"]

    if not planned_done:
        bei_value: float | str = "N/A"
        bei_status, bei_score = "warning", 0.5
    else:
        bei_raw = len(actual_done) / len(planned_done)
        bei_value = round(bei_raw, 3)
        bei_status, bei_score = _check_ratio(bei_raw, 0.95, 0.80)

    checks.append(
        _Check(
            id="bei",
            name="BEI",
            description="Baseline Execution Index — completions vs baseline plan",
            value=bei_value,
            target="≥ 0.95",
            unit="index",
            status=bei_status,
            severity="critical"
            if bei_status == "fail"
            else "high"
            if bei_status == "warning"
            else "low",
            score=bei_score,
        )
    )

    # ── Overall Health Score ───────────────────────────────────────────────
    health_score = round(sum(c.score for c in checks) / len(checks) * 100)
    pass_count = sum(1 for c in checks if c.status == "pass")
    warn_count = sum(1 for c in checks if c.status == "warning")
    fail_count = sum(1 for c in checks if c.status == "fail")

    score_color = "green" if health_score >= 80 else "amber" if health_score >= 60 else "red"

    logger.info(
        "DCMA — project %s: score=%d pass=%d warn=%d fail=%d",
        project_id,
        health_score,
        pass_count,
        warn_count,
        fail_count,
    )

    return {
        "has_data": True,
        "score": health_score,
        "score_color": score_color,
        "checks": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "value": c.value,
                "target": c.target,
                "unit": c.unit,
                "status": c.status,
                "severity": c.severity,
                "score": c.score,
                "flagged_items": c.flagged_items,
            }
            for c in checks
        ],
        "summary": {
            "pass": pass_count,
            "warning": warn_count,
            "fail": fail_count,
            "total": len(checks),
        },
        "task_count": task_count,
        "dep_count": dep_count,
        "as_of": today.isoformat(),
        "calendar_basis": calendar_basis_note(cal_map),
    }
