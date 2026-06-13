# scheduling/services/executive_controls/coverage.py
"""Analytical coverage service — explicit denominators, no conflation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db.models import Count, Q

from scheduling.services.executive_controls.contracts import CoverageItem
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.scope_classification import ScopeClassificationResolver
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)


class AnalyticalCoverageService:
    """Project-scoped coverage counts and ratios — read-only."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)
        self._reader = BindingGovernanceReader(self.project_id)
        self._scope = ScopeClassificationResolver()
        self._calculated_at = datetime.now(UTC).isoformat()
        self._data_date, _ = get_project_data_date(self.project_id)

    def build(self) -> dict[str, Any]:
        """Return full coverage payload grouped by domain."""
        from scheduling.models import P6ResourceAssignment, Task

        tasks = list(
            Task.objects.filter(project_id=self.project_id).only(
                "pk",
                "start_date",
                "end_date",
                "actual_start",
                "actual_end",
                "status",
                "cost",
                "physical_percent_complete",
                "duration_percent_complete",
                "total_float",
                "stage",
                "activity_type",
                "activity_code",
                "name",
                "is_non_physical",
            )
        )
        all_count = len(tasks)
        schedulable = [t for t in tasks if t.start_date and t.end_date]
        schedulable_count = len(schedulable)

        trusted_task_ids = self._reader.trusted_task_ids()
        trusted_entity_gids = self._reader.trusted_entity_gids(ifc_scope=True)
        review_task_ids = {
            str(pk)
            for pk in self._reader.review_bindings_qs().values_list("task_id", flat=True).distinct()
        }
        indexed_entity_count = len(self._reader._project_ifc_entity_gids())

        authoritative_scope = 0
        suggestion_scope = 0
        unknown_scope = 0
        for t in tasks:
            linked = str(t.pk) in trusted_task_ids
            result = self._scope.resolve(t, trusted_model_linked=linked)
            if result.authoritative:
                authoritative_scope += 1
            elif result.authority_level == MetricAuthority.SUGGESTION.value:
                suggestion_scope += 1
            else:
                unknown_scope += 1

        with_baseline = sum(1 for t in tasks if t.end_date)
        agg = Task.objects.filter(project_id=self.project_id).aggregate(
            with_float=Count("pk", filter=Q(total_float__isnull=False)),
            milestones=Count(
                "pk",
                filter=Q(activity_type__icontains="milestone") | Q(name__icontains="milestone"),
            ),
            with_stage=Count("pk", filter=~Q(stage="")),
        )

        in_flight = [
            t
            for t in tasks
            if t.status in ("active", "delayed", "complete") or t.actual_start or t.actual_end
        ]
        with_progress = sum(
            1
            for t in in_flight
            if t.physical_percent_complete is not None or t.duration_percent_complete is not None
        )

        with_cost = Task.objects.filter(
            project_id=self.project_id, cost__isnull=False, cost__gt=0
        ).count()
        ra_planned = P6ResourceAssignment.objects.filter(
            project_id=self.project_id, is_pending=False, planned_cost__gt=0
        ).count()
        ra_actual = P6ResourceAssignment.objects.filter(
            project_id=self.project_id, is_pending=False, actual_cost__gt=0
        ).count()
        labor_units = P6ResourceAssignment.objects.filter(
            project_id=self.project_id,
            is_pending=False,
            resource_type__icontains="labor",
        ).aggregate(
            planned=Count("pk", filter=Q(planned_units__gt=0)),
            actual=Count("pk", filter=Q(actual_units__gt=0)),
        )

        schedule_items = self._items(
            [
                ("e8.all_activity_count", "All tasks", all_count, None),
                (
                    "e8.schedulable_activity_count",
                    "Schedulable tasks",
                    schedulable_count,
                    all_count,
                ),
                ("e8.baseline_coverage", "Baseline/reference finish", with_baseline, all_count),
                (
                    "e8.progress_coverage",
                    "Progress fields",
                    with_progress,
                    max(len(in_flight), 1),
                ),
                (
                    "e8.schedule_field_coverage",
                    "Start and end dates",
                    schedulable_count,
                    all_count,
                ),
            ]
        )

        scope_items = self._items(
            [
                (
                    "e8.scope_classification_coverage",
                    "Authoritative scope",
                    authoritative_scope,
                    all_count,
                ),
                ("e8.unknown_scope_count", "Unknown scope", unknown_scope, all_count),
            ]
        )
        scope_items.append(
            CoverageItem(
                metric_id="e8.suggestion_scope_count",
                label="Suggestion-only scope",
                numerator=suggestion_scope,
                denominator=all_count,
                percentage=round(100.0 * suggestion_scope / all_count, 2) if all_count else None,
                available=all_count > 0,
                authority=MetricAuthority.SUGGESTION.value,
                source="ScopeClassificationResolver",
                caveat="Excluded from authoritative scope denominators.",
                drilldown_filter={"scope_authoritative": "false"},
            ).to_dict()
        )

        hierarchy_items = self._items(
            [
                ("e8.hierarchy_coverage", "Stage proxy hierarchy", agg["with_stage"], all_count),
            ]
        )
        hierarchy_items.append(
            CoverageItem(
                metric_id="e8.wbs_link_coverage",
                label="WBS node linkage",
                numerator=0,
                denominator=all_count,
                percentage=None,
                available=False,
                authority=MetricAuthority.UNAVAILABLE.value,
                source="P6WBSNode",
                caveat="Task.wbs_node FK not implemented — 0% WBS linkage.",
                drilldown_filter={},
            ).to_dict()
        )

        model_items = self._items(
            [
                (
                    "e8.trusted_task_link_coverage",
                    "Trusted-linked tasks",
                    len(trusted_task_ids),
                    all_count,
                ),
                (
                    "e8.trusted_entity_link_coverage",
                    "Trusted-linked entities",
                    len(trusted_entity_gids),
                    indexed_entity_count,
                ),
                (
                    "e8.review_coverage",
                    "Review suggestions (tasks)",
                    len(review_task_ids),
                    None,
                ),
            ]
        )

        cost_items = self._items(
            [
                (
                    "e8.cost_coverage",
                    "Task schedule cost",
                    with_cost,
                    schedulable_count or all_count,
                ),
                (
                    "e8.actual_cost_coverage",
                    "Resource actual cost rows",
                    ra_actual,
                    max(ra_planned, 1),
                ),
            ]
        )

        resource_items = self._items(
            [
                (
                    "e8.planned_manhours",
                    "Labor assignments with planned units",
                    labor_units["planned"] or 0,
                    None,
                ),
                (
                    "e8.actual_manhours",
                    "Labor assignments with actual units",
                    labor_units["actual"] or 0,
                    None,
                ),
            ]
        )

        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "data_date": self._data_date.isoformat(),
            "calculated_at": self._calculated_at,
            "denominators": {
                "all_tasks": all_count,
                "schedulable_tasks": schedulable_count,
                "indexed_entities": indexed_entity_count,
                "in_flight_tasks": len(in_flight),
            },
            "schedule": schedule_items,
            "scope": scope_items,
            "hierarchy": hierarchy_items,
            "model_links": model_items,
            "cost": cost_items,
            "resources": resource_items,
            "milestones": agg["milestones"],
            "float_coverage": agg["with_float"],
            "caveats": [
                "Task and entity coverage use separate denominators — never combine.",
                "Review, property hints, and legacy M2M excluded from trusted counts.",
            ],
        }

    def _items(self, specs: list[tuple[str, str, int, int | None]]) -> list[dict[str, Any]]:
        from scheduling.services.executive_controls.methodology import E8_METRIC_REGISTRY

        out: list[dict[str, Any]] = []
        for metric_id, _label, num, den in specs:
            definition = E8_METRIC_REGISTRY.get(metric_id)
            if not definition:
                continue
            pct: float | None = None
            available = True
            if den is not None and den > 0:
                pct = round(100.0 * num / den, 2)
            elif den == 0:
                available = False
            item = CoverageItem(
                metric_id=metric_id,
                label=definition.label,
                numerator=num,
                denominator=den,
                percentage=pct,
                available=available,
                authority=definition.authority_level,
                source=definition.primary_source,
                caveat=definition.caveat,
                drilldown_filter=dict(definition.drilldown_filter),
            )
            out.append(item.to_dict())
        return out
