# scheduling/services/executive_controls/activity_drilldown.py
"""Paginated activity drilldown for one matrix/trade group."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.urls import reverse

from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.dimension_registry import (
    UNKNOWN_KEY,
    ExecutiveDimensionRegistry,
)
from scheduling.services.executive_controls.matrix_filters import (
    MAX_PAGE_SIZE,
    ExecutiveMatrixFilters,
)
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.progress_aggregation import (
    ScheduleProgressAggregationService,
)
from scheduling.services.executive_controls.scope_classification import ScopeClassificationResolver
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)


class ActivityDrilldownService:
    """Read-only paginated source table for a selected group."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._registry = ExecutiveDimensionRegistry(self.project_id)
        self._reader = BindingGovernanceReader(self.project_id)
        self._scope = ScopeClassificationResolver()
        self._progress = ScheduleProgressAggregationService(self.project_id)
        self._trusted_task_ids = self._reader.trusted_task_ids()
        self._entities_by_task = self._reader.entity_gids_by_task(trusted_only=True)

    def build(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        """Return paginated activity rows for group_key + dimension."""
        from scheduling.services.executive_controls.performance_cube import (
            ProjectPerformanceCubeService,
        )

        if not filters.group_key or not filters.dimension:
            return {"error": "group_key and dimension required", "rows": []}

        cube = ProjectPerformanceCubeService(self.project)
        tasks = cube._load_tasks(filters)
        key_fn = self._registry.key_fn(filters.dimension)

        matched = [t for t in tasks if key_fn(t)[0] == filters.group_key]
        if filters.group_key == UNKNOWN_KEY:
            matched = [t for t in tasks if key_fn(t)[0] == UNKNOWN_KEY]

        data_date, _ = get_project_data_date(self.project_id)
        classifier = DelayClassificationService(
            self.project_id,
            day_type=filters.day_type,
            data_date=data_date,
        )

        sort = filters.sort
        sort_reverse = filters.sort_dir != "asc"

        def _sort_key(t):
            if sort == "name":
                return (t.name or "").lower()
            if sort == "float":
                return t.total_float if t.total_float is not None else 9999
            if sort == "cost":
                return float(t.cost or 0)
            return t.activity_code or t.name or ""

        matched.sort(key=_sort_key, reverse=sort_reverse)

        total = len(matched)
        page_size = min(MAX_PAGE_SIZE, max(1, filters.page_size))
        start = (filters.page - 1) * page_size
        page_tasks = matched[start : start + page_size]

        rows: list[dict[str, Any]] = []
        for task in page_tasks:
            tid = str(task.pk)
            scope = self._scope.resolve(task, trusted_model_linked=tid in self._trusted_task_ids)
            delay = classifier.classify_task(
                task,
                trusted_entity_count=len(self._entities_by_task.get(tid, [])),
                scope_classification=scope.classification,
                scope_authoritative=scope.authoritative,
            )
            prog = self._progress.aggregate([task], weighting_mode=filters.weighting_mode)

            rows.append(
                {
                    "task_id": tid,
                    "activity_code": task.activity_code or "",
                    "name": task.name,
                    "status": task.status,
                    "stage": task.stage or "",
                    "sub_stage": task.sub_stage or "",
                    "scope_classification": scope.classification,
                    "scope_authority": scope.authority_level,
                    "scope_authoritative": scope.authoritative,
                    "baseline_finish": task.end_date.isoformat() if task.end_date else None,
                    "forecast_finish": (task.early_finish or task.end_date).isoformat()
                    if (task.early_finish or task.end_date)
                    else None,
                    "progress_pct": prog.get("actual_progress_pct"),
                    "variance_pct": prog.get("variance_pct"),
                    "total_float": task.total_float,
                    "is_critical": task.is_critical,
                    "primary_delay_state": delay.primary_delay_type,
                    "secondary_indicators": delay.secondary_indicators,
                    "budget": float(task.cost) if task.cost else None,
                    "trusted_entity_count": len(self._entities_by_task.get(tid, [])),
                    "weighting_label": prog.get("weighting_label"),
                    "links": {
                        "gantt": reverse("scheduling:schedule", kwargs={"pk": self.project_id})
                        + f"?highlight={tid}",
                        "governance": reverse(
                            "scheduling:link_governance_overview",
                            kwargs={"pk": self.project_id},
                        ),
                        "task_detail": reverse(
                            "scheduling:schedule", kwargs={"pk": self.project_id}
                        )
                        + f"?task={tid}",
                    },
                }
            )

        return {
            "section": "activity_drilldown",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "group_key": filters.group_key,
            "dimension": filters.dimension,
            "filters": filters.to_query(),
            "data_date": data_date.isoformat(),
            "rows": rows,
            "pagination": {
                "page": filters.page,
                "page_size": page_size,
                "total": total,
                "pages": max(1, (total + page_size - 1) // page_size),
            },
            "calculated_at": datetime.now(UTC).isoformat(),
        }
