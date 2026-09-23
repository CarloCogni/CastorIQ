# scheduling/services/executive_controls/performance_cube.py
"""Server-side project performance cube — grouped aggregation without full serialization."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.urls import reverse

from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.dimension_registry import (
    UNKNOWN_KEY,
    ExecutiveDimensionRegistry,
)
from scheduling.services.executive_controls.enums import DelayType, MetricAuthority
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.progress_aggregation import (
    ScheduleProgressAggregationService,
)
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

DELAYED_PRIMARY = {
    DelayType.COMPLETED_LATE.value,
    DelayType.CURRENTLY_LATE.value,
    DelayType.FORECAST_LATE.value,
}


@dataclass
class _GroupBucket:
    key: str
    label: str
    authority: str
    task_ids: list[str] = field(default_factory=list)
    tasks: list = field(default_factory=list)
    schedulable: int = 0
    complete: int = 0
    active: int = 0
    planned: int = 0
    critical: int = 0
    negative_float: int = 0
    unknown_scope: int = 0
    primary_late: int = 0
    budget_sum: float = 0.0
    budget_tasks: int = 0
    cost_tasks: int = 0
    trusted_tasks: int = 0
    trusted_entities: int = 0
    delayed_trusted: int = 0
    critical_trusted: int = 0
    primary_counts: dict[str, int] = field(default_factory=dict)


class ProjectPerformanceCubeService:
    """Read-only grouped performance matrix."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._registry = ExecutiveDimensionRegistry(self.project_id)
        self._reader = BindingGovernanceReader(self.project_id)
        self._progress = ScheduleProgressAggregationService(self.project_id)
        self._trusted_task_ids = self._reader.trusted_task_ids()
        self._entities_by_task = self._reader.entity_gids_by_task(trusted_only=True)

    def _filtered_qs(self, filters: ExecutiveMatrixFilters):
        from scheduling.models import Task

        qs = Task.objects.filter(project_id=self.project_id)
        qs = self._registry.apply_parent_filter(qs, filters)
        if filters.stage:
            qs = qs.filter(stage=filters.stage)
        if filters.sub_stage or filters.trade or filters.package:
            val = filters.sub_stage or filters.trade or filters.package
            qs = qs.filter(sub_stage=val)
        if filters.status:
            qs = qs.filter(status=filters.status)
        if filters.physical_scope == "physical":
            qs = qs.filter(is_non_physical=False)
        elif filters.physical_scope == "non_physical":
            qs = qs.filter(is_non_physical=True)
        if filters.linked_trusted is True:
            qs = qs.filter(pk__in=self._trusted_task_ids)
        elif filters.linked_trusted is False:
            qs = qs.exclude(pk__in=self._trusted_task_ids)
        if filters.critical_only:
            qs = qs.filter(is_critical=True)
        return qs

    def _load_tasks(self, filters: ExecutiveMatrixFilters) -> list:
        return list(
            self._filtered_qs(filters).only(
                "pk",
                "activity_code",
                "name",
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
                "calendar_object_id",
            )
        )

    def _build_buckets(
        self,
        tasks: list,
        dimension_id: str,
        *,
        filters: ExecutiveMatrixFilters,
    ) -> dict[str, _GroupBucket]:
        if dimension_id not in ExecutiveDimensionRegistry.SUPPORTED_IDS:
            raise ValueError(f"Unsupported dimension: {dimension_id}")

        key_fn = self._registry.key_fn(dimension_id)
        data_date, _ = get_project_data_date(self.project_id)
        classifier = DelayClassificationService(
            self.project_id,
            day_type=filters.day_type,
            data_date=data_date,
        )

        buckets: dict[str, _GroupBucket] = {}

        for task in tasks:
            key, label, authority = key_fn(task)

            if filters.authoritative_only and authority in (
                MetricAuthority.SUGGESTION.value,
                MetricAuthority.UNAVAILABLE.value,
            ):
                if dimension_id in ("sub_stage", "scope_suggestion"):
                    continue
                if key == UNKNOWN_KEY and dimension_id == "scope_authoritative":
                    pass
                elif dimension_id == "sub_stage":
                    continue

            if (
                filters.classification_authority == "authoritative"
                and authority != MetricAuthority.AUTHORITATIVE.value
            ):
                if dimension_id.startswith("scope_"):
                    continue
            if (
                filters.classification_authority == "suggestion"
                and authority != MetricAuthority.SUGGESTION.value
            ):
                if dimension_id == "scope_suggestion":
                    continue

            bucket = buckets.get(key)
            if bucket is None:
                bucket = _GroupBucket(key=key, label=label, authority=authority)
                buckets[key] = bucket

            tid = str(task.pk)
            bucket.task_ids.append(tid)
            bucket.tasks.append(task)

            if task.start_date and task.end_date:
                bucket.schedulable += 1
            if task.status == "complete":
                bucket.complete += 1
            elif task.status == "active":
                bucket.active += 1
            else:
                bucket.planned += 1
            if task.is_critical:
                bucket.critical += 1
            if task.total_float is not None and task.total_float < 0:
                bucket.negative_float += 1
            if task.cost and float(task.cost) > 0:
                bucket.budget_sum += float(task.cost)
                bucket.budget_tasks += 1
                bucket.cost_tasks += 1

            delay = classifier.classify_task(
                task,
                trusted_entity_count=len(self._entities_by_task.get(tid, [])),
            )
            primary = delay.primary_delay_type
            bucket.primary_counts[primary] = bucket.primary_counts.get(primary, 0) + 1
            if primary in DELAYED_PRIMARY:
                bucket.primary_late += 1

            if tid in self._trusted_task_ids:
                bucket.trusted_tasks += 1
                ent_n = len(self._entities_by_task.get(tid, []))
                bucket.trusted_entities += ent_n
                if primary in DELAYED_PRIMARY:
                    bucket.delayed_trusted += 1
                if task.is_critical:
                    bucket.critical_trusted += 1

        return buckets

    def _sort_buckets(
        self, buckets: list[_GroupBucket], filters: ExecutiveMatrixFilters
    ) -> list[_GroupBucket]:
        reverse = filters.sort_dir != "asc"
        key_name = filters.sort

        def _key(b: _GroupBucket) -> float | str:
            if key_name == "label":
                return b.label.lower()
            if key_name == "variance":
                prog = self._progress.aggregate(b.tasks, weighting_mode=filters.weighting_mode)
                return prog.get("variance_pct") or 0.0
            if key_name == "budget":
                return b.budget_sum
            if key_name == "delayed":
                return b.primary_late
            return len(b.task_ids)

        return sorted(buckets, key=_key, reverse=reverse)

    def _row_from_bucket(
        self,
        bucket: _GroupBucket,
        *,
        filters: ExecutiveMatrixFilters,
        dimension_def,
    ) -> dict[str, Any]:
        progress = self._progress.aggregate(
            bucket.tasks,
            weighting_mode=filters.weighting_mode,
        )
        activity_count = len(bucket.task_ids)
        budget_available = bucket.budget_tasks > 0

        drill_params = filters.to_query()
        drill_params["group_key"] = bucket.key
        drill_params["dimension"] = filters.dimension

        activity_url = reverse(
            "scheduling:executive_controls_matrix_activities",
            kwargs={"pk": self.project_id},
        )
        qs = filters.query_string()
        group_q = f"group_key={bucket.key}&dimension={filters.dimension}"
        activity_drilldown = f"{activity_url}?{group_q}" + (f"&{qs}" if qs else "")

        next_dim = filters.next_dimension or ("sub_stage" if filters.dimension == "stage" else None)
        expand_url = ""
        if next_dim and bucket.key != UNKNOWN_KEY:
            from urllib.parse import urlencode

            expand_params = filters.to_query()
            expand_params["dimension"] = next_dim
            expand_params["parent_dimension"] = filters.dimension
            expand_params["parent_key"] = bucket.key
            expand_url = (
                reverse(
                    "scheduling:executive_controls_matrix_rows",
                    kwargs={"pk": self.project_id},
                )
                + "?"
                + urlencode(expand_params)
            )

        return {
            "dimension": filters.dimension,
            "key": bucket.key,
            "label": bucket.label,
            "authority": bucket.authority,
            "parent_context": {
                "parent_dimension": filters.parent_dimension,
                "parent_key": filters.parent_key,
            },
            "population": {
                "activity_count": activity_count,
                "schedulable_count": bucket.schedulable,
                "complete_count": bucket.complete,
                "in_progress_count": bucket.active,
                "not_started_count": bucket.planned,
                "unknown_unclassified_count": 1 if bucket.key == UNKNOWN_KEY else 0,
            },
            "schedule": {
                "planned_progress_pct": progress.get("planned_progress_pct"),
                "actual_progress_pct": progress.get("actual_progress_pct"),
                "variance_pct": progress.get("variance_pct"),
                "weighting_mode": progress.get("weighting_mode"),
                "weighting_label": progress.get("weighting_label"),
                "critical_count": bucket.critical,
                "negative_float_count": bucket.negative_float,
                "primary_late_count": bucket.primary_late,
                "primary_counts": bucket.primary_counts,
            },
            "cost": {
                "budget_total": round(bucket.budget_sum, 2) if budget_available else None,
                "budget_task_count": bucket.budget_tasks,
                "cost_coverage_pct": round(100.0 * bucket.cost_tasks / activity_count, 2)
                if activity_count
                else None,
                "available": budget_available,
                "unavailable_reason": "No task cost in group" if not budget_available else "",
            },
            "model_impact": {
                "trusted_task_count": bucket.trusted_tasks,
                "trusted_entity_count": bucket.trusted_entities,
                "delayed_trusted_task_count": bucket.delayed_trusted,
                "critical_trusted_task_count": bucket.critical_trusted,
                "entity_count_caveat": (
                    "Entity counts sum per task — may overlap if one entity maps to "
                    "multiple tasks; not a distinct GlobalId dedupe."
                ),
            },
            "coverage": {
                "progress_coverage_pct": progress.get("coverage", {}).get("schedulable_pct"),
                "classification_authority": bucket.authority,
            },
            "navigation": {
                "next_dimension": next_dim,
                "expand_url": expand_url,
                "activity_drilldown_url": activity_drilldown,
                "model_impact_url": reverse(
                    "scheduling:executive_controls",
                    kwargs={"pk": self.project_id},
                )
                + f"?linked_trusted=1&stage={bucket.key}"
                if filters.dimension == "stage" and bucket.key != UNKNOWN_KEY
                else reverse("scheduling:executive_controls", kwargs={"pk": self.project_id}),
            },
            "caveats": [dimension_def.caveat] if dimension_def else [],
        }

    def build_matrix_shell(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        """Lightweight matrix page metadata — no task loop."""
        dimensions = self._registry.discover()
        dim = self._registry.get(filters.dimension)
        ctx = AnalyticalContextService(self.project).build()

        return {
            "section": "matrix_shell",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "analytical_context": ctx,
            "selected_dimension": dim.to_dict() if dim else None,
            "available_dimensions": [d.to_dict() for d in dimensions],
            "filters": filters.to_query(),
            "warnings": [
                "Stage/sub-stage are hierarchy proxies — not contractual WBS.",
                "Trusted entity counts within a group may overlap across tasks.",
            ],
        }

    def build_rows(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        """Paginated matrix rows for one dimension."""
        dimension_id = filters.dimension
        if dimension_id not in ExecutiveDimensionRegistry.SUPPORTED_IDS:
            return {
                "error": f"Unsupported dimension: {dimension_id}",
                "available_dimensions": [d.dimension_id for d in self._registry.discover()],
            }

        dim_def = self._registry.get(dimension_id)
        tasks = self._load_tasks(filters)
        buckets_map = self._build_buckets(tasks, dimension_id, filters=filters)
        buckets = self._sort_buckets(list(buckets_map.values()), filters)

        total_groups = len(buckets)
        start = (filters.page - 1) * filters.page_size
        end = start + filters.page_size
        page_buckets = buckets[start:end]

        rows = [
            self._row_from_bucket(b, filters=filters, dimension_def=dim_def) for b in page_buckets
        ]

        summary_progress = self._progress.aggregate(tasks, weighting_mode=filters.weighting_mode)

        return {
            "section": "matrix_rows",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "selected_dimension": dim_def.to_dict() if dim_def else None,
            "available_dimensions": [d.to_dict() for d in self._registry.discover()],
            "filters": filters.to_query(),
            "summary": {
                "filtered_task_count": len(tasks),
                "group_count": total_groups,
                "progress": summary_progress,
            },
            "rows": rows,
            "pagination": {
                "page": filters.page,
                "page_size": filters.page_size,
                "total": total_groups,
                "pages": max(1, (total_groups + filters.page_size - 1) // filters.page_size),
            },
            "warnings": [
                "Parent/child group totals are not additive across hierarchy levels.",
                "Budget totals exclude tasks without cost.",
            ],
            "calculated_at": datetime.now(UTC).isoformat(),
        }
