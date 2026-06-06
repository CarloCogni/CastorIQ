# castor/ifc_insights/services/metrics.py
"""Entity-level metrics computed from the IFCEntity queryset for the Insights dashboard."""

from __future__ import annotations

import math
from collections import defaultdict

from django.db.models import Count

from ifc_processor.models import IFCEntity

_CIRC = round(2 * math.pi * 36, 2)  # SVG donut circumference, radius=36


def _ring(pct: int) -> dict:
    """Precompute SVG stroke-dasharray and color class for a readiness ring."""
    filled = round(pct / 100 * _CIRC, 2)
    gap = round(_CIRC - filled, 2)
    if pct >= 80:
        color = "#22c55e"
    elif pct >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"
    return {"pct": pct, "dasharray": f"{filled} {gap}", "color": color}


def _build_schedule_cost_map(ifc_file) -> dict[str, float]:
    """Return {entity_global_id: task_cost} for entities linked to tasks that have cost set.

    Used to implement schedule cost priority over IFC property cost.
    Returns an empty dict if the scheduling app is unavailable or no tasks have cost.
    """
    try:
        from scheduling.models import Task as ScheduleTask

        cost_map: dict[str, float] = {}
        for task in (
            ScheduleTask.objects.filter(project=ifc_file.project, cost__isnull=False)
            .only("cost")
            .prefetch_related("ifc_entities")
        ):
            task_cost = float(task.cost)
            for entity in task.ifc_entities.filter(ifc_file=ifc_file).only("global_id"):
                cost_map[entity.global_id] = task_cost
        return cost_map
    except Exception:
        return {}


def entity_metrics(ifc_file) -> dict:
    """Compute all Insights dashboard metrics in a single pass over IFCEntity rows.

    Returns a dict ready to be merged into view context.
    """
    qs = IFCEntity.objects.filter(ifc_file=ifc_file)
    total = qs.count()

    if total == 0:
        zero_ring = _ring(0)
        return {
            "total_entities": 0,
            "ring_4d": zero_ring,
            "ring_5d": zero_ring,
            "activity_table": [],
            "has_cost_data": False,
            "total_cost": 0.0,
            "cost_by_type": [],
            "missing_4d": 0,
            "missing_5d": 0,
        }

    # Schedule cost map: global_id → float cost (overrides IFC property cost)
    schedule_cost_map = _build_schedule_cost_map(ifc_file)

    fourd_hits = 0
    fived_hits = 0
    activity_groups: dict[tuple, int] = {}
    cost_by_type: dict[str, float] = defaultdict(float)
    total_cost = 0.0

    for entity in qs.only("global_id", "ifc_type", "properties").iterator(chunk_size=500):
        props = entity.properties or {}
        act_id = None
        act_name = None
        has_cost = False
        entity_cost = 0.0

        for k, v in props.items():
            if not v:
                continue
            kl = k.lower()
            if kl.endswith("activity id"):
                act_id = str(v).strip()
            elif kl.endswith("activity name"):
                act_name = str(v).strip()
            if kl.endswith("cost"):  # catches both "cost" and "unit cost"
                try:
                    entity_cost += float(str(v).replace(",", ""))
                    has_cost = True
                except (TypeError, ValueError):
                    pass

        if act_id:
            fourd_hits += 1
            key = (act_id, act_name or "—", entity.ifc_type or "Unknown")
            activity_groups[key] = activity_groups.get(key, 0) + 1

        # Priority: schedule cost > IFC cost
        sched_cost = schedule_cost_map.get(entity.global_id)
        if sched_cost is not None:
            fived_hits += 1
            total_cost += sched_cost
            cost_by_type[entity.ifc_type or "Unknown"] += sched_cost
        elif has_cost and entity_cost:
            fived_hits += 1
            total_cost += entity_cost
            cost_by_type[entity.ifc_type or "Unknown"] += entity_cost

    fourd_pct = round(fourd_hits / total * 100)
    fived_pct = round(fived_hits / total * 100)

    activity_table = sorted(
        [
            {"act_id": k[0], "act_name": k[1], "ifc_type": k[2], "count": v}
            for k, v in activity_groups.items()
        ],
        key=lambda x: -x["count"],
    )[:20]

    cost_table = sorted(
        [{"ifc_type": t, "cost": c} for t, c in cost_by_type.items()],
        key=lambda x: -x["cost"],
    )[:10]

    return {
        "total_entities": total,
        "ring_4d": _ring(fourd_pct),
        "ring_5d": _ring(fived_pct),
        "activity_table": activity_table,
        "has_cost_data": total_cost > 0,
        "total_cost": total_cost,
        "cost_by_type": cost_table,
        "missing_4d": total - fourd_hits,
        "missing_5d": total - fived_hits,
    }


def non_physical_metrics(project) -> dict:
    """Compute non-physical task statistics for the Insights dashboard panel.

    Returns np_total, np_auto (auto-detected), np_locked (manually set),
    np_by_type (top activity types), np_by_stage (stage distribution).
    """
    try:
        from scheduling.models import Task

        qs = Task.objects.filter(project=project, is_non_physical=True)
        total = qs.count()

        if total == 0:
            return {
                "np_total": 0,
                "np_auto": 0,
                "np_locked": 0,
                "np_by_type": [],
                "np_by_stage": [],
            }

        np_locked = qs.filter(non_physical_locked=True).count()

        np_by_type = [
            {"activity_type": t, "count": c}
            for t, c in (
                qs.exclude(activity_type="")
                .values_list("activity_type")
                .annotate(cnt=Count("activity_type"))
                .order_by("-cnt")
                .values_list("activity_type", "cnt")[:6]
            )
        ]

        np_by_stage = [
            {"stage": s, "count": c}
            for s, c in (
                qs.exclude(stage="")
                .values_list("stage")
                .annotate(cnt=Count("stage"))
                .order_by("-cnt")
                .values_list("stage", "cnt")[:6]
            )
        ]

        return {
            "np_total": total,
            "np_auto": total - np_locked,
            "np_locked": np_locked,
            "np_by_type": np_by_type,
            "np_by_stage": np_by_stage,
        }
    except Exception:
        return {"np_total": 0, "np_auto": 0, "np_locked": 0, "np_by_type": [], "np_by_stage": []}


def schedule_progress_metrics(project, stage: str = "", sub_stage: str = "") -> dict:
    """Compute weighted schedule completion % for the Schedule Progress ring.

    Reads the project's ProgressMode (defaults to 'count').
    If stage is given, only tasks with that stage value are included.
    If sub_stage is given, tasks are further filtered to that sub_stage.
    A task counts as complete when status == COMPLETE or actual_end is set.
    Returns keys: ring_progress, progress_mode, progress_stage, progress_sub_stage,
    progress_complete, progress_total.
    """
    try:
        from scheduling.models import ProgressMode, Task

        mode_obj = ProgressMode.objects.filter(project=project).first()
        mode: str = mode_obj.mode if mode_obj else ProgressMode.Mode.BY_COUNT

        qs = Task.objects.filter(project=project, is_non_physical=False)
        if stage:
            qs = qs.filter(stage=stage)
        if sub_stage:
            qs = qs.filter(sub_stage=sub_stage)
        tasks = list(qs.only("status", "actual_end", "cost", "start_date", "end_date", "weight"))

        if not tasks:
            return {
                "ring_progress": _ring(0),
                "progress_mode": mode,
                "progress_stage": stage,
                "progress_sub_stage": sub_stage,
                "progress_complete": 0,
                "progress_total": 0,
            }

        complete_status = Task.Status.COMPLETE

        def is_done(t: Task) -> bool:
            return t.status == complete_status or t.actual_end is not None

        if mode == ProgressMode.Mode.BY_COST:
            total_w = sum(float(t.cost or 0) for t in tasks) or float(len(tasks))
            done_w = sum(float(t.cost or 0) for t in tasks if is_done(t))
        elif mode == ProgressMode.Mode.BY_DURATION:
            total_w = float(sum(max(1, (t.end_date - t.start_date).days) for t in tasks))
            done_w = float(
                sum(max(1, (t.end_date - t.start_date).days) for t in tasks if is_done(t))
            )
        elif mode == ProgressMode.Mode.BY_WEIGHT:
            total_w = sum(t.weight for t in tasks) or float(len(tasks))
            done_w = sum(t.weight for t in tasks if is_done(t))
        else:  # count (default)
            total_w = float(len(tasks))
            done_w = float(sum(1 for t in tasks if is_done(t)))

        pct = round(done_w / total_w * 100) if total_w > 0 else 0
        complete_count = sum(1 for t in tasks if is_done(t))

        return {
            "ring_progress": _ring(pct),
            "progress_mode": mode,
            "progress_stage": stage,
            "progress_sub_stage": sub_stage,
            "progress_complete": complete_count,
            "progress_total": len(tasks),
        }
    except Exception:
        return {
            "ring_progress": _ring(0),
            "progress_mode": "count",
            "progress_stage": "",
            "progress_sub_stage": "",
            "progress_complete": 0,
            "progress_total": 0,
        }


def breakdown_data(ifc_file, breakdown_type: str) -> dict:
    """Compute breakdown card rows for a single dimension.

    Returns {"title": str, "rows": list[{"label", "count", "pct"}]}
    """
    qs = IFCEntity.objects.filter(ifc_file=ifc_file)

    if breakdown_type == "level":
        data = qs.values("spatial_container").annotate(count=Count("id")).order_by("-count")[:6]
        rows = [{"label": r["spatial_container"] or "—", "count": r["count"]} for r in data]
        title = "By Level"

    elif breakdown_type == "element_type":
        data = qs.values("ifc_type").annotate(count=Count("id")).order_by("-count")[:6]
        rows = [{"label": r["ifc_type"] or "—", "count": r["count"]} for r in data]
        title = "By Element Type"

    elif breakdown_type == "material":
        mat_counts: dict[str, int] = defaultdict(int)
        for entity in qs.only("properties").iterator(chunk_size=500):
            props = entity.properties or {}
            for k, v in props.items():
                if k.lower().endswith("material") and v:
                    mat_counts[str(v).strip()] += 1
                    break
        rows = sorted(
            [{"label": k, "count": v} for k, v in mat_counts.items()],
            key=lambda x: -x["count"],
        )[:6]
        title = "By Material"

    else:
        return {"title": "Unknown", "rows": []}

    max_count = max((r["count"] for r in rows), default=1)
    for r in rows:
        r["pct"] = round(r["count"] / max_count * 100)

    return {"title": title, "rows": rows}
