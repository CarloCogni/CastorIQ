# scheduling/services/executive_controls/progress_aggregation.py
"""Lightweight schedule progress aggregation — no compute_evm()."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from django.db.models import QuerySet

from scheduling.services.calendar_utils import load_project_calendars, task_cal
from scheduling.services.evm import _earned_pct_at, _planned_pct_at
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

WEIGHTING_COST = "cost_weighted"
WEIGHTING_QUANTITY = "quantity_weighted"
WEIGHTING_DURATION = "duration_weighted"
WEIGHTING_COUNT = "count_based"

COST_COVERAGE_THRESHOLD = 0.5
QUANTITY_COVERAGE_THRESHOLD = 0.3


class ScheduleProgressAggregationService:
    """Read-only progress summaries for overview cards and matrix rows."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)
        self._cal_map = load_project_calendars(self.project_id)

    def select_weighting_mode(
        self,
        tasks: list,
        *,
        requested: str | None = None,
    ) -> tuple[str, str, str]:
        """Pick defensible weighting mode; return (mode_id, label, authority)."""
        physical = [
            t
            for t in tasks
            if not getattr(t, "is_non_physical", False) and t.start_date and t.end_date
        ]
        if not physical:
            return "unavailable", "Unavailable", MetricAuthority.UNAVAILABLE.value

        n = len(physical)
        n_cost = sum(1 for t in physical if t.cost and float(t.cost) > 0)
        n_qty = sum(
            1
            for t in physical
            if getattr(t, "physical_percent_complete", None) is not None
            or getattr(t, "duration_percent_complete", None) is not None
        )

        if requested == WEIGHTING_COUNT:
            return (
                WEIGHTING_COUNT,
                "Unweighted activity completion (count-based)",
                MetricAuthority.PROXY.value,
            )
        if requested == WEIGHTING_DURATION:
            return (
                WEIGHTING_DURATION,
                "Duration-weighted schedule progress (proxy)",
                MetricAuthority.PROXY.value,
            )
        if requested == WEIGHTING_QUANTITY and n_qty / n >= QUANTITY_COVERAGE_THRESHOLD:
            return (
                WEIGHTING_QUANTITY,
                "Quantity-weighted physical progress",
                MetricAuthority.DERIVED.value,
            )
        if requested == WEIGHTING_COST and n_cost / n >= COST_COVERAGE_THRESHOLD:
            return (
                WEIGHTING_COST,
                "Cost-weighted schedule progress",
                MetricAuthority.DERIVED.value,
            )

        if n_cost / n >= COST_COVERAGE_THRESHOLD:
            return WEIGHTING_COST, "Cost-weighted schedule progress", MetricAuthority.DERIVED.value
        if n_qty / n >= QUANTITY_COVERAGE_THRESHOLD:
            return (
                WEIGHTING_QUANTITY,
                "Quantity-weighted physical progress",
                MetricAuthority.DERIVED.value,
            )
        return (
            WEIGHTING_DURATION,
            "Duration-weighted schedule progress (proxy)",
            MetricAuthority.PROXY.value,
        )

    def _task_weight(self, task, mode: str) -> float:
        if mode == WEIGHTING_COST:
            return max(float(task.cost or 0), 0.0)
        if mode == WEIGHTING_QUANTITY:
            phys = getattr(task, "physical_percent_complete", None)
            if phys is not None:
                return 1.0
            dur = getattr(task, "duration_percent_complete", None)
            if dur is not None:
                return 1.0
            return 0.0
        if mode == WEIGHTING_COUNT:
            return 1.0
        if task.start_date and task.end_date:
            cal = task_cal(task, self._cal_map) if self._cal_map else None
            if cal is not None:
                from scheduling.services.calendar_utils import working_day_diff

                return float(max(working_day_diff(task.start_date, task.end_date, cal), 1))
            return float(max((task.end_date - task.start_date).days, 1))
        return 0.0

    def aggregate(
        self,
        tasks: list,
        *,
        as_of_date: date | None = None,
        weighting_mode: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate planned/actual progress for a task list — single O(n) pass."""
        data_date, _ = get_project_data_date(self.project_id)
        as_of = as_of_date or data_date
        calculated_at = datetime.now(UTC).isoformat()

        included: list = []
        excluded = 0
        for t in tasks:
            if t.start_date and t.end_date:
                included.append(t)
            else:
                excluded += 1

        if not included:
            return {
                "available": False,
                "weighting_mode": "unavailable",
                "weighting_label": "Unavailable",
                "weighting_authority": MetricAuthority.UNAVAILABLE.value,
                "planned_progress_pct": None,
                "actual_progress_pct": None,
                "variance_pct": None,
                "planned_numerator": None,
                "actual_numerator": None,
                "weight_denominator": None,
                "included_task_count": 0,
                "excluded_task_count": excluded,
                "total_task_count": len(tasks),
                "coverage": {},
                "methodology_version": E8_METHODOLOGY_VERSION,
                "caveat": "No schedulable tasks with start and end dates.",
                "data_date": as_of.isoformat(),
                "calculated_at": calculated_at,
            }

        mode, label, authority = self.select_weighting_mode(included, requested=weighting_mode)

        if mode == WEIGHTING_QUANTITY:
            qty_tasks = [
                t
                for t in included
                if getattr(t, "physical_percent_complete", None) is not None
                or getattr(t, "duration_percent_complete", None) is not None
            ]
            if not qty_tasks:
                mode, label, authority = (
                    WEIGHTING_DURATION,
                    "Duration-weighted schedule progress (proxy)",
                    MetricAuthority.PROXY.value,
                )
            else:
                included = qty_tasks

        if mode == WEIGHTING_COST:
            cost_tasks = [t for t in included if t.cost and float(t.cost) > 0]
            if not cost_tasks:
                mode, label, authority = (
                    WEIGHTING_DURATION,
                    "Duration-weighted schedule progress (proxy)",
                    MetricAuthority.PROXY.value,
                )
            else:
                included = cost_tasks

        planned_sum = 0.0
        actual_sum = 0.0
        weight_sum = 0.0

        for task in included:
            w = self._task_weight(task, mode)
            if w <= 0:
                continue
            cal = task_cal(task, self._cal_map) if self._cal_map else None
            planned_sum += w * _planned_pct_at(task, as_of, cal)
            actual_sum += w * _earned_pct_at(task, as_of, cal)
            weight_sum += w

        if weight_sum <= 0:
            return {
                "available": False,
                "weighting_mode": mode,
                "weighting_label": label,
                "weighting_authority": authority,
                "planned_progress_pct": None,
                "actual_progress_pct": None,
                "variance_pct": None,
                "planned_numerator": None,
                "actual_numerator": None,
                "weight_denominator": 0,
                "included_task_count": len(included),
                "excluded_task_count": excluded,
                "total_task_count": len(tasks),
                "coverage": {},
                "methodology_version": E8_METHODOLOGY_VERSION,
                "caveat": "Insufficient weight denominator for selected mode.",
                "data_date": as_of.isoformat(),
                "calculated_at": calculated_at,
            }

        planned_pct = round(100.0 * planned_sum / weight_sum, 2)
        actual_pct = round(100.0 * actual_sum / weight_sum, 2)
        variance = round(actual_pct - planned_pct, 2)

        n_all = len(tasks)
        n_cost = sum(1 for t in tasks if t.cost and float(t.cost) > 0)

        caveat = ""
        if mode == WEIGHTING_DURATION:
            caveat = "Duration proxy — not Earned Value or cost EVM."
        elif mode == WEIGHTING_COUNT:
            caveat = "Count-based completion — not weighted schedule or cost progress."

        return {
            "available": True,
            "weighting_mode": mode,
            "weighting_label": label,
            "weighting_authority": authority,
            "planned_progress_pct": planned_pct,
            "actual_progress_pct": actual_pct,
            "variance_pct": variance,
            "planned_numerator": round(planned_sum, 4),
            "actual_numerator": round(actual_sum, 4),
            "weight_denominator": round(weight_sum, 4),
            "included_task_count": len(included),
            "excluded_task_count": excluded,
            "total_task_count": len(tasks),
            "coverage": {
                "cost_coverage_pct": round(100.0 * n_cost / n_all, 2) if n_all else None,
                "schedulable_pct": round(100.0 * len(included) / n_all, 2) if n_all else None,
            },
            "methodology_version": E8_METHODOLOGY_VERSION,
            "caveat": caveat,
            "data_date": as_of.isoformat(),
            "calculated_at": calculated_at,
        }

    def aggregate_queryset(
        self,
        qs: QuerySet,
        *,
        weighting_mode: str | None = None,
    ) -> dict[str, Any]:
        """Fetch minimal task fields and aggregate."""
        tasks = list(
            qs.only(
                "pk",
                "start_date",
                "end_date",
                "actual_start",
                "actual_end",
                "status",
                "cost",
                "physical_percent_complete",
                "duration_percent_complete",
                "is_non_physical",
                "calendar_object_id",
            )
        )
        return self.aggregate(tasks, weighting_mode=weighting_mode)
