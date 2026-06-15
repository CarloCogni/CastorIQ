# scheduling/services/executive_controls/governed_mapping_analytics_session.py
"""Read-only governed mapping analytics session for E8 (DF-D3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from scheduling.models import AnalyticalDimension, Task
from scheduling.services.baseline.evm_scope import ActivityMatchKind, BaselineEVMScopeService
from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.dimension_mode import (
    DimensionModeService,
    E8DimensionModeContext,
)
from scheduling.services.executive_controls.performance_cube import DELAYED_PRIMARY
from scheduling.services.executive_controls.wbs_analytics_session import TaskEvmPoint
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.governed_mapping.contracts import EffectiveMappingResult
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

UNMAPPED_BUCKET = "__unmapped__"
CONFLICT_BUCKET = "__conflict__"
PROPOSED_BUCKET = "__proposed_only__"

BUCKET_LABELS = {
    UNMAPPED_BUCKET: "Unmapped",
    CONFLICT_BUCKET: "Conflict",
    PROPOSED_BUCKET: "Proposed only",
}


@dataclass
class GovernedMappingAnalyticsSession:
    """Cached read-only inputs for governed dimension aggregation."""

    project_id: str
    data_date: date
    mode_context: E8DimensionModeContext
    tasks: list[Task] = field(default_factory=list)
    tasks_by_id: dict[str, Task] = field(default_factory=dict)
    task_ids: list[UUID] = field(default_factory=list)
    dimensions_by_key: dict[str, AnalyticalDimension] = field(default_factory=dict)
    resolutions_by_dimension: dict[str, dict[str, EffectiveMappingResult]] = field(
        default_factory=dict
    )
    trusted_task_ids: set[str] = field(default_factory=set)
    entities_by_task: dict[str, list[str]] = field(default_factory=dict)
    review_entities_by_task: dict[str, list[str]] = field(default_factory=dict)
    task_evm: dict[str, TaskEvmPoint] = field(default_factory=dict)
    evm_methodology: dict[str, Any] = field(default_factory=dict)
    classifier: DelayClassificationService | None = None
    _delay_by_task: dict[str, Any] = field(default_factory=dict, repr=False)
    _metrics_cached: bool = field(default=False, repr=False)

    @classmethod
    def load(
        cls,
        project,
        *,
        dimension_keys: list[str] | None = None,
        requested_modes: dict[str, str] | None = None,
    ) -> GovernedMappingAnalyticsSession:
        """Load session data in bounded queries."""
        project_id = str(project.pk)
        data_date, _ = get_project_data_date(project_id)
        mode_context = DimensionModeService(project).build(requested_modes=requested_modes)

        keys = dimension_keys or ["trade", "package"]
        resolver = EffectiveMappingResolver(project)
        dimensions_by_key: dict[str, AnalyticalDimension] = {}
        for key in keys:
            dim = (
                AnalyticalDimension.objects.filter(
                    project_id=project_id,
                    dimension_key=key,
                    is_selected_for_analysis=True,
                    status=AnalyticalDimension.Status.ACTIVE,
                )
                .order_by("-revision_number")
                .first()
            )
            if dim:
                dimensions_by_key[key] = dim

        tasks = list(
            Task.objects.filter(project_id=project_id)
            .select_related("wbs_node", "schedule_activity")
            .only(
                "pk",
                "name",
                "activity_code",
                "stage",
                "sub_stage",
                "activity_type",
                "status",
                "start_date",
                "end_date",
                "actual_start",
                "actual_end",
                "early_finish",
                "total_float",
                "is_critical",
                "is_non_physical",
                "cost",
                "physical_percent_complete",
                "duration_percent_complete",
                "wbs_node_id",
                "schedule_activity_id",
            )
        )
        task_ids = [t.pk for t in tasks]
        tasks_by_id = {str(t.pk): t for t in tasks}

        reader = BindingGovernanceReader(project_id)
        trusted_ids = reader.trusted_task_ids()
        entities_by_task = reader.entity_gids_by_task(trusted_only=True)
        review_by_task = reader.entity_gids_by_task(trusted_only=False)

        resolutions: dict[str, dict[str, EffectiveMappingResult]] = {}
        for key, dim in dimensions_by_key.items():
            mapping_set = resolver.active_mapping_set(dim)
            if mapping_set:
                resolutions[key] = resolver.resolve_many_tasks(task_ids, dim)
            else:
                resolutions[key] = {}

        session = cls(
            project_id=project_id,
            data_date=data_date,
            mode_context=mode_context,
            tasks=tasks,
            tasks_by_id=tasks_by_id,
            task_ids=task_ids,
            dimensions_by_key=dimensions_by_key,
            resolutions_by_dimension=resolutions,
            trusted_task_ids=trusted_ids,
            entities_by_task=entities_by_task,
            review_entities_by_task=review_by_task,
            classifier=DelayClassificationService(project_id, data_date=data_date),
        )
        session._load_task_evm()
        return session

    def _load_task_evm(self) -> None:
        """Compute point-in-time PV/EV/AC per task using baseline scope."""
        from scheduling.services.calendar_utils import load_project_calendars, task_cal
        from scheduling.services.evm import (
            _earned_pct_at,
            _load_actual_costs,
            _planned_pct_at_dates,
        )

        scope = BaselineEVMScopeService(self.project_id).resolve(self.tasks)
        self.evm_methodology = scope.to_metadata()
        cal_map = load_project_calendars(self.project_id)
        task_cals = {str(t.pk): task_cal(t, cal_map) for t in self.tasks} if cal_map else {}
        today = self.data_date
        actual_costs = _load_actual_costs([str(t.pk) for t in self.tasks])

        for entry in scope.matched_entries:
            task = entry.task
            tid = str(task.pk)
            point = TaskEvmPoint()
            if entry.match_kind != ActivityMatchKind.MATCHED:
                point.unavailable_reason = "Task not matched to baseline scope."
                self.task_evm[tid] = point
                continue
            value = entry.baseline_cost
            if value is None:
                if entry.planned_start and entry.planned_finish:
                    value = float(max((entry.planned_finish - entry.planned_start).days + 1, 1))
                elif task.cost:
                    value = float(task.cost)
                else:
                    point.unavailable_reason = "No baseline cost or duration."
                    self.task_evm[tid] = point
                    continue
            if not entry.planned_start or not entry.planned_finish:
                point.unavailable_reason = "Missing planned dates for EVM."
                self.task_evm[tid] = point
                continue
            cal = task_cals.get(tid)
            pv_pct = _planned_pct_at_dates(entry.planned_start, entry.planned_finish, today, cal)
            ev_pct = _earned_pct_at(task, today, cal)
            point.bac = float(value)
            point.pv = pv_pct * float(value)
            point.ev = ev_pct * float(value)
            point.ac = actual_costs.get(tid, 0.0)
            point.available = True
            self.task_evm[tid] = point

        for task in self.tasks:
            tid = str(task.pk)
            if tid not in self.task_evm:
                self.task_evm[tid] = TaskEvmPoint(unavailable_reason="Outside baseline EVM scope.")

    def bucket_task_ids(self, dimension_key: str) -> dict[str, set[str]]:
        """Group task ids by governed value or virtual bucket."""
        buckets: dict[str, set[str]] = {}
        resolved = self.resolutions_by_dimension.get(dimension_key, {})
        dim = self.dimensions_by_key.get(dimension_key)
        cardinality = dim.cardinality if dim else "single"

        for tid in self.tasks_by_id:
            result = resolved.get(tid)
            if result is None:
                continue
            if result.resolution == "conflict":
                buckets.setdefault(CONFLICT_BUCKET, set()).add(tid)
                continue
            if result.resolution == "proposed_only":
                buckets.setdefault(PROPOSED_BUCKET, set()).add(tid)
                continue
            if result.resolution == "unmapped" or not result.values:
                buckets.setdefault(UNMAPPED_BUCKET, set()).add(tid)
                continue
            for val in result.values:
                vid = val["value_id"]
                buckets.setdefault(vid, set()).add(tid)

        if dim and cardinality == "single":
            assigned = sum(len(s) for k, s in buckets.items() if not k.startswith("__"))
            virtual = len(buckets.get(UNMAPPED_BUCKET, set())) + len(
                buckets.get(CONFLICT_BUCKET, set())
            )
            _ = assigned + virtual  # reconciliation available for tests

        return buckets

    def aggregate_evm(self, task_ids: set[str]) -> dict[str, Any]:
        """Sum PV/EV/AC; SPI/CPI from rolled components."""
        pv = ev = ac = bac = 0.0
        available_count = 0
        for tid in task_ids:
            point = self.task_evm.get(tid)
            if point and point.available:
                pv += point.pv
                ev += point.ev
                ac += point.ac
                bac += point.bac
                available_count += 1
        if available_count == 0:
            return {
                "pv": None,
                "ev": None,
                "ac": None,
                "bac": None,
                "spi": None,
                "cpi": None,
                "available": False,
                "task_count": len(task_ids),
            }
        return {
            "pv": round(pv, 2),
            "ev": round(ev, 2),
            "ac": round(ac, 2) if ac > 0 else None,
            "bac": round(bac, 2),
            "spi": round(ev / pv, 4) if pv > 0 else None,
            "cpi": round(ev / ac, 4) if ac > 0 else None,
            "available": True,
            "task_count": len(task_ids),
        }

    def aggregate_delay(self, task_ids: set[str]) -> dict[str, int]:
        """Delay counts for a task scope."""
        self.ensure_task_metrics_cache()
        primary_late = near_critical = completed_late = 0
        if not self.classifier:
            return {
                "primary_late_count": 0,
                "near_critical_late_count": 0,
                "completed_late_count": 0,
            }
        for tid in task_ids:
            delay = self._delay_by_task.get(tid)
            if not delay:
                task = self.tasks_by_id.get(tid)
                if not task:
                    continue
                trusted_ent = len(self.entities_by_task.get(tid, []))
                delay = self.classifier.classify_task(task, trusted_entity_count=trusted_ent)
            primary = delay.primary_delay_type
            if primary in DELAYED_PRIMARY:
                primary_late += 1
            if primary == "near_critical":
                near_critical += 1
            if primary == "completed_late":
                completed_late += 1
        return {
            "primary_late_count": primary_late,
            "near_critical_late_count": near_critical,
            "completed_late_count": completed_late,
        }

    def aggregate_model_scope(self, task_ids: set[str]) -> dict[str, int]:
        """Trusted model scope counts."""
        trusted_tasks = sum(1 for tid in task_ids if tid in self.trusted_task_ids)
        entity_gids: set[str] = set()
        review_gids: set[str] = set()
        for tid in task_ids:
            entity_gids.update(self.entities_by_task.get(tid, []))
            review_gids.update(self.review_entities_by_task.get(tid, []))
        return {
            "trusted_task_count": trusted_tasks,
            "trusted_entity_count": len(entity_gids),
            "review_entity_count": len(review_gids),
            "unmapped_task_count": len(task_ids) - trusted_tasks,
        }

    def schedulable_count(self, task_ids: set[str]) -> int:
        return sum(
            1
            for tid in task_ids
            if (t := self.tasks_by_id.get(tid))
            and not t.is_non_physical
            and t.start_date
            and t.end_date
        )

    def completed_count(self, task_ids: set[str]) -> int:
        return sum(
            1 for tid in task_ids if (t := self.tasks_by_id.get(tid)) and t.status == "complete"
        )

    def ensure_task_metrics_cache(self) -> None:
        """Classify delay once per task — reused by rollup and drilldowns."""
        if self._metrics_cached or not self.classifier:
            return
        for tid, task in self.tasks_by_id.items():
            trusted_ent = len(self.entities_by_task.get(tid, []))
            self._delay_by_task[tid] = self.classifier.classify_task(
                task, trusted_entity_count=trusted_ent
            )
        self._metrics_cached = True

    def _bucket_key_for_result(self, result: EffectiveMappingResult | None) -> str:
        if result is None:
            return UNMAPPED_BUCKET
        if result.resolution == "conflict":
            return CONFLICT_BUCKET
        if result.resolution == "proposed_only":
            return PROPOSED_BUCKET
        if result.resolution == "unmapped" or not result.values:
            return UNMAPPED_BUCKET
        return result.values[0]["value_id"]

    def build_dimension_rollup(self, dimension_key: str) -> dict[str, dict[str, Any]]:
        """Single-pass per-task accumulation into value/virtual buckets (DF-D3.1)."""
        self.ensure_task_metrics_cache()
        resolved = self.resolutions_by_dimension.get(dimension_key, {})
        dim = self.dimensions_by_key.get(dimension_key)
        cardinality = dim.cardinality if dim else "single"

        accum: dict[str, dict[str, Any]] = {}

        def _acc(key: str) -> dict[str, Any]:
            if key not in accum:
                accum[key] = {
                    "value_id": key,
                    "is_virtual_bucket": key.startswith("__"),
                    "task_ids": set(),
                    "pv": 0.0,
                    "ev": 0.0,
                    "ac": 0.0,
                    "bac": 0.0,
                    "evm_available_count": 0,
                    "primary_late_count": 0,
                    "near_critical_late_count": 0,
                    "completed_late_count": 0,
                    "trusted_entity_gids": set(),
                    "review_entity_gids": set(),
                    "schedulable_count": 0,
                    "completed_count": 0,
                }
            return accum[key]

        for tid, task in self.tasks_by_id.items():
            result = resolved.get(tid)
            if cardinality == "multi":
                if (
                    result
                    and result.values
                    and result.resolution
                    not in (
                        "conflict",
                        "proposed_only",
                        "unmapped",
                    )
                ):
                    keys = [v["value_id"] for v in result.values]
                else:
                    keys = [self._bucket_key_for_result(result)]
            else:
                keys = [self._bucket_key_for_result(result)]

            for key in keys:
                bucket = _acc(key)
                bucket["task_ids"].add(tid)
                point = self.task_evm.get(tid)
                if point and point.available:
                    bucket["pv"] += point.pv
                    bucket["ev"] += point.ev
                    bucket["ac"] += point.ac
                    bucket["bac"] += point.bac
                    bucket["evm_available_count"] += 1
                delay = self._delay_by_task.get(tid)
                if delay:
                    primary = delay.primary_delay_type
                    if primary in DELAYED_PRIMARY:
                        bucket["primary_late_count"] += 1
                    if primary == "near_critical":
                        bucket["near_critical_late_count"] += 1
                    if primary == "completed_late":
                        bucket["completed_late_count"] += 1
                bucket["trusted_entity_gids"].update(self.entities_by_task.get(tid, []))
                bucket["review_entity_gids"].update(self.review_entities_by_task.get(tid, []))
                if not task.is_non_physical and task.start_date and task.end_date:
                    bucket["schedulable_count"] += 1
                if task.status == "complete":
                    bucket["completed_count"] += 1

        for bucket in accum.values():
            task_ids = bucket["task_ids"]
            bucket["task_count"] = len(task_ids)
            bucket["trusted_task_count"] = sum(
                1 for tid in task_ids if tid in self.trusted_task_ids
            )
            bucket["trusted_entity_count"] = len(bucket["trusted_entity_gids"])
            bucket["review_entity_count"] = len(bucket["review_entity_gids"])
            bucket["unmapped_task_count"] = bucket["task_count"] - bucket["trusted_task_count"]
            if bucket["evm_available_count"] == 0:
                bucket["evm"] = {
                    "pv": None,
                    "ev": None,
                    "ac": None,
                    "bac": None,
                    "spi": None,
                    "cpi": None,
                    "available": False,
                    "task_count": bucket["task_count"],
                }
            else:
                pv, ev, ac = bucket["pv"], bucket["ev"], bucket["ac"]
                bucket["evm"] = {
                    "pv": round(pv, 2),
                    "ev": round(ev, 2),
                    "ac": round(ac, 2) if ac > 0 else None,
                    "bac": round(bucket["bac"], 2),
                    "spi": round(ev / pv, 4) if pv > 0 else None,
                    "cpi": round(ev / ac, 4) if ac > 0 else None,
                    "available": True,
                    "task_count": bucket["task_count"],
                }
            bucket["delay"] = {
                "primary_late_count": bucket.pop("primary_late_count"),
                "near_critical_late_count": bucket.pop("near_critical_late_count"),
                "completed_late_count": bucket.pop("completed_late_count"),
            }
            bucket["model_scope"] = {
                "trusted_task_count": bucket.pop("trusted_task_count"),
                "trusted_entity_count": bucket.pop("trusted_entity_count"),
                "review_entity_count": bucket.pop("review_entity_count"),
                "unmapped_task_count": bucket.pop("unmapped_task_count"),
            }
            bucket.pop("trusted_entity_gids", None)
            bucket.pop("review_entity_gids", None)
            bucket.pop("pv", None)
            bucket.pop("ev", None)
            bucket.pop("ac", None)
            bucket.pop("bac", None)
            bucket.pop("evm_available_count", None)

        return accum
