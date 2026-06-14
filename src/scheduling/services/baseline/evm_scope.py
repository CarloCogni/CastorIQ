# scheduling/services/baseline/evm_scope.py
"""Baseline-backed EVM scope — mode selection, activity matching, coverage."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from scheduling.models import BaselineTaskState, BaselineVersion, Task
from scheduling.services.baseline.lifecycle import BaselineVersionService

logger = logging.getLogger(__name__)

COST_EVM_MIN_MATCHED_WITH_COST = 1


class EVMMethodologyMode(StrEnum):
    """Explicit EVM calculation authority modes (DF-A2.1)."""

    APPROVED_BASELINE_COST_EVM = "approved_baseline_cost_evm"
    REFERENCE_BASELINE_COST_EVM = "reference_baseline_cost_evm"
    WORKING_BASELINE_COST_EVM = "working_baseline_cost_evm"
    DERIVED_CURRENT_SCHEDULE_EVM = "derived_current_schedule_evm"
    SCHEDULE_PERFORMANCE_MODE = "schedule_performance_mode"


class ActivityMatchKind(StrEnum):
    """ScheduleActivity alignment between current Task and BaselineTaskState."""

    MATCHED = "matched"
    CURRENT_ONLY = "current_only"
    BASELINE_ONLY = "baseline_only"
    UNRESOLVED = "unresolved"


@dataclass
class MatchedEVMEntry:
    """One schedulable current task aligned to optional baseline state."""

    task: Task
    baseline_state: BaselineTaskState | None
    match_kind: str
    baseline_cost: float | None
    planned_start: date | None
    planned_finish: date | None


@dataclass
class BaselineEVMScope:
    """Resolved baseline EVM scope for one compute_evm() pass."""

    methodology_mode: str
    baseline: BaselineVersion | None
    baseline_authority: str
    caveats: tuple[str, ...] = ()
    matched_entries: list[MatchedEVMEntry] = field(default_factory=list)
    baseline_only_states: list[BaselineTaskState] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def is_baseline_backed(self) -> bool:
        return self.methodology_mode in (
            EVMMethodologyMode.APPROVED_BASELINE_COST_EVM,
            EVMMethodologyMode.REFERENCE_BASELINE_COST_EVM,
            EVMMethodologyMode.WORKING_BASELINE_COST_EVM,
        )

    @property
    def use_baseline_cost_evm(self) -> bool:
        if not self.is_baseline_backed:
            return False
        return self.coverage.get("matched_cost_count", 0) >= COST_EVM_MIN_MATCHED_WITH_COST

    def to_metadata(self) -> dict[str, Any]:
        """API-safe baseline EVM metadata block."""
        b = self.baseline
        return {
            "methodology_mode": self.methodology_mode,
            "baseline_id": str(b.pk) if b else None,
            "baseline_name": b.name if b else None,
            "baseline_type": b.baseline_type if b else None,
            "baseline_status": b.status if b else None,
            "baseline_authority": self.baseline_authority,
            "coverage": self.coverage,
            "caveats": list(self.caveats),
            "historical": False,
            "series_authority": "baseline_task_state"
            if self.is_baseline_backed
            else "current_task_snapshot",
        }


def _authority_for_mode(mode: str) -> str:
    if mode == EVMMethodologyMode.APPROVED_BASELINE_COST_EVM:
        return "authoritative"
    if mode in (
        EVMMethodologyMode.REFERENCE_BASELINE_COST_EVM,
        EVMMethodologyMode.WORKING_BASELINE_COST_EVM,
    ):
        return "caveated"
    if mode == EVMMethodologyMode.SCHEDULE_PERFORMANCE_MODE:
        return "proxy"
    return "derived"


def _mode_for_baseline(baseline: BaselineVersion) -> tuple[str, tuple[str, ...]]:
    """Map selected baseline type/status to methodology mode and caveats."""
    caveats: list[str] = []
    btype = baseline.baseline_type
    if baseline.status in (
        BaselineVersion.Status.REJECTED,
        BaselineVersion.Status.ARCHIVED,
    ):
        return EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM, (
            "Selected baseline is rejected or archived — using derived current schedule mode.",
        )

    if btype == BaselineVersion.BaselineType.APPROVED:
        if baseline.status != BaselineVersion.Status.PUBLISHED or not baseline.approved_at:
            caveats.append(
                "Approved baseline type without publication/approval metadata — caveated mode."
            )
            return EVMMethodologyMode.WORKING_BASELINE_COST_EVM, tuple(caveats)
        return EVMMethodologyMode.APPROVED_BASELINE_COST_EVM, ()

    if btype == BaselineVersion.BaselineType.IMPORTED_REFERENCE:
        caveats.append("Imported reference baseline — not contractual or approved EVM baseline.")
        return EVMMethodologyMode.REFERENCE_BASELINE_COST_EVM, tuple(caveats)

    if btype == BaselineVersion.BaselineType.WORKING:
        caveats.append("Working baseline — internal target only, not executive contractual truth.")
        return EVMMethodologyMode.WORKING_BASELINE_COST_EVM, tuple(caveats)

    if btype == BaselineVersion.BaselineType.COMPARISON_ONLY:
        caveats.append("Comparison-only baseline — not authoritative operational EVM.")
        return EVMMethodologyMode.REFERENCE_BASELINE_COST_EVM, tuple(caveats)

    return EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM, ()


def _cost_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


class BaselineEVMScopeService:
    """Resolve baseline-backed EVM scope for compute_evm()."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    def resolve(self, tasks: list[Task]) -> BaselineEVMScope:
        """Build scope from selected baseline and current schedulable tasks."""
        from environments.models import Project

        project = Project.objects.filter(pk=self.project_id).first()
        if project is None:
            return self._derived_scope(tasks, caveats=("Project not found.",))

        baseline = BaselineVersionService.get_selected_baseline(project)
        if baseline is None:
            return self._derived_scope(tasks)

        mode, caveats = _mode_for_baseline(baseline)
        if mode == EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM:
            return self._derived_scope(tasks, baseline=baseline, caveats=caveats)

        return self._baseline_scope(baseline, tasks, mode, caveats)

    def _derived_scope(
        self,
        tasks: list[Task],
        *,
        baseline: BaselineVersion | None = None,
        caveats: tuple[str, ...] = (),
    ) -> BaselineEVMScope:
        entries = [
            MatchedEVMEntry(
                task=t,
                baseline_state=None,
                match_kind=ActivityMatchKind.CURRENT_ONLY,
                baseline_cost=None,
                planned_start=t.start_date,
                planned_finish=t.end_date,
            )
            for t in tasks
        ]
        coverage = _empty_coverage(len(tasks), len(tasks))
        return BaselineEVMScope(
            methodology_mode=EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM,
            baseline=baseline,
            baseline_authority="derived",
            caveats=caveats
            or ("No selected baseline — PV/BAC from current operational Task fields.",),
            matched_entries=entries,
            coverage=coverage,
        )

    def _baseline_scope(
        self,
        baseline: BaselineVersion,
        tasks: list[Task],
        mode: str,
        caveats: tuple[str, ...],
    ) -> BaselineEVMScope:
        states = list(baseline.task_states.select_related("schedule_activity").all())
        state_by_activity = {s.schedule_activity_id: s for s in states}

        current_by_activity: dict[Any, Task] = {}
        for t in tasks:
            if t.schedule_activity_id and t.schedule_activity_id not in current_by_activity:
                current_by_activity[t.schedule_activity_id] = t

        matched_entries: list[MatchedEVMEntry] = []
        baseline_only: list[BaselineTaskState] = []
        matched = 0
        current_only = 0
        unresolved = 0
        matched_with_dates = 0
        matched_with_cost = 0

        for activity_id, task in current_by_activity.items():
            state = state_by_activity.get(activity_id)
            if state is None:
                current_only += 1
                matched_entries.append(
                    MatchedEVMEntry(
                        task=task,
                        baseline_state=None,
                        match_kind=ActivityMatchKind.CURRENT_ONLY,
                        baseline_cost=None,
                        planned_start=task.start_date,
                        planned_finish=task.end_date,
                    )
                )
                continue

            kind = ActivityMatchKind.MATCHED
            if state.schedule_activity.identity_status != "active":
                kind = ActivityMatchKind.UNRESOLVED
                unresolved += 1
            else:
                matched += 1

            b_cost = _cost_float(state.baseline_cost)
            p_start = state.planned_start
            p_finish = state.planned_finish
            if p_start and p_finish:
                matched_with_dates += 1
            if b_cost is not None and kind == ActivityMatchKind.MATCHED:
                matched_with_cost += 1

            matched_entries.append(
                MatchedEVMEntry(
                    task=task,
                    baseline_state=state,
                    match_kind=kind,
                    baseline_cost=b_cost if kind == ActivityMatchKind.MATCHED else None,
                    planned_start=p_start,
                    planned_finish=p_finish,
                )
            )

        for activity_id, state in state_by_activity.items():
            if activity_id not in current_by_activity:
                baseline_only.append(state)

        tasks_without_activity = [t for t in tasks if not t.schedule_activity_id]
        for t in tasks_without_activity:
            current_only += 1
            matched_entries.append(
                MatchedEVMEntry(
                    task=t,
                    baseline_state=None,
                    match_kind=ActivityMatchKind.CURRENT_ONLY,
                    baseline_cost=None,
                    planned_start=t.start_date,
                    planned_finish=t.end_date,
                )
            )

        total_states = len(states)
        states_with_cost = sum(1 for s in states if s.baseline_cost is not None)
        states_with_dates = sum(1 for s in states if s.planned_start and s.planned_finish)
        represented_bac = sum(
            float(e.baseline_cost)
            for e in matched_entries
            if e.match_kind == ActivityMatchKind.MATCHED and e.baseline_cost is not None
        )
        total_baseline_bac = sum(
            float(s.baseline_cost) for s in states if s.baseline_cost is not None
        )
        excluded_bac = max(total_baseline_bac - represented_bac, 0.0)

        sched_n = len(tasks) or 1
        coverage = {
            "baseline_task_state_count": total_states,
            "current_task_count": len(tasks),
            "matched_activity_count": matched,
            "current_only_count": current_only,
            "baseline_only_count": len(baseline_only),
            "unresolved_count": unresolved,
            "baseline_date_coverage_pct": round(100.0 * states_with_dates / total_states, 2)
            if total_states
            else None,
            "baseline_cost_coverage_pct": round(100.0 * states_with_cost / total_states, 2)
            if total_states
            else None,
            "baseline_match_coverage_pct": round(100.0 * matched / total_states, 2)
            if total_states
            else None,
            "matched_date_count": matched_with_dates,
            "matched_cost_count": matched_with_cost,
            "matched_cost_coverage_pct": round(100.0 * matched_with_cost / sched_n, 2),
            "represented_bac": round(represented_bac, 2),
            "total_baseline_bac": round(total_baseline_bac, 2),
            "excluded_bac": round(excluded_bac, 2),
        }

        extra: list[str] = list(caveats)
        if current_only:
            extra.append(
                f"{current_only} current activities lack baseline state — excluded from baseline BAC."
            )
        if matched_with_cost < COST_EVM_MIN_MATCHED_WITH_COST:
            extra.append(
                "Insufficient matched baseline cost — cost EVM unavailable; schedule performance only."
            )

        return BaselineEVMScope(
            methodology_mode=mode,
            baseline=baseline,
            baseline_authority=_authority_for_mode(mode),
            caveats=tuple(extra),
            matched_entries=matched_entries,
            baseline_only_states=baseline_only,
            coverage=coverage,
        )


def _empty_coverage(current_n: int, sched_n: int) -> dict[str, Any]:
    return {
        "baseline_task_state_count": 0,
        "current_task_count": current_n,
        "matched_activity_count": 0,
        "current_only_count": sched_n,
        "baseline_only_count": 0,
        "unresolved_count": 0,
        "baseline_date_coverage_pct": None,
        "baseline_cost_coverage_pct": None,
        "baseline_match_coverage_pct": None,
        "matched_date_count": 0,
        "matched_cost_count": 0,
        "matched_cost_coverage_pct": 0.0,
        "represented_bac": 0.0,
        "total_baseline_bac": 0.0,
        "excluded_bac": 0.0,
    }
