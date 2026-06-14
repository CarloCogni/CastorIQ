# scheduling/services/baseline/comparison.py
"""Compare current operational schedule to a baseline version."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from scheduling.models import BaselineVersion, Task

logger = logging.getLogger(__name__)


class BaselineComparisonService:
    """Read-only comparison between current tasks and baseline task states."""

    def __init__(self, project, baseline: BaselineVersion | None = None) -> None:
        self.project = project
        self.project_id = project.pk
        self.baseline = baseline

    def resolve_baseline(self) -> BaselineVersion | None:
        """Use explicit baseline or project's selected baseline."""
        if self.baseline is not None:
            if self.baseline.project_id != self.project_id:
                raise ValueError("Baseline must belong to the same project.")
            return self.baseline
        from scheduling.services.baseline.lifecycle import BaselineVersionService

        return BaselineVersionService.get_selected_baseline(self.project)

    def summary(self) -> dict[str, Any]:
        """Project-level comparison summary."""
        baseline = self.resolve_baseline()
        if baseline is None:
            return {
                "baseline_id": None,
                "matched_count": 0,
                "new_current_count": 0,
                "baseline_only_count": 0,
                "unresolved_count": 0,
                "baseline_coverage_pct": None,
                "start_variance_available_count": 0,
                "finish_variance_available_count": 0,
                "cost_variance_available_count": 0,
            }

        current_by_activity = self._current_tasks_by_activity()
        baseline_states = list(baseline.task_states.select_related("schedule_activity").all())
        baseline_by_activity = {s.schedule_activity_id: s for s in baseline_states}

        matched = 0
        new_current = 0
        baseline_only = 0
        unresolved = 0
        start_var = 0
        finish_var = 0
        cost_var = 0

        seen_activities = set()

        for activity_id, task in current_by_activity.items():
            seen_activities.add(activity_id)
            state = baseline_by_activity.get(activity_id)
            if state is None:
                new_current += 1
                continue
            if state.schedule_activity.identity_status != "active":
                unresolved += 1
            else:
                matched += 1
            if task.start_date and state.planned_start:
                start_var += 1
            if task.end_date and state.planned_finish:
                finish_var += 1
            if task.cost is not None and state.baseline_cost is not None:
                cost_var += 1

        for activity_id in baseline_by_activity:
            if activity_id not in seen_activities:
                baseline_only += 1

        total_baseline = len(baseline_states)
        coverage = round(100.0 * matched / total_baseline, 2) if total_baseline else None

        return {
            "baseline_id": str(baseline.pk),
            "matched_count": matched,
            "new_current_count": new_current,
            "baseline_only_count": baseline_only,
            "unresolved_count": unresolved,
            "baseline_coverage_pct": coverage,
            "start_variance_available_count": start_var,
            "finish_variance_available_count": finish_var,
            "cost_variance_available_count": cost_var,
        }

    def _current_tasks_by_activity(self) -> dict[Any, Task]:
        """Map schedule_activity_id → current operational task (latest per activity)."""
        tasks = (
            Task.objects.filter(
                project_id=self.project_id,
                schedule_activity_id__isnull=False,
            )
            .select_related("schedule_activity")
            .order_by("schedule_activity_id", "-updated_at")
        )
        by_activity: dict[Any, Task] = {}
        for task in tasks:
            aid = task.schedule_activity_id
            if aid not in by_activity:
                by_activity[aid] = task
        return by_activity

    @staticmethod
    def finish_variance_days(
        current_finish: date | None, baseline_finish: date | None
    ) -> int | None:
        """Days current finish minus baseline finish — None if either missing."""
        if current_finish is None or baseline_finish is None:
            return None
        return (current_finish - baseline_finish).days

    @staticmethod
    def cost_variance(current_cost, baseline_cost) -> float | None:
        """Cost variance — None if either missing (no zero-for-missing)."""
        if current_cost is None or baseline_cost is None:
            return None
        return float(current_cost - baseline_cost)
