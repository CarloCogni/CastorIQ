# scheduling/services/timeline_payload.py
"""Trusted-only 4D timeline payloads — Planned / Actual / Variance modes.

Truth+Context: fine paint buckets keep future/not-due geometry visible,
distinguish no-actual-data, and expose comparison sets + inspector index.
Classification rules (classify_*) are unchanged.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal

from ifc_processor.models import IFCEntity, IFCFile
from scheduling.models import Task
from scheduling.services.governance.reader import BindingGovernanceReader

logger = logging.getLogger(__name__)

SCHEMA_SUMMARY = "timeline.summary.v3"
SCHEMA_DETAIL = "timeline.interval_detail.v3"

Mode = Literal["planned", "actual", "variance"]
MODES: tuple[Mode, ...] = ("planned", "actual", "variance")
DEFAULT_MODE: Mode = "variance"

# Fine paint keys for Time View embed (all visible by default; no hide).
PAINT_KEYS = (
    "complete",
    "in_progress",
    "delayed",
    "delayed_late",
    "delayed_progress",
    "no_actual_mid",
    "not_due",
    "no_actual_data",
    "insufficient",
    "no_task",
)

# Legacy 4-bucket keys for older Links timeline consumers.
LEGACY_PAINT_KEYS = ("complete", "in_progress", "delayed", "not_started")

PLANNED_STATES = (
    "planned_in_progress",
    "planned_complete",
    "planned_not_started",
    "insufficient_planned",
)
PLANNED_SEVERITY = (
    "planned_in_progress",
    "planned_complete",
    "planned_not_started",
    "insufficient_planned",
)
PLANNED_PAINT = {
    "planned_complete": "complete",
    "planned_in_progress": "in_progress",
    "planned_not_started": "not_due",
    "insufficient_planned": "insufficient",
}
PLANNED_LABELS = {
    "planned_complete": "Planned complete",
    "planned_in_progress": "Planned in progress",
    "planned_not_started": "Planned not started",
    "insufficient_planned": "Incomplete planned dates",
    "no_task": "No linked schedule activity",
}

ACTUAL_STATES = (
    "actual_in_progress",
    "actual_complete",
    "actual_not_started",
    "no_actual_data",
)
ACTUAL_SEVERITY = (
    "actual_in_progress",
    "actual_complete",
    "actual_not_started",
    "no_actual_data",
)
ACTUAL_PAINT = {
    "actual_complete": "complete",
    "actual_in_progress": "in_progress",
    "actual_not_started": "not_due",
    "no_actual_data": "no_actual_data",
}
ACTUAL_LABELS = {
    "actual_complete": "Actually complete",
    "actual_in_progress": "Actually in progress",
    # Grey: actual evidence exists but starts after the playback date.
    "actual_not_started": "No actual progress by this date",
    # Purple: required Actual Start/Finish evidence is absent (both null).
    "no_actual_data": "No actual dates recorded",
    "no_task": "No linked schedule activity",
}

VARIANCE_STATES = (
    "completed_on_time",
    "completed_late",
    "in_progress",
    "delayed_in_progress",
    "planned_start_no_actual",
    "planned_finish_no_actual",
    "not_due",
    "insufficient_data",
)
VARIANCE_SEVERITY = (
    "delayed_in_progress",
    "planned_finish_no_actual",
    "planned_start_no_actual",
    "completed_late",
    "in_progress",
    "completed_on_time",
    "not_due",
    "insufficient_data",
)
VARIANCE_PAINT = {
    "completed_on_time": "complete",
    "completed_late": "delayed_late",
    "in_progress": "in_progress",
    "delayed_in_progress": "delayed_progress",
    "planned_start_no_actual": "no_actual_mid",
    "planned_finish_no_actual": "delayed",
    "not_due": "not_due",
    "insufficient_data": "insufficient",
}
VARIANCE_LABELS = {
    "completed_on_time": "Completed by planned finish",
    "completed_late": "Completed after planned finish",
    "in_progress": "In progress within planned period",
    "delayed_in_progress": "In progress past planned finish",
    "planned_start_no_actual": "Planned start passed — no actual update",
    "planned_finish_no_actual": "Planned finish passed — no actual update",
    "not_due": "Not started — not yet due",
    "insufficient_data": "Incomplete schedule dates",
    "no_task": "No linked schedule activity",
}

PAINT_COLORS = {
    "complete": "#10B981",
    "in_progress": "#3B82F6",
    "delayed": "#EF4444",
    "delayed_late": "#B91C1C",
    "delayed_progress": "#F97316",
    "no_actual_mid": "#F59E0B",
    "not_due": "#CBD5E1",
    "no_actual_data": "#7C3AED",
    "insufficient": "#A855F7",
    "no_task": "#64748B",
}

# Planned in-progress keeps amber active-window colour; Actual uses shared blue.
MODE_LEGEND_COLOR_OVERRIDE = {
    ("planned", "planned_in_progress"): "#F59E0B",
}

LEGACY_FOLD = {
    "complete": "complete",
    "in_progress": "in_progress",
    "delayed": "delayed",
    "delayed_late": "delayed",
    "delayed_progress": "delayed",
    "no_actual_mid": "delayed",
    "not_due": "not_started",
    "no_actual_data": "not_started",
    "insufficient": "not_started",
    "no_task": "not_started",
}


def parse_mode(raw: str | None) -> Mode:
    """Parse mode query value; default Variance."""
    if not raw:
        return DEFAULT_MODE
    key = raw.strip().lower()
    if key in MODES:
        return key  # type: ignore[return-value]
    return DEFAULT_MODE


def parse_snapshot_date(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD for interval detail; return None if invalid/missing."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _pick_severity(states: list[str], order: tuple[str, ...]) -> str:
    """Return the most severe state present; last order entry if empty."""
    if not states:
        return order[-1]
    rank = {name: i for i, name in enumerate(order)}
    return min(states, key=lambda s: rank.get(s, len(order)))


class TimelinePayloadService:
    """Build slim timeline summary and trusted-only interval detail by mode."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build_summary(self, mode: Mode = DEFAULT_MODE) -> dict[str, Any]:
        """Return full project scrubber range + linked-only playback queue.

        Project timeline (scrubber start/end) uses every valid task on the
        current ScheduleSource. Playback events / activities use unique
        trusted-linked tasks only.
        """
        mode = parse_mode(mode)
        ctx = self._prepare()
        if ctx is None:
            return {
                "schema": SCHEMA_SUMMARY,
                "mode": mode,
                "has_tasks": False,
                "trusted_only": True,
                "intervals": [],
                "no_task_count": 0,
                "linked_entity_count": 0,
                "project_task_count": 0,
                "playback_task_count": 0,
                "playback_event_count": 0,
                "playback_activities": [],
                "playback_events": [],
                "playback_steps": [],
                "playback_step_count": 0,
            }

        intervals: list[dict[str, Any]] = []
        current = ctx["min_date"]
        week_num = 1
        while current <= ctx["max_date"]:
            buckets = self._bucket(ctx, current, mode, include_gids=False)
            intervals.append(
                {
                    "date": current.isoformat(),
                    "label": f"Week {week_num}",
                    "stats": buckets["stats"],
                    "entities": {k: [] for k in LEGACY_PAINT_KEYS},
                }
            )
            current += timedelta(weeks=1)
            week_num += 1

        events = self._playback_events(ctx, mode)
        activities = self._playback_activities(ctx, mode)
        steps = self._playback_steps(ctx, mode, events)

        return {
            "schema": SCHEMA_SUMMARY,
            "mode": mode,
            "has_tasks": True,
            "trusted_only": True,
            "detail_required": True,
            "project_start": ctx["min_date"].isoformat(),
            "project_end": ctx["max_date"].isoformat(),
            "project_task_count": ctx["project_task_count"],
            "linked_entity_count": ctx["linked_count"],
            "playback_task_count": ctx["playback_task_count"],
            "playback_event_count": len(events),
            "playback_step_count": len(steps),
            "trusted_binding_count": ctx["trusted_binding_count"],
            "no_task_count": ctx["no_task_count"],
            "no_task": [],
            "intervals": intervals,
            "playback_activities": activities,
            "playback_events": events,
            "playback_steps": steps,
            "legend": self._legend_for_mode(mode),
        }

    def build_interval_detail(
        self,
        snapshot: date,
        *,
        mode: Mode = DEFAULT_MODE,
        include_no_task: bool = True,
        include_inspector_index: bool = True,
    ) -> dict[str, Any]:
        """Return paint buckets, state entities, comparison, inspector index."""
        mode = parse_mode(mode)
        ctx = self._prepare()
        empty_paint = {k: [] for k in PAINT_KEYS}
        empty_legacy = {k: [] for k in LEGACY_PAINT_KEYS}
        if ctx is None:
            return {
                "schema": SCHEMA_DETAIL,
                "mode": mode,
                "has_tasks": False,
                "trusted_only": True,
                "date": snapshot.isoformat(),
                "entities": empty_legacy,
                "paint": empty_paint,
                "state_entities": {},
                "stats": self._empty_stats(mode),
                "legend": self._legend_for_mode(mode),
                "comparison": self._empty_comparison(),
                "inspector_index": {},
                "task_info": {},
                "current_activity_ids": [],
                "activities_at_date": [],
                "activities_at_date_count": 0,
                "activities_at_date_state_counts": {},
                "activities_planned_count": 0,
                "activities_actual_count": 0,
                "activities_both_count": 0,
                "activities_planned_only_count": 0,
                "activities_actual_only_count": 0,
                "next_linked_activity_date": None,
                "week_index": 1,
                "week_total": 1,
                "project_task_count": 0,
                "playback_task_count": 0,
                "no_task": [],
                "no_task_count": 0,
            }

        buckets = self._bucket(ctx, snapshot, mode, include_gids=True)
        comparison = self._comparison_sets(ctx, snapshot)
        task_info = self._task_info_at_snapshot(ctx, snapshot) if include_inspector_index else {}
        at_date = self._activities_at_date(ctx, snapshot, mode)
        payload: dict[str, Any] = {
            "schema": SCHEMA_DETAIL,
            "mode": mode,
            "has_tasks": True,
            "trusted_only": True,
            "date": snapshot.isoformat(),
            "entities": buckets["legacy_entities"],
            "paint": buckets["entities"],
            "state_entities": buckets["state_entities"],
            "stats": buckets["stats"],
            "legend": self._legend_for_mode(mode),
            "comparison": comparison,
            "project_task_count": ctx["project_task_count"],
            "playback_task_count": ctx["playback_task_count"],
            "current_activity_ids": [row["task_id"] for row in at_date["activities"]],
            "activities_at_date": at_date["activities"],
            "activities_at_date_count": len(at_date["activities"]),
            "activities_at_date_state_counts": at_date["state_counts"],
            "activities_planned_count": at_date["planned_count"],
            "activities_actual_count": at_date["actual_count"],
            "activities_both_count": at_date["both_count"],
            "activities_planned_only_count": at_date["planned_only_count"],
            "activities_actual_only_count": at_date["actual_only_count"],
            "next_linked_activity_date": at_date["next_linked_activity_date"],
            "week_index": at_date["week_index"],
            "week_total": at_date["week_total"],
            "no_task_count": ctx["no_task_count"],
            "linked_entity_count": ctx["linked_count"],
            "data_quality": buckets.get("data_quality", {}),
            "hide_future_default": False,
        }
        if include_no_task:
            payload["no_task"] = list(ctx["no_task_gids"])
            payload["paint"]["no_task"] = list(ctx["no_task_gids"])
        else:
            payload["no_task"] = []
            payload["paint"]["no_task"] = []
        if include_inspector_index:
            payload["inspector_index"] = ctx["inspector_index"]
            payload["task_info"] = task_info
        else:
            payload["inspector_index"] = {}
            payload["task_info"] = {}
        return payload

    def _task_info_at_snapshot(
        self, ctx: dict[str, Any], snapshot: date
    ) -> dict[str, dict[str, Any]]:
        """Copy base task_info and attach Planned/Actual/Variance states at snapshot."""
        by_id = {str(k): v for k, v in ctx["task_map"].items()}
        out: dict[str, dict[str, Any]] = {}
        for tid, info in ctx["task_info"].items():
            task = by_id.get(str(tid))
            if task is None:
                out[tid] = dict(info)
                continue
            p_st, _ = self.classify_planned(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            a_st, _ = self.classify_actual(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            v_st, _ = self.classify_variance(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            row = dict(info)
            row["snapshot"] = snapshot.isoformat()
            row["planned_state"] = p_st
            row["planned_label"] = PLANNED_LABELS[p_st]
            row["actual_state"] = a_st
            row["actual_label"] = ACTUAL_LABELS[a_st]
            row["variance_state"] = v_st
            row["variance_label"] = VARIANCE_LABELS[v_st]
            out[tid] = row
        return out

    def _load_project_tasks(self) -> list:
        """Valid tasks for the current active ScheduleSource (full project timeline)."""
        base = (
            Task.objects.filter(project=self.project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
        )
        try:
            from scheduling.services.source_version.source_version import (
                ScheduleSourceVersionService,
            )

            current = ScheduleSourceVersionService(self.project).get_current()
        except Exception:
            current = None
        if current is not None:
            scoped = list(base.filter(source_version_id=current.pk))
            if scoped:
                return scoped
            if current.schedule_source_id:
                scoped = list(base.filter(schedule_source_id=current.schedule_source_id))
                if scoped:
                    return scoped
        return list(base)

    def _prepare(self) -> dict[str, Any] | None:
        tasks = self._load_project_tasks()
        if not tasks:
            return None

        task_map = {t.pk: t for t in tasks}
        reader = BindingGovernanceReader(self.project_id)
        gids_by_task = reader.entity_gids_by_task([t.pk for t in tasks], trusted_only=True)

        entity_tasks: dict[str, list] = defaultdict(list)
        inspector_index: dict[str, str] = {}
        entity_task_ids: dict[str, list[str]] = defaultdict(list)
        fanout: dict[str, int] = defaultdict(int)
        trusted_binding_count = 0
        for task in tasks:
            tid = str(task.pk)
            for gid in gids_by_task.get(tid, []):
                trusted_binding_count += 1
                entity_tasks[gid].append(
                    (task.start_date, task.end_date, task.actual_start, task.actual_end, tid)
                )
                inspector_index[gid] = tid
                if tid not in entity_task_ids[gid]:
                    entity_task_ids[gid].append(tid)
                fanout[tid] += 1

        linked_tasks = [t for t in tasks if fanout.get(str(t.pk))]
        linked_tasks.sort(
            key=lambda t: (
                t.start_date or date.max,
                t.activity_code or "",
                t.name or "",
                str(t.pk),
            )
        )

        task_info: dict[str, dict[str, Any]] = {}
        for task in linked_tasks:
            tid = str(task.pk)
            task_info[tid] = {
                "task_id": tid,
                "name": task.name,
                "activity_code": task.activity_code,
                "planned_start": task.start_date.isoformat() if task.start_date else None,
                "planned_finish": task.end_date.isoformat() if task.end_date else None,
                "actual_start": task.actual_start.isoformat() if task.actual_start else None,
                "actual_finish": task.actual_end.isoformat() if task.actual_end else None,
                "fanout": fanout[tid],
                "entity_gids": list(gids_by_task.get(tid, [])),
                "governance": "trusted",
                "is_active": True,
            }

        ifc_files = IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
        all_gids: list[str] = list(
            IFCEntity.objects.filter(ifc_file__in=ifc_files)
            .values_list("global_id", flat=True)
            .iterator(chunk_size=1000)
        ) or list(entity_tasks.keys())

        linked_set = set(entity_tasks.keys())
        no_task_gids = [gid for gid in all_gids if gid not in linked_set]
        task_gids = [gid for gid in all_gids if gid in linked_set]

        return {
            "tasks": tasks,
            "linked_tasks": linked_tasks,
            "task_map": task_map,
            "entity_tasks": entity_tasks,
            "entity_task_ids": dict(entity_task_ids),
            "gids_by_task": {str(k): list(v) for k, v in gids_by_task.items() if v},
            "task_gids": task_gids,
            "no_task_gids": no_task_gids,
            "total": len(all_gids),
            "linked_count": len(task_gids),
            "no_task_count": len(no_task_gids),
            "project_task_count": len(tasks),
            "playback_task_count": len(linked_tasks),
            "trusted_binding_count": trusted_binding_count,
            "min_date": min(t.start_date for t in tasks),
            "max_date": max(t.end_date for t in tasks),
            "inspector_index": inspector_index,
            "task_info": task_info,
        }

    def _event_dates_for_task(self, task, mode: Mode) -> list[date]:
        """Mode-specific linked-task event dates (no planned fallback for Actual)."""
        dates: list[date] = []
        if mode == "planned":
            if task.start_date:
                dates.append(task.start_date)
            if task.end_date and task.end_date != task.start_date:
                dates.append(task.end_date)
        elif mode == "actual":
            if task.actual_start:
                dates.append(task.actual_start)
            if task.actual_end and task.actual_end != task.actual_start:
                dates.append(task.actual_end)
        else:
            if task.start_date:
                dates.append(task.start_date)
            if task.end_date and task.end_date != task.start_date:
                dates.append(task.end_date)
            if task.actual_start and task.actual_start not in dates:
                dates.append(task.actual_start)
            if task.actual_end and task.actual_end not in dates:
                dates.append(task.actual_end)
        return dates

    def _playback_events(self, ctx: dict[str, Any], mode: Mode) -> list[dict[str, Any]]:
        """Distinct linked-event dates with every activity that fires that day."""
        by_date: dict[date, list[str]] = defaultdict(list)
        by_id = {str(t.pk): t for t in ctx["linked_tasks"]}
        lo, hi = ctx["min_date"], ctx["max_date"]
        for task in ctx["linked_tasks"]:
            tid = str(task.pk)
            for d in self._event_dates_for_task(task, mode):
                if d < lo or d > hi:
                    continue
                if tid not in by_date[d]:
                    by_date[d].append(tid)
        events: list[dict[str, Any]] = []
        for d, tids in sorted(by_date.items(), key=lambda item: item[0]):
            tids_sorted = sorted(
                tids,
                key=lambda tid: (
                    (by_id[tid].activity_code or "") if tid in by_id else "",
                    (by_id[tid].name or "") if tid in by_id else "",
                    tid,
                ),
            )
            events.append(
                {
                    "date": d.isoformat(),
                    "task_ids": tids_sorted,
                    "activity_count": len(tids_sorted),
                }
            )
        return events

    def _playback_steps(
        self,
        ctx: dict[str, Any],
        mode: Mode,
        events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """One step per linked activity on each event date — Now Playing sequence."""
        if events is None:
            events = self._playback_events(ctx, mode)
        labels = self._labels(mode)
        by_id = {str(t.pk): t for t in ctx["linked_tasks"]}
        steps: list[dict[str, Any]] = []
        for event in events:
            tids = event["task_ids"]
            same_count = len(tids)
            snap = date.fromisoformat(event["date"])
            for same_idx, tid in enumerate(tids, start=1):
                task = by_id.get(tid)
                info = ctx["task_info"].get(tid) or {}
                if task is not None:
                    st, _ = self._classifier(mode)(
                        task.start_date,
                        task.end_date,
                        task.actual_start,
                        task.actual_end,
                        snap,
                    )
                else:
                    st = ""
                steps.append(
                    {
                        "date": event["date"],
                        "task_id": tid,
                        "activity_code": info.get("activity_code") or "",
                        "name": info.get("name") or "",
                        "planned_start": info.get("planned_start"),
                        "planned_finish": info.get("planned_finish"),
                        "actual_start": info.get("actual_start"),
                        "actual_finish": info.get("actual_finish"),
                        "element_count": info.get("fanout") or 0,
                        "mode_state": st,
                        "mode_label": labels.get(st, st),
                        "same_date_index": same_idx,
                        "same_date_count": same_count,
                    }
                )
        total = len(steps)
        for i, step in enumerate(steps):
            step["sequence_index"] = i + 1
            step["sequence_total"] = total
        return steps

    def _playback_activities(self, ctx: dict[str, Any], mode: Mode) -> list[dict[str, Any]]:
        """Unique trusted linked activities in Planned-Start chronological order."""
        rows: list[dict[str, Any]] = []
        for seq, task in enumerate(ctx["linked_tasks"], start=1):
            tid = str(task.pk)
            info = ctx["task_info"][tid]
            p_st, _ = self.classify_planned(
                task.start_date, task.end_date, task.actual_start, task.actual_end, ctx["min_date"]
            )
            a_st, _ = self.classify_actual(
                task.start_date, task.end_date, task.actual_start, task.actual_end, ctx["min_date"]
            )
            v_st, _ = self.classify_variance(
                task.start_date, task.end_date, task.actual_start, task.actual_end, ctx["min_date"]
            )
            state = p_st if mode == "planned" else a_st if mode == "actual" else v_st
            labels = self._labels(mode)
            rows.append(
                {
                    "seq": seq,
                    "task_id": tid,
                    "activity_code": info.get("activity_code") or "",
                    "name": info.get("name") or "",
                    "planned_start": info.get("planned_start"),
                    "planned_finish": info.get("planned_finish"),
                    "actual_start": info.get("actual_start"),
                    "actual_finish": info.get("actual_finish"),
                    "element_count": info.get("fanout") or 0,
                    "mode_state": state,
                    "mode_label": labels.get(state, state),
                    "relevant_event": (
                        info.get("planned_start")
                        if mode != "actual"
                        else (info.get("actual_start") or info.get("actual_finish"))
                    ),
                }
            )
        return rows

    def _in_planned_window(self, task, snapshot: date) -> bool:
        """planned_start <= selected_date <= planned_finish."""
        if task.start_date is None or task.end_date is None:
            return False
        if task.end_date < task.start_date:
            return False
        return task.start_date <= snapshot <= task.end_date

    def _in_actual_window(self, task, snapshot: date) -> bool:
        """actual_start <= selected <= actual_finish, or open-ended after actual_start."""
        if task.actual_start is None:
            return False
        if task.actual_start > snapshot:
            return False
        if task.actual_end is None:
            return True
        return snapshot <= task.actual_end

    def _is_visible_at_date(self, task, snapshot: date, mode: Mode) -> tuple[bool, str]:
        """Legacy helper — panel now uses planned/actual windows; keep for callers."""
        _ = mode
        in_p = self._in_planned_window(task, snapshot)
        in_a = self._in_actual_window(task, snapshot)
        if not in_p and not in_a:
            return False, ""
        st, _ = self.classify_variance(
            task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
        )
        return True, st

    def _activities_at_date(
        self, ctx: dict[str, Any], snapshot: date, mode: Mode
    ) -> dict[str, Any]:
        """Linked activities relevant at snapshot — Planned and/or Actual windows.

        De-duplicates tasks present in both windows as membership
        ``planned_and_actual``. Unlinked tasks never appear. Mode only affects
        which classifier label is exposed as ``mode_state`` for the model mode
        chrome; both planned and actual memberships are always computed.
        """
        _ = mode
        labels_p = self._labels("planned")
        labels_a = self._labels("actual")
        labels_v = self._labels("variance")
        rows: list[dict[str, Any]] = []
        state_counts: dict[str, int] = defaultdict(int)
        for task in ctx["linked_tasks"]:
            in_planned = self._in_planned_window(task, snapshot)
            in_actual = self._in_actual_window(task, snapshot)
            if not in_planned and not in_actual:
                continue
            tid = str(task.pk)
            info = ctx["task_info"][tid]
            p_st, _ = self.classify_planned(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            a_st, _ = self.classify_actual(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            v_st, _ = self.classify_variance(
                task.start_date, task.end_date, task.actual_start, task.actual_end, snapshot
            )
            if in_planned and in_actual:
                membership = "planned_and_actual"
            elif in_planned:
                membership = "planned"
            else:
                membership = "actual"
            mode_state = p_st if mode == "planned" else a_st if mode == "actual" else v_st
            mode_labels = (
                labels_p if mode == "planned" else labels_a if mode == "actual" else labels_v
            )
            state_counts[mode_state] += 1
            rows.append(
                {
                    "task_id": tid,
                    "activity_code": info.get("activity_code") or "",
                    "name": info.get("name") or "",
                    "planned_start": info.get("planned_start"),
                    "planned_finish": info.get("planned_finish"),
                    "actual_start": info.get("actual_start"),
                    "actual_finish": info.get("actual_finish"),
                    "element_count": info.get("fanout") or 0,
                    "membership": membership,
                    "in_planned": in_planned,
                    "in_actual": in_actual,
                    "planned_state": p_st,
                    "planned_label": labels_p.get(p_st, p_st),
                    "actual_state": a_st,
                    "actual_label": labels_a.get(a_st, a_st),
                    "variance_state": v_st,
                    "variance_label": labels_v.get(v_st, v_st),
                    "mode_state": mode_state,
                    "mode_label": mode_labels.get(mode_state, mode_state),
                }
            )

        membership_rank = {"planned_and_actual": 0, "planned": 1, "actual": 2}
        rows.sort(
            key=lambda r: (
                membership_rank.get(r["membership"], 9),
                r.get("activity_code") or "",
                r.get("name") or "",
                r["task_id"],
            )
        )

        next_date = None
        for event in self._playback_events(ctx, mode):
            if event["date"] > snapshot.isoformat():
                next_date = event["date"]
                break

        week_index = ((snapshot - ctx["min_date"]).days // 7) + 1
        week_total = ((ctx["max_date"] - ctx["min_date"]).days // 7) + 1

        both_count = sum(1 for r in rows if r["membership"] == "planned_and_actual")
        planned_only_count = sum(1 for r in rows if r["membership"] == "planned")
        actual_only_count = sum(1 for r in rows if r["membership"] == "actual")
        return {
            "activities": rows,
            "state_counts": dict(state_counts),
            "next_linked_activity_date": next_date,
            "planned_count": sum(1 for r in rows if r["in_planned"]),
            "actual_count": sum(1 for r in rows if r["in_actual"]),
            "both_count": both_count,
            "planned_only_count": planned_only_count,
            "actual_only_count": actual_only_count,
            "week_index": week_index,
            "week_total": week_total,
        }

    def _current_activity_ids(self, ctx: dict[str, Any], snapshot: date, mode: Mode) -> list[str]:
        """Task IDs visible in the Activities-at-this-date panel."""
        return [
            row["task_id"] for row in self._activities_at_date(ctx, snapshot, mode)["activities"]
        ]

    def _bucket(
        self,
        ctx: dict[str, Any],
        snapshot: date,
        mode: Mode,
        *,
        include_gids: bool,
    ) -> dict[str, Any]:
        classify = self._classifier(mode)
        severity = self._severity(mode)
        paint_map = self._paint_map(mode)
        state_keys = self._state_keys(mode)

        paint_lists: dict[str, list[str]] = {k: [] for k in PAINT_KEYS}
        legacy_lists: dict[str, list[str]] = {k: [] for k in LEGACY_PAINT_KEYS}
        state_lists: dict[str, list[str]] = {k: [] for k in state_keys}
        paint_counts = {k: 0 for k in PAINT_KEYS}
        state_counts = {k: 0 for k in state_keys}
        missing_actual_start_with_finish = 0

        for gid in ctx["task_gids"]:
            task_states: list[str] = []
            for row in ctx["entity_tasks"][gid]:
                s, e, a_s, a_e = row[0], row[1], row[2], row[3]
                st, dq = classify(s, e, a_s, a_e, snapshot)
                task_states.append(st)
                if dq.get("actual_finish_without_start"):
                    missing_actual_start_with_finish += 1
            chosen = _pick_severity(task_states, severity)
            paint = paint_map[chosen]
            paint_counts[paint] += 1
            state_counts[chosen] += 1
            if include_gids:
                paint_lists[paint].append(gid)
                state_lists[chosen].append(gid)
                legacy_lists[LEGACY_FOLD[paint]].append(gid)

        legacy_counts = {k: 0 for k in LEGACY_PAINT_KEYS}
        for paint, n in paint_counts.items():
            if paint == "no_task":
                continue
            legacy_counts[LEGACY_FOLD[paint]] += n

        stats = {
            "total": ctx["total"],
            "no_task": ctx["no_task_count"],
            "linked": ctx["linked_count"],
            "complete": legacy_counts["complete"],
            "in_progress": legacy_counts["in_progress"],
            "delayed": legacy_counts["delayed"],
            "not_started": legacy_counts["not_started"] + ctx["no_task_count"],
            "mode_states": dict(state_counts),
            "paint_counts": dict(paint_counts),
        }
        for key, count in state_counts.items():
            stats[key] = count

        return {
            "entities": paint_lists if include_gids else {k: [] for k in PAINT_KEYS},
            "legacy_entities": legacy_lists if include_gids else {k: [] for k in LEGACY_PAINT_KEYS},
            "state_entities": state_lists if include_gids else {k: [] for k in state_keys},
            "stats": stats,
            "data_quality": {
                "actual_finish_without_start_task_hits": missing_actual_start_with_finish,
            },
        }

    def _comparison_sets(self, ctx: dict[str, Any], snapshot: date) -> dict[str, Any]:
        """Element-set comparison at snapshot — not unrelated total subtraction."""
        planned_complete: set[str] = set()
        actual_complete: set[str] = set()
        no_actual_evidence: set[str] = set()

        for gid in ctx["task_gids"]:
            p_states = [
                self.classify_planned(s, e, a_s, a_e, snapshot)[0]
                for s, e, a_s, a_e, _tid in (
                    (row[0], row[1], row[2], row[3], row[4]) for row in ctx["entity_tasks"][gid]
                )
            ]
            a_states = [
                self.classify_actual(s, e, a_s, a_e, snapshot)[0]
                for s, e, a_s, a_e, _tid in (
                    (row[0], row[1], row[2], row[3], row[4]) for row in ctx["entity_tasks"][gid]
                )
            ]
            p_chosen = _pick_severity(p_states, PLANNED_SEVERITY)
            a_chosen = _pick_severity(a_states, ACTUAL_SEVERITY)
            if p_chosen == "planned_complete":
                planned_complete.add(gid)
            if a_chosen == "actual_complete":
                actual_complete.add(gid)
            if a_chosen == "no_actual_data":
                no_actual_evidence.add(gid)

        plan_not_actual = planned_complete - actual_complete
        return {
            "date": snapshot.isoformat(),
            "unit": "elements",
            "planned_complete": len(planned_complete),
            "actual_complete": len(actual_complete),
            "plan_complete_not_actual_complete": len(plan_not_actual),
            "no_actual_evidence": len(no_actual_evidence),
        }

    @staticmethod
    def _empty_comparison() -> dict[str, Any]:
        return {
            "date": None,
            "unit": "elements",
            "planned_complete": 0,
            "actual_complete": 0,
            "plan_complete_not_actual_complete": 0,
            "no_actual_evidence": 0,
        }

    def _classifier(self, mode: Mode):
        if mode == "planned":
            return self.classify_planned
        if mode == "actual":
            return self.classify_actual
        return self.classify_variance

    def _severity(self, mode: Mode) -> tuple[str, ...]:
        if mode == "planned":
            return PLANNED_SEVERITY
        if mode == "actual":
            return ACTUAL_SEVERITY
        return VARIANCE_SEVERITY

    def _paint_map(self, mode: Mode) -> dict[str, str]:
        if mode == "planned":
            return PLANNED_PAINT
        if mode == "actual":
            return ACTUAL_PAINT
        return VARIANCE_PAINT

    def _state_keys(self, mode: Mode) -> tuple[str, ...]:
        if mode == "planned":
            return PLANNED_STATES
        if mode == "actual":
            return ACTUAL_STATES
        return VARIANCE_STATES

    def _labels(self, mode: Mode) -> dict[str, str]:
        if mode == "planned":
            return PLANNED_LABELS
        if mode == "actual":
            return ACTUAL_LABELS
        return VARIANCE_LABELS

    def _legend_for_mode(self, mode: Mode) -> list[dict[str, Any]]:
        labels = self._labels(mode)
        paint_map = self._paint_map(mode)
        perf_keys = {
            "completed_on_time",
            "completed_late",
            "in_progress",
            "delayed_in_progress",
            "planned_start_no_actual",
            "planned_finish_no_actual",
        }
        items: list[dict[str, Any]] = []
        for key in self._state_keys(mode):
            paint = paint_map[key]
            color = MODE_LEGEND_COLOR_OVERRIDE.get((mode, key), PAINT_COLORS[paint])
            item: dict[str, Any] = {
                "key": key,
                "label": labels[key],
                "paint": paint,
                "color": color,
                "border": paint in ("not_due", "no_task"),
                "muted": paint == "not_due",
            }
            if mode == "variance":
                item["group"] = "schedule_performance" if key in perf_keys else "data_model_context"
            items.append(item)
        items.append(
            {
                "key": "no_task",
                "label": labels["no_task"],
                "paint": "no_task",
                "color": PAINT_COLORS["no_task"],
                "border": True,
                "muted": False,
                **({"group": "data_model_context"} if mode == "variance" else {}),
            }
        )
        return items

    def _empty_stats(self, mode: Mode) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total": 0,
            "complete": 0,
            "in_progress": 0,
            "delayed": 0,
            "not_started": 0,
            "no_task": 0,
            "linked": 0,
            "mode_states": {},
            "paint_counts": {k: 0 for k in PAINT_KEYS},
        }
        for key in self._state_keys(mode):
            stats[key] = 0
            stats["mode_states"][key] = 0
        return stats

    @staticmethod
    def classify_planned(
        start_date: date | None,
        end_date: date | None,
        actual_start: date | None,
        actual_end: date | None,
        snapshot: date,
    ) -> tuple[str, dict[str, bool]]:
        """Programme-only state. Ignores actual_* entirely."""
        _ = (actual_start, actual_end)
        dq: dict[str, bool] = {}
        if start_date is None or end_date is None or end_date < start_date:
            return "insufficient_planned", dq
        if snapshot < start_date:
            return "planned_not_started", dq
        if start_date <= snapshot < end_date:
            return "planned_in_progress", dq
        return "planned_complete", dq

    @staticmethod
    def classify_actual(
        start_date: date | None,
        end_date: date | None,
        actual_start: date | None,
        actual_end: date | None,
        snapshot: date,
    ) -> tuple[str, dict[str, bool]]:
        """Actual dated evidence only — never falls back to planned dates."""
        _ = (start_date, end_date)
        dq: dict[str, bool] = {}
        if actual_end is not None and actual_start is None:
            dq["actual_finish_without_start"] = True
            if actual_end <= snapshot:
                return "actual_complete", dq
            return "no_actual_data", dq

        if actual_end is not None and actual_end <= snapshot:
            return "actual_complete", dq

        if actual_start is not None and actual_start <= snapshot:
            if actual_end is None or actual_end > snapshot:
                return "actual_in_progress", dq

        if actual_start is not None and actual_start > snapshot:
            return "actual_not_started", dq

        return "no_actual_data", dq

    @staticmethod
    def classify_variance(
        start_date: date | None,
        end_date: date | None,
        actual_start: date | None,
        actual_end: date | None,
        snapshot: date,
    ) -> tuple[str, dict[str, bool]]:
        """Compare actual dated evidence to planned dates at snapshot."""
        dq: dict[str, bool] = {}
        if start_date is None or end_date is None or end_date < start_date:
            return "insufficient_data", dq

        finished = actual_end is not None and actual_end <= snapshot
        started = actual_start is not None and actual_start <= snapshot

        if finished:
            if actual_end is not None and actual_end <= end_date:
                return "completed_on_time", dq
            return "completed_late", dq

        if started:
            if snapshot <= end_date:
                return "in_progress", dq
            return "delayed_in_progress", dq

        # No usable actual evidence at snapshot.
        if snapshot < start_date:
            return "not_due", dq
        if snapshot <= end_date:
            return "planned_start_no_actual", dq
        return "planned_finish_no_actual", dq

    @staticmethod
    def _task_state(
        start_date: date,
        end_date: date,
        actual_start: date | None,
        actual_end: date | None,
        snapshot: date,
    ) -> str:
        """Deprecated: returns legacy paint-bucket name under Variance rules."""
        state, _ = TimelinePayloadService.classify_variance(
            start_date, end_date, actual_start, actual_end, snapshot
        )
        fine = VARIANCE_PAINT[state]
        return LEGACY_FOLD.get(fine, fine)
