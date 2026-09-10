# scheduling/services/timeline_payload.py
"""Applied/confirmed-only 4D timeline payloads — summary-first with lazy interval detail.

Reliability Patch A″.2: the legacy TimelineView embedded every linked GlobalId
in every weekly interval (plus a full no_task list), producing ~30MB+ JSON on
large pilots. Summary responses keep scrubber stats only; interval detail loads
GlobalId buckets for one snapshot date on demand.

Main-compatible trust filter: ``TaskEntityBinding.needs_review=False`` (same
contract as Phase 4 Links / takeoff trusted_links). Package BindingGovernanceReader
is not required and is not imported.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from ifc_processor.models import IFCEntity, IFCFile
from scheduling.models import Task, TaskEntityBinding

logger = logging.getLogger(__name__)

SCHEMA_SUMMARY = "timeline.summary.v1"
SCHEMA_DETAIL = "timeline.interval_detail.v1"


def _applied_gids_by_task(project_id: str | UUID, task_ids: list) -> dict[str, list[str]]:
    """Map task_id → entity GlobalIds for applied/confirmed bindings only."""
    if not task_ids:
        return {}
    out: dict[str, list[str]] = defaultdict(list)
    rows = TaskEntityBinding.objects.filter(
        task_id__in=task_ids,
        task__project_id=project_id,
        needs_review=False,
    ).values_list("task_id", "entity_global_id")
    for tid, gid in rows:
        out[str(tid)].append(gid)
    return dict(out)


class TimelinePayloadService:
    """Build slim timeline summary and applied/confirmed-only interval detail."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build_summary(self) -> dict[str, Any]:
        """Return scrubber-ready intervals with stats only — no GlobalId arrays."""
        ctx = self._prepare()
        if ctx is None:
            return {
                "schema": SCHEMA_SUMMARY,
                "has_tasks": False,
                "trusted_only": True,
                "intervals": [],
                "no_task_count": 0,
                "linked_entity_count": 0,
            }

        intervals: list[dict[str, Any]] = []
        current = ctx["min_date"]
        week_num = 1
        while current <= ctx["max_date"]:
            buckets = self._bucket_counts(ctx, current)
            intervals.append(
                {
                    "date": current.isoformat(),
                    "label": f"Week {week_num}",
                    "stats": buckets["stats"],
                    # Compat: empty entities so older clients do not crash on .entities
                    "entities": {
                        "not_started": [],
                        "in_progress": [],
                        "complete": [],
                        "delayed": [],
                    },
                }
            )
            current += timedelta(weeks=1)
            week_num += 1

        return {
            "schema": SCHEMA_SUMMARY,
            "has_tasks": True,
            "trusted_only": True,
            "detail_required": True,
            "project_start": ctx["min_date"].isoformat(),
            "project_end": ctx["max_date"].isoformat(),
            "linked_entity_count": ctx["linked_count"],
            "no_task_count": ctx["no_task_count"],
            # Intentionally omit full no_task GID list from summary.
            "no_task": [],
            "intervals": intervals,
        }

    def build_interval_detail(
        self, snapshot: date, *, include_no_task: bool = True
    ) -> dict[str, Any]:
        """Return applied/confirmed GlobalId buckets for one snapshot date."""
        ctx = self._prepare()
        if ctx is None:
            return {
                "schema": SCHEMA_DETAIL,
                "has_tasks": False,
                "trusted_only": True,
                "date": snapshot.isoformat(),
                "entities": {
                    "not_started": [],
                    "in_progress": [],
                    "complete": [],
                    "delayed": [],
                },
                "stats": {
                    "total": 0,
                    "complete": 0,
                    "in_progress": 0,
                    "delayed": 0,
                    "not_started": 0,
                },
                "no_task": [],
                "no_task_count": 0,
            }

        buckets = self._bucket_gids(ctx, snapshot)
        payload: dict[str, Any] = {
            "schema": SCHEMA_DETAIL,
            "has_tasks": True,
            "trusted_only": True,
            "date": snapshot.isoformat(),
            "entities": buckets["entities"],
            "stats": buckets["stats"],
            "no_task_count": ctx["no_task_count"],
            "linked_entity_count": ctx["linked_count"],
        }
        if include_no_task:
            payload["no_task"] = list(ctx["no_task_gids"])
        else:
            payload["no_task"] = []
        return payload

    def _prepare(self) -> dict[str, Any] | None:
        tasks = list(
            Task.objects.filter(project=self.project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
        )
        if not tasks:
            return None

        task_map = {t.pk: t for t in tasks}
        gids_by_task = _applied_gids_by_task(self.project_id, [t.pk for t in tasks])

        entity_tasks: dict[str, list] = defaultdict(list)
        for task in tasks:
            for gid in gids_by_task.get(str(task.pk), []):
                entity_tasks[gid].append(
                    (task.start_date, task.end_date, task.actual_start, task.actual_end)
                )

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
            "task_map": task_map,
            "entity_tasks": entity_tasks,
            "task_gids": task_gids,
            "no_task_gids": no_task_gids,
            "total": len(all_gids),
            "linked_count": len(task_gids),
            "no_task_count": len(no_task_gids),
            "min_date": min(t.start_date for t in tasks),
            "max_date": max(t.end_date for t in tasks),
        }

    def _bucket_gids(self, ctx: dict[str, Any], snapshot: date) -> dict[str, Any]:
        not_started: list[str] = []
        in_progress: list[str] = []
        complete: list[str] = []
        delayed: list[str] = []
        entity_tasks = ctx["entity_tasks"]

        for gid in ctx["task_gids"]:
            states = [
                self._task_state(s, e, a_s, a_e, snapshot) for s, e, a_s, a_e in entity_tasks[gid]
            ]
            if "delayed" in states:
                delayed.append(gid)
            elif all(st == "complete" for st in states):
                complete.append(gid)
            elif "in_progress" in states:
                in_progress.append(gid)
            else:
                not_started.append(gid)

        return {
            "entities": {
                "not_started": not_started,
                "in_progress": in_progress,
                "complete": complete,
                "delayed": delayed,
            },
            "stats": {
                "total": ctx["total"],
                "complete": len(complete),
                "in_progress": len(in_progress),
                "delayed": len(delayed),
                "not_started": len(not_started) + ctx["no_task_count"],
            },
        }

    def _bucket_counts(self, ctx: dict[str, Any], snapshot: date) -> dict[str, Any]:
        """Same classification as detail, but counts only — no GID lists."""
        n_not = n_inp = n_cmp = n_del = 0
        entity_tasks = ctx["entity_tasks"]
        for gid in ctx["task_gids"]:
            states = [
                self._task_state(s, e, a_s, a_e, snapshot) for s, e, a_s, a_e in entity_tasks[gid]
            ]
            if "delayed" in states:
                n_del += 1
            elif all(st == "complete" for st in states):
                n_cmp += 1
            elif "in_progress" in states:
                n_inp += 1
            else:
                n_not += 1
        return {
            "stats": {
                "total": ctx["total"],
                "complete": n_cmp,
                "in_progress": n_inp,
                "delayed": n_del,
                "not_started": n_not + ctx["no_task_count"],
            }
        }

    @staticmethod
    def _task_state(
        start_date: date,
        end_date: date,
        actual_start: date | None,
        actual_end: date | None,
        snapshot: date,
    ) -> str:
        """Classify a task relative to the playback snapshot date.

        Actual finish must be compared to ``snapshot``. Returning complete
        solely because ``actual_end`` is populated made programme playback
        paint nearly the whole model green on every date.
        """
        # Finished as-of snapshot (actual).
        if actual_end is not None and snapshot >= actual_end:
            if actual_end > end_date:
                return "delayed"
            return "complete"

        # Started but not finished as-of snapshot (actual).
        if actual_start is not None and snapshot >= actual_start:
            planned_span = end_date - start_date
            overtime = snapshot > actual_start + planned_span * 1.2
            past_planned_end = snapshot > end_date
            if actual_end is None and (overtime or past_planned_end):
                return "delayed"
            if past_planned_end:
                return "delayed"
            return "in_progress"

        # Planned dates only (no actual progress yet).
        if end_date <= snapshot:
            return "complete"
        if start_date <= snapshot < end_date:
            return "in_progress"
        return "not_started"


def parse_snapshot_date(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD for interval detail; return None if invalid/missing."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None
