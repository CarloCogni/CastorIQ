# scheduling/services/executive_controls/delays.py
"""Executive delay summary and paginated detail — read-only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db.models import QuerySet

from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.enums import DayType, DelayType
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.scope_classification import ScopeClassificationResolver
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50


@dataclass
class DelayFilters:
    """Query filters for delay detail endpoint."""

    delay_type: str | None = None
    status: str | None = None
    stage: str | None = None
    critical: bool | None = None
    negative_float: bool | None = None
    near_critical: bool | None = None
    linked_trusted: bool | None = None
    scope_classification: str | None = None
    scope_authoritative: bool | None = None
    day_type: str = DayType.WORKING.value
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_params(cls, params: dict[str, str]) -> DelayFilters:
        def _bool(key: str) -> bool | None:
            val = params.get(key)
            if val in ("1", "true", "yes"):
                return True
            if val in ("0", "false", "no"):
                return False
            return None

        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(MAX_PAGE_SIZE, max(1, int(params.get("page_size", DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE

        return cls(
            delay_type=params.get("delay_type") or params.get("type") or None,
            status=params.get("status") or None,
            stage=params.get("stage") or params.get("wbs") or None,
            critical=_bool("critical"),
            negative_float=_bool("negative_float"),
            near_critical=_bool("near_critical"),
            linked_trusted=_bool("linked_trusted"),
            scope_classification=params.get("scope_classification") or None,
            scope_authoritative=_bool("scope_authoritative"),
            day_type=params.get("day_type", DayType.WORKING.value),
            page=page,
            page_size=page_size,
        )


class ExecutiveDelayService:
    """Delay summary and paginated classification for executive controls."""

    def __init__(self, project_id: str, *, near_critical_threshold: int = 5) -> None:
        self.project_id = str(project_id)
        self.near_critical_threshold = near_critical_threshold
        self._reader = BindingGovernanceReader(self.project_id)
        self._scope = ScopeClassificationResolver()
        self._trusted_task_ids = self._reader.trusted_task_ids()
        self._entities_by_task = self._reader.entity_gids_by_task(trusted_only=True)

    def _classifier(self, filters: DelayFilters | None = None) -> DelayClassificationService:
        f = filters or DelayFilters()
        data_date, _ = get_project_data_date(self.project_id)
        return DelayClassificationService(
            self.project_id,
            near_critical_threshold=self.near_critical_threshold,
            day_type=f.day_type,
            data_date=data_date,
        )

    def _base_qs(self) -> QuerySet:
        from scheduling.models import Task

        return (
            Task.objects.filter(project_id=self.project_id)
            .order_by("activity_code", "name", "pk")
            .only(
                "pk",
                "name",
                "activity_code",
                "status",
                "stage",
                "start_date",
                "end_date",
                "actual_start",
                "actual_end",
                "early_finish",
                "total_float",
                "is_critical",
                "activity_type",
                "activity_code",
                "is_non_physical",
            )
        )

    def _classify_all(self, classifier: DelayClassificationService, tasks) -> list:
        results = []
        for task in tasks:
            tid = str(task.pk)
            scope = self._scope.resolve(task, trusted_model_linked=tid in self._trusted_task_ids)
            results.append(
                classifier.classify_task(
                    task,
                    trusted_entity_count=len(self._entities_by_task.get(tid, [])),
                    scope_classification=scope.classification,
                    scope_authoritative=scope.authoritative,
                )
            )
        return results

    def build_summary(self, filters: DelayFilters | None = None) -> dict[str, Any]:
        """Aggregate delay counts without full task payloads."""
        filters = filters or DelayFilters()
        classifier = self._classifier(filters)
        tasks = list(self._base_qs())
        results = self._classify_all(classifier, tasks)
        counts = classifier.summarize_counts(results)

        primary_counts = {dt.value: 0 for dt in DelayType}
        for r in results:
            if r.primary_delay_type in primary_counts:
                primary_counts[r.primary_delay_type] += 1

        finish = classifier.project_finish_variance(tasks)

        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "data_date": classifier.data_date.isoformat(),
            "calculated_at": datetime.now(UTC).isoformat(),
            "day_type": filters.day_type,
            "near_critical_threshold": self.near_critical_threshold,
            "primary_counts": primary_counts,
            "indicator_counts": counts,
            "project_finish_variance": finish,
            "task_count": len(tasks),
            "caveats": [
                "No generic slip field — use typed delay modes.",
                "Baseline = Current Reference/Baseline Fields from Imported Schedule.",
            ],
        }

    def build_detail(self, filters: DelayFilters) -> dict[str, Any]:
        """Paginated delay detail with stable ordering."""
        classifier = self._classifier(filters)
        tasks = list(self._base_qs())

        if filters.status:
            tasks = [t for t in tasks if t.status == filters.status]
        if filters.stage:
            tasks = [t for t in tasks if t.stage == filters.stage]

        classified = self._classify_all(classifier, tasks)
        paired = list(zip(tasks, classified, strict=True))

        if filters.delay_type:
            paired = [
                (t, r)
                for t, r in paired
                if r.primary_delay_type == filters.delay_type
                or filters.delay_type in r.secondary_indicators
            ]
        if filters.critical is True:
            paired = [(t, r) for t, r in paired if r.is_critical]
        if filters.negative_float is True:
            paired = [
                (t, r)
                for t, r in paired
                if DelayType.NEGATIVE_FLOAT.value in r.secondary_indicators
            ]
        if filters.near_critical is True:
            paired = [
                (t, r) for t, r in paired if DelayType.NEAR_CRITICAL.value in r.secondary_indicators
            ]
        if filters.linked_trusted is True:
            paired = [(t, r) for t, r in paired if r.trusted_entity_count > 0]
        elif filters.linked_trusted is False:
            paired = [(t, r) for t, r in paired if r.trusted_entity_count == 0]
        if filters.scope_classification:
            paired = [
                (t, r) for t, r in paired if r.scope_classification == filters.scope_classification
            ]
        if filters.scope_authoritative is True:
            paired = [(t, r) for t, r in paired if r.scope_authoritative]
        elif filters.scope_authoritative is False:
            paired = [(t, r) for t, r in paired if not r.scope_authoritative]

        total = len(paired)
        start = (filters.page - 1) * filters.page_size
        end = start + filters.page_size
        page_pairs = paired[start:end]

        rows = []
        for task, result in page_pairs:
            rows.append(
                {
                    "task_id": str(task.pk),
                    "activity_code": task.activity_code,
                    "name": task.name,
                    "status": task.status,
                    "stage": task.stage,
                    "delay": result.to_dict(),
                }
            )

        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "data_date": classifier.data_date.isoformat(),
            "calculated_at": datetime.now(UTC).isoformat(),
            "filters": {
                "delay_type": filters.delay_type,
                "status": filters.status,
                "stage": filters.stage,
                "critical": filters.critical,
                "negative_float": filters.negative_float,
                "near_critical": filters.near_critical,
                "linked_trusted": filters.linked_trusted,
                "scope_classification": filters.scope_classification,
                "day_type": filters.day_type,
            },
            "pagination": {
                "page": filters.page,
                "page_size": filters.page_size,
                "total": total,
                "pages": max(1, (total + filters.page_size - 1) // filters.page_size),
            },
            "rows": rows,
        }
