# scheduling/services/executive_controls/delay_classification.py
"""Canonical delay classification — read-only, no generic slip field."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from scheduling.services.calendar_utils import load_project_calendars, task_cal, working_day_diff
from scheduling.services.executive_controls.contracts import DelayClassificationResult
from scheduling.services.executive_controls.enums import DayType, DelayType, SourceAuthority
from scheduling.services.utils import get_project_data_date

if TYPE_CHECKING:
    from scheduling.models import Task

logger = logging.getLogger(__name__)

DEFAULT_NEAR_CRITICAL_THRESHOLD = 5


class DelayClassificationService:
    """Classify schedule delay semantics per task without mutating Task rows."""

    def __init__(
        self,
        project_id: str,
        *,
        near_critical_threshold: int = DEFAULT_NEAR_CRITICAL_THRESHOLD,
        day_type: DayType | str = DayType.WORKING,
        data_date: date | None = None,
    ) -> None:
        self.project_id = str(project_id)
        self.near_critical_threshold = near_critical_threshold
        self.day_type = DayType(day_type) if isinstance(day_type, str) else day_type
        self._data_date = data_date
        self._cal_map = load_project_calendars(self.project_id)
        self._default_cal = None

    @property
    def data_date(self) -> date:
        if self._data_date is not None:
            return self._data_date
        as_of, _ = get_project_data_date(self.project_id)
        return as_of

    def _task_calendar(self, task: Task) -> tuple[dict, bool]:
        """Return (calendar dict, used_fallback)."""
        if self._cal_map:
            return task_cal(task, self._cal_map), False
        from scheduling.services.calendar_utils import task_cal as _task_cal_fn

        return _task_cal_fn(task, {}), True

    def _day_diff(self, d1: date, d2: date, cal: dict, *, calendar_fallback: bool) -> int:
        if self.day_type == DayType.CALENDAR:
            return (d2 - d1).days
        return working_day_diff(d1, d2, cal)

    def _baseline_finish(self, task: Task) -> date | None:
        """Reference baseline finish from imported schedule (Task.end_date)."""
        return task.end_date

    def _forecast_finish(self, task: Task) -> date | None:
        """Current/forecast finish: early_finish preferred, else end_date."""
        return task.early_finish or task.end_date

    def _required_finish(self, task: Task) -> date | None:
        """Finish date used for current-lateness against data date."""
        if task.status == "complete":
            return task.actual_end
        return self._forecast_finish(task)

    def classify_task(
        self,
        task: Task,
        *,
        trusted_entity_count: int = 0,
        scope_classification: str = "unknown",
        scope_authoritative: bool = False,
    ) -> DelayClassificationResult:
        """Return canonical delay semantics for one task."""
        missing: list[str] = []
        caveats: list[str] = []
        evidence: list[str] = []
        secondary: list[str] = []

        baseline = self._baseline_finish(task)
        actual = task.actual_end
        forecast = self._forecast_finish(task)
        cal, cal_fallback = self._task_calendar(task)
        if cal_fallback and self.day_type == DayType.WORKING:
            caveats.append("Working calendar unavailable — using default 7-day calendar.")

        is_completed = task.status == "complete" or actual is not None

        if baseline is None:
            missing.append("baseline_finish")
            return self._result(
                task,
                primary=DelayType.MISSING_BASELINE.value,
                secondary=secondary,
                is_completed=is_completed,
                is_current_risk=False,
                baseline=None,
                actual=actual,
                forecast=forecast,
                variance=None,
                cal_fallback=cal_fallback,
                cal=cal,
                evidence=evidence,
                missing=missing,
                caveats=caveats + ["No reference baseline finish (end_date) on task."],
                explanation="Cannot compute delay without reference baseline finish.",
                trusted_entity_count=trusted_entity_count,
                scope_classification=scope_classification,
                scope_authoritative=scope_authoritative,
            )

        evidence.append("end_date")

        if is_completed:
            if actual is None:
                missing.append("actual_finish")
                primary = DelayType.MISSING_FORECAST.value
                explanation = "Task marked complete but actual_end is missing."
            else:
                evidence.append("actual_end")
                variance = self._day_diff(baseline, actual, cal, calendar_fallback=cal_fallback)
                if variance > 0:
                    primary = DelayType.COMPLETED_LATE.value
                    secondary.append(DelayType.ACTUAL_FINISH_VARIANCE.value)
                    explanation = (
                        f"Completed {variance} {self.day_type.value} day(s) after baseline finish."
                    )
                else:
                    primary = DelayType.NOT_LATE.value
                    if variance < 0:
                        secondary.append(DelayType.ACTUAL_FINISH_VARIANCE.value)
                    explanation = "Completed on or before baseline finish."
        else:
            if forecast is None:
                missing.append("forecast_finish")
                return self._result(
                    task,
                    primary=DelayType.MISSING_FORECAST.value,
                    secondary=secondary,
                    is_completed=False,
                    is_current_risk=False,
                    baseline=baseline,
                    actual=actual,
                    forecast=None,
                    variance=None,
                    cal_fallback=cal_fallback,
                    cal=cal,
                    evidence=evidence,
                    missing=missing,
                    caveats=caveats + ["No forecast/current finish available."],
                    explanation="Cannot assess forecast delay without finish date.",
                    trusted_entity_count=trusted_entity_count,
                    scope_classification=scope_classification,
                    scope_authoritative=scope_authoritative,
                )

            if task.early_finish:
                evidence.append("early_finish")
            else:
                evidence.append("end_date")

            variance = self._day_diff(baseline, forecast, cal, calendar_fallback=cal_fallback)
            required = self._required_finish(task)
            is_current_risk = False
            if required and required < self.data_date:
                is_current_risk = True
                secondary.append(DelayType.CURRENTLY_LATE.value)
                evidence.append("data_date")

            if variance > 0:
                primary = DelayType.FORECAST_LATE.value
                secondary.append(DelayType.BASELINE_FINISH_VARIANCE.value)
                explanation = (
                    f"Forecast finish is {variance} {self.day_type.value} day(s) after baseline."
                )
            elif is_current_risk:
                primary = DelayType.CURRENTLY_LATE.value
                explanation = f"Required finish before data date ({self.data_date.isoformat()})."
            else:
                primary = DelayType.NOT_LATE.value
                explanation = "Not currently late and forecast on or before baseline."

        if task.is_critical:
            secondary.append(DelayType.CRITICAL.value)
        if task.total_float is not None:
            evidence.append("total_float")
            if task.total_float < 0:
                secondary.append(DelayType.NEGATIVE_FLOAT.value)
            elif task.total_float == 0:
                secondary.append(DelayType.ZERO_FLOAT.value)
            elif 0 < task.total_float <= self.near_critical_threshold:
                secondary.append(DelayType.NEAR_CRITICAL.value)

        variance_days: int | None = None
        if is_completed and actual and baseline:
            variance_days = self._day_diff(baseline, actual, cal, calendar_fallback=cal_fallback)
        elif not is_completed and forecast and baseline:
            variance_days = self._day_diff(baseline, forecast, cal, calendar_fallback=cal_fallback)

        return self._result(
            task,
            primary=primary,
            secondary=secondary,
            is_completed=is_completed,
            is_current_risk=is_current_risk if not is_completed else False,
            baseline=baseline,
            actual=actual,
            forecast=forecast,
            variance=variance_days,
            cal_fallback=cal_fallback,
            cal=cal,
            evidence=evidence,
            missing=missing,
            caveats=caveats,
            explanation=explanation,
            trusted_entity_count=trusted_entity_count,
            scope_classification=scope_classification,
            scope_authoritative=scope_authoritative,
        )

    def _result(
        self,
        task: Task,
        *,
        primary: str,
        secondary: list[str],
        is_completed: bool,
        is_current_risk: bool,
        baseline: date | None,
        actual: date | None,
        forecast: date | None,
        variance: int | None,
        cal_fallback: bool,
        cal: dict,
        evidence: list[str],
        missing: list[str],
        caveats: list[str],
        explanation: str,
        trusted_entity_count: int,
        scope_classification: str,
        scope_authoritative: bool,
    ) -> DelayClassificationResult:
        return DelayClassificationResult(
            task_id=str(task.pk),
            primary_delay_type=primary,
            secondary_indicators=sorted(set(secondary)),
            is_completed=is_completed,
            is_current_risk=is_current_risk,
            baseline_finish=baseline.isoformat() if baseline else None,
            actual_finish=actual.isoformat() if actual else None,
            current_forecast_finish=forecast.isoformat() if forecast else None,
            variance_days=variance,
            day_type=self.day_type.value,
            calendar_fallback=cal_fallback,
            total_float=task.total_float,
            is_critical=bool(task.is_critical),
            evidence_fields=evidence,
            source_authority=SourceAuthority.BASELINE_SCHEDULE.value,
            missing_fields=missing,
            caveats=caveats,
            explanation=explanation,
            trusted_entity_count=trusted_entity_count,
            scope_classification=scope_classification,
            scope_authoritative=scope_authoritative,
        )

    def project_finish_variance(self, tasks: list[Task]) -> dict[str, int | None | str]:
        """Project-level finish slip: max forecast − max baseline on schedulable tasks."""
        dated = [t for t in tasks if t.start_date and t.end_date]
        if not dated:
            return {
                "available": False,
                "variance_days": None,
                "baseline_finish": None,
                "forecast_finish": None,
                "caveat": "No schedulable tasks with dates.",
            }

        baseline_finish = max(t.end_date for t in dated if t.end_date)
        forecast_candidates = [
            (t.early_finish or t.end_date) for t in dated if (t.early_finish or t.end_date)
        ]
        if not forecast_candidates:
            return {
                "available": False,
                "variance_days": None,
                "baseline_finish": baseline_finish.isoformat(),
                "forecast_finish": None,
                "caveat": "No forecast finish dates available.",
            }

        forecast_finish = max(forecast_candidates)
        cal, _ = self._task_calendar(dated[0])
        if self.day_type == DayType.CALENDAR:
            slip = (forecast_finish - baseline_finish).days
        else:
            slip = working_day_diff(baseline_finish, forecast_finish, cal)

        return {
            "available": True,
            "variance_days": slip,
            "baseline_finish": baseline_finish.isoformat(),
            "forecast_finish": forecast_finish.isoformat(),
            "caveat": (
                "Current Reference/Baseline Fields from Imported Schedule — "
                "not contractual baseline."
            ),
        }

    def summarize_counts(self, results: list[DelayClassificationResult]) -> dict[str, int]:
        """Aggregate delay type counts for summary endpoint."""
        counts: dict[str, int] = {dt.value: 0 for dt in DelayType}
        for r in results:
            if r.primary_delay_type in counts:
                counts[r.primary_delay_type] += 1
            for indicator in r.secondary_indicators:
                if indicator in counts:
                    counts[indicator] += 1
        return counts
