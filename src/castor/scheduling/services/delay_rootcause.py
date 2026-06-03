# castor/scheduling/services/delay_rootcause.py
"""Delay Root-Cause Clustering via CPM driving-relationship propagation.

For each delayed task, identifies the driving predecessor (FS/SS predecessor
whose finish + lag aligns with the task's early_start within BINDING_TOLERANCE).
Tasks whose delay can't be explained by network predecessors are classified as
CONSTRAINT-driven (hard date) or ORPHAN (no logic links).

Classification: PROPAGATED (predecessor also delayed) / ROOT_CAUSE (origin node)
/ CONSTRAINT / ORPHAN.  Root causes are ranked by downstream task count via BFS
and clustered by trade, stage, and activity type.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from datetime import date, timedelta

from .utils import get_project_data_date

logger = logging.getLogger(__name__)


# A predecessor is "binding" if its (EF + lag) is within this many calendar days
# of the successor's early_start (accounts for working-day vs calendar-day CPM).
BINDING_TOLERANCE = 7

# If the nearest binding date is more than this many days before early_start,
# the delay is driven by a date constraint, not by the predecessor chain.
CONSTRAINT_GAP = 14


_CSI_TRADE: dict[str, str] = {
    "00": "General",
    "01": "General Requirements",
    "02": "Existing Conditions",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "06": "Wood & Plastics",
    "07": "Thermal & Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "11": "Equipment",
    "12": "Furnishings",
    "13": "Special Construction",
    "14": "Conveying (Elevators)",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety / Security",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
}

_STAGE_LABELS: dict[str, str] = {
    "substructure": "Substructure",
    "structure": "Structure",
    "envelope": "Envelope",
    "mep": "MEP",
    "finishes": "Finishes",
    "external": "External Works",
    "": "General / Unassigned",
}


_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")


def _csi_trade(activity_code: str) -> str:
    """Parse CSI 2-digit division from codes like 'SCCAS-C035300.00'."""
    m = _CSI_RE.search(activity_code or "")
    if m:
        return _CSI_TRADE.get(m.group(1), f"Div {m.group(1)}")
    return "Unknown"


def _csi_trade_resolved(task, override_map: dict | None) -> str:
    """Trade name after applying any confirmed audit override for this task."""
    if override_map:
        task_pk = str(task.pk)
        if task_pk in override_map:
            csi = override_map[task_pk]
            return _CSI_TRADE.get(csi, f"Div {csi}")
    return _csi_trade(task.activity_code or "")


def _activity_type(name: str) -> str:
    n = name.lower()
    if "submit" in n:
        return "Submittal"
    if "approv" in n:
        return "Approval"
    if "procur" in n or "purchase" in n or "sourcing" in n:
        return "Procurement"
    if "loe" in n or "level of effort" in n:
        return "LOE"
    if "inspect" in n or "testing" in n or "commission" in n or "snagging" in n:
        return "Testing / Inspection"
    return "Construction"


def _finish_variance(task, today: date, cal: dict | None = None) -> int | None:
    """Working-day finish variance; None = not assessable (no baseline or no actual).

    Positive = late, negative = early.  Uses the task's P6 calendar when
    provided so weekends/holidays are excluded from the count.
    Falls back to calendar-day arithmetic when cal is None.
    """
    from .calendar_utils import working_day_diff

    if not task.end_date:
        return None
    if cal is None:
        # Legacy fallback: calendar days (used when no P6 calendar is available)
        if task.status == "complete":
            return (task.actual_end - task.end_date).days if task.actual_end else None
        if task.early_finish:
            return (task.early_finish - task.end_date).days
        return None
    if task.status == "complete":
        return working_day_diff(task.end_date, task.actual_end, cal) if task.actual_end else None
    if task.early_finish:
        return working_day_diff(task.end_date, task.early_finish, cal)
    return None


def _binding_date(pred, dep_type: str, lag_days: int) -> date | None:
    """Date at which predecessor binds successor's early_start."""
    if dep_type == "FS":
        if pred.early_finish is None:
            return None
        return pred.early_finish + timedelta(days=lag_days)
    if dep_type == "SS":
        if pred.early_start is None:
            return None
        return pred.early_start + timedelta(days=lag_days)
    return None  # FF/SF not handled for driving logic


def run_delay_rootcause(
    project_id: str,
    delay_threshold_days: int = 0,
    override_map: dict | None = None,
) -> dict:
    """Run delay root-cause analysis for *project_id*.

    Args:
        delay_threshold_days: only tasks with finish_variance > this value are
            considered delayed.  Default 0 (any slippage counts).
        override_map: optional {task_pk: ai_csi} from trade_resolver; when
            provided the resolved trade is used for clustering instead of the
            raw coded value.

    Returns a dict with keys: has_data, summary, root_causes, clusters, orphans.
    """
    from castor.scheduling.models import Task, TaskDependency

    from .calendar_utils import load_project_calendars, task_cal

    today, _ = get_project_data_date(project_id)

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
    )
    if not tasks:
        return {"has_data": False}

    # Load P6 calendars for working-day arithmetic.
    # Falls back to _DEFAULT_CAL (all days = working) when no calendar data exists.
    cal_map = load_project_calendars(project_id)
    has_calendar_data = bool(cal_map)

    task_map: dict[str, object] = {str(t.pk): t for t in tasks}

    variances: dict[str, int] = {}  # pk → finish_variance_days (working days)
    excluded_no_baseline = 0

    for t in tasks:
        cal = task_cal(t, cal_map) if has_calendar_data else None
        fv = _finish_variance(t, today, cal)
        if fv is None:
            excluded_no_baseline += 1
        else:
            variances[str(t.pk)] = fv

    delayed: dict[str, int] = {pk: fv for pk, fv in variances.items() if fv > delay_threshold_days}

    if not delayed:
        return {
            "has_data": True,
            "summary": {
                "total_tasks": len(tasks),
                "total_delayed": 0,
                "excluded_no_baseline": excluded_no_baseline,
                "message": "No delayed tasks found.",
            },
            "root_causes": [],
            "clusters": {},
            "orphans": {"count": 0, "tasks": []},
        }

    # preds_of[successor_pk] = [(pred_pk, dep_type, lag_days), ...]
    # succs_of[pred_pk]      = [(succ_pk, dep_type, lag_days), ...]
    all_deps = list(
        TaskDependency.objects.filter(predecessor__project_id=project_id).values(
            "predecessor_id", "successor_id", "dep_type", "lag_days"
        )
    )

    preds_of: dict[str, list] = defaultdict(list)
    succs_of: dict[str, list] = defaultdict(list)
    for d in all_deps:
        p = str(d["predecessor_id"])
        s = str(d["successor_id"])
        preds_of[s].append((p, d["dep_type"], d["lag_days"]))
        succs_of[p].append((s, d["dep_type"], d["lag_days"]))

    # driving_delayed_pred[pk] = pred_pk if propagated, else None
    classification: dict[
        str, str
    ] = {}  # pk → "root_cause" | "propagated" | "constraint" | "orphan"
    driving_pred_of: dict[str, str | None] = {}  # pk → driving delayed pred pk (propagated only)
    # driving graph for forward walk: delayed_pred → set of driven delayed succs
    drives: dict[str, set[str]] = defaultdict(set)
    # confidence for root_cause nodes only:
    #   "firm"  — has a binding FS/SS predecessor that is not delayed (origin is network-clear)
    #   "lower" — no FS/SS binding found (FF/SF-only preds or 7–14 day gap); origin inferred
    rc_confidence: dict[str, str] = {}

    for pk in delayed:
        task = task_map[pk]
        preds = preds_of.get(pk, [])

        if not preds:
            classification[pk] = "orphan"
            driving_pred_of[pk] = None
            continue

        # Find the predecessor with binding date closest to task.early_start
        es = task.early_start
        if es is None:
            classification[pk] = "orphan"
            driving_pred_of[pk] = None
            continue

        best_pred_pk: str | None = None
        best_delta: int | None = None  # days between binding and ES (≥ 0)
        min_gap: int | None = None  # smallest gap among all preds (to detect constraint)

        for pred_pk, dep_type, lag_days in preds:
            pred = task_map.get(pred_pk)
            if pred is None:
                continue
            bd = _binding_date(pred, dep_type, lag_days)
            if bd is None:
                continue
            delta = (es - bd).days  # positive = pred finishes before ES (normal)
            if delta < 0:
                continue  # pred binding is AFTER ES — data anomaly, skip
            if min_gap is None or delta < min_gap:
                min_gap = delta
            if delta <= BINDING_TOLERANCE:
                    if best_delta is None or delta < best_delta:
                    best_pred_pk = pred_pk
                    best_delta = delta

        # Determine classification
        if best_pred_pk is not None:
            # Network-driven delay
            if best_pred_pk in delayed:
                classification[pk] = "propagated"
                driving_pred_of[pk] = best_pred_pk
                drives[best_pred_pk].add(pk)
            else:
                classification[pk] = "root_cause"
                driving_pred_of[pk] = None
                rc_confidence[pk] = "firm"  # binding pred exists and is on-time
        elif min_gap is not None and min_gap > CONSTRAINT_GAP:
            # All predecessors finish well before ES — a date constraint is driving
            classification[pk] = "constraint"
            driving_pred_of[pk] = None
        else:
            # No FS/SS binding found (FF/SF-only preds, no CPM dates, or 7-14d gap)
            classification[pk] = "root_cause"
            driving_pred_of[pk] = None
            rc_confidence[pk] = "lower"  # origin inferred, not network-confirmed

    root_cause_pks = [
        pk for pk, cls in classification.items() if cls in ("root_cause", "constraint")
    ]

    downstream_counts: dict[str, int] = {}
    downstream_days: dict[str, int] = {}
    downstream_examples: dict[str, list[str]] = {}

    for rc_pk in root_cause_pks:
        visited: set[str] = set()
        queue = deque(drives.get(rc_pk, []))
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            queue.extend(drives.get(node, []))
        downstream_counts[rc_pk] = len(visited)
        # chain_delay_days = Σ(each downstream task's own finish_variance).
        # This is NOT "delay caused by this root cause" — each downstream task
        # accumulated its own delay on top of the propagated push.  Use it as a
        # rough scale indicator of how schedule-critical this chain is, not as a
        # causal attribution.
        downstream_days[rc_pk] = sum(delayed.get(n, 0) for n in visited)
        # Pick 3 most-delayed downstream tasks as examples
        sorted_ds = sorted(visited, key=lambda n: -delayed.get(n, 0))[:3]
        downstream_examples[rc_pk] = [task_map[n].name for n in sorted_ds if n in task_map]

    # Ranking by downstream_days would mis-rank: a root cause with 2 downstream
    # tasks each 400 days late beats one with 300 downstream tasks 10 days late.
    # Count is the honest primary ranking; chain_delay_days is a secondary scale
    # indicator shown with an explicit label.
    root_causes_sorted = sorted(
        root_cause_pks,
        key=lambda pk: (
            -downstream_counts.get(pk, 0),
            -downstream_days.get(pk, 0),
            -delayed.get(pk, 0),
        ),
    )[:25]

    # Count root causes whose binding could not be resolved via FS/SS
    # (all their predecessors are FF/SF or lack CPM dates).  These are
    # classified root_cause by the else-branch; the delay origin is less certain.
    n_no_fs_ss_binding = sum(
        1
        for pk in root_cause_pks
        if classification[pk] == "root_cause"
        and all(
            _binding_date(task_map[pred_pk], dt, lag) is None
            for pred_pk, dt, lag in preds_of.get(pk, [])
            if task_map.get(pred_pk) is not None
        )
    )

    root_causes_out = []
    for pk in root_causes_sorted:
        t = task_map[pk]
        root_causes_out.append(
            {
                "task_pk": pk,
                "activity_code": t.activity_code or "",
                "name": t.name,
                "trade": _csi_trade_resolved(t, override_map),
                "stage": t.stage or "",
                "stage_label": _STAGE_LABELS.get(t.stage or "", t.stage or ""),
                "activity_type": _activity_type(t.name),
                "classification": classification[pk],
                # "firm": binding FS/SS non-delayed predecessor confirmed.
                # "lower": no FS/SS binding (FF/SF-only preds or 7-14d gap);
                #          origin is inferred, not network-confirmed.
                "confidence": rc_confidence.get(pk, "lower"),
                "finish_variance_days": delayed[pk],
                "downstream_count": downstream_counts.get(pk, 0),
                # Labelled "chain_delay_days" to distinguish from causal attribution
                "chain_delay_days": downstream_days.get(pk, 0),
                "example_downstream": downstream_examples.get(pk, []),
            }
        )

    def _build_cluster(key_fn: object, label_fn: object) -> list[dict]:
        groups: dict[str, dict] = {}
        for pk in root_cause_pks:
            t = task_map[pk]
            key = key_fn(t)
            label = label_fn(key)
            if key not in groups:
                groups[key] = {
                    "key": key,
                    "label": label,
                    "root_cause_count": 0,
                    "downstream_tasks": 0,
                    "chain_delay_days": 0,
                    "examples": [],
                }
            g = groups[key]
            g["root_cause_count"] += 1
            g["downstream_tasks"] += downstream_counts.get(pk, 0)
            g["chain_delay_days"] += downstream_days.get(pk, 0)
            if len(g["examples"]) < 3:
                g["examples"].append(t.name)
        return sorted(groups.values(), key=lambda g: -g["downstream_tasks"])

    clusters_by_trade = _build_cluster(
        key_fn=lambda t: _csi_trade_resolved(t, override_map),
        label_fn=lambda k: k,
    )
    clusters_by_stage = _build_cluster(
        key_fn=lambda t: t.stage or "",
        label_fn=lambda k: _STAGE_LABELS.get(k, k or "General / Unassigned"),
    )
    clusters_by_type = _build_cluster(
        key_fn=lambda t: _activity_type(t.name),
        label_fn=lambda k: k,
    )

    n_root = sum(1 for c in classification.values() if c == "root_cause")
    n_root_firm = sum(1 for pk, v in rc_confidence.items() if v == "firm")
    n_root_lower = sum(1 for pk, v in rc_confidence.items() if v == "lower")
    n_constraint = sum(1 for c in classification.values() if c == "constraint")
    n_propagated = sum(1 for c in classification.values() if c == "propagated")
    n_orphan = sum(1 for c in classification.values() if c == "orphan")
    n_total_delayed = len(delayed)

    traceable = n_root + n_constraint + n_propagated
    traceable_pct = round(traceable / n_total_delayed * 100, 1) if n_total_delayed else 0.0
    orphan_pct = round(n_orphan / n_total_delayed * 100, 1) if n_total_delayed else 0.0

    top_cluster = max(clusters_by_trade, key=lambda g: g["downstream_tasks"], default=None)

    # Orphan task names (capped at 10)
    orphan_names = [task_map[pk].name for pk in delayed if classification.get(pk) == "orphan"][:10]

    logger.info(
        "Delay root-cause — project %s: %d delayed, %d root causes (%d no-FS/SS binding), "
        "%d propagated, %d constraint, %d orphan",
        project_id,
        n_total_delayed,
        n_root,
        n_no_fs_ss_binding,
        n_propagated,
        n_constraint,
        n_orphan,
    )

    return {
        "has_data": True,
        "summary": {
            "total_tasks": len(tasks),
            "excluded_no_baseline": excluded_no_baseline,
            "total_delayed": n_total_delayed,
            "root_causes": n_root,
            "root_causes_firm": n_root_firm,
            "root_causes_lower_confidence": n_root_lower,
            # kept for API compatibility — subset of root_causes_lower_confidence
            "root_causes_no_fs_ss_binding": n_no_fs_ss_binding,
            "constraint_driven": n_constraint,
            "propagated": n_propagated,
            "orphan": n_orphan,
            "traceable_pct": traceable_pct,
            "orphan_pct": orphan_pct,
            "top_cluster_label": top_cluster["label"] if top_cluster else None,
            "top_cluster_tasks": top_cluster["downstream_tasks"] if top_cluster else 0,
            "top_cluster_chain_days": top_cluster["chain_delay_days"] if top_cluster else 0,
        },
        "root_causes": root_causes_out,
        "clusters": {
            "by_trade": clusters_by_trade,
            "by_stage": clusters_by_stage,
            "by_activity_type": clusters_by_type,
        },
        "orphans": {
            "count": n_orphan,
            "tasks": orphan_names,
        },
        "as_of": today.isoformat(),
        "using_audit_overrides": bool(override_map),
        "n_overridden": len(override_map) if override_map else 0,
        "calendar_basis": "working days per project calendar"
        if has_calendar_data
        else "calendar days (no P6 calendar imported)",
    }
