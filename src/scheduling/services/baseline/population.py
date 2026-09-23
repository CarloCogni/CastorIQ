# scheduling/services/baseline/population.py
"""Populate BaselineTaskState rows from explicit source evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction

from scheduling.models import (
    BaselineAuditEvent,
    BaselineTaskState,
    BaselineVersion,
    ScheduleActivity,
    Task,
)
from scheduling.services.baseline.audit import record_baseline_event
from scheduling.services.baseline.exceptions import BaselineValidationError
from scheduling.services.baseline.lifecycle import BaselineVersionService

logger = logging.getLogger(__name__)

BULK_BATCH = 500


class PopulationSourceMode(StrEnum):
    """Explicit population source — never implicit."""

    CURRENT_OPERATIONAL = "current_operational"
    SOURCE_VERSION = "source_version"
    EXPLICIT_DTO = "explicit_dto"


@dataclass(frozen=True)
class TaskStateDTO:
    """Manual baseline task snapshot input."""

    schedule_activity_id: str
    name_snapshot: str
    activity_code: str = ""
    source_task_id: str = ""
    planned_start: date | None = None
    planned_finish: date | None = None
    duration_days: int | None = None
    calendar_reference: str = ""
    baseline_cost: Decimal | None = None
    planned_resource_units: Decimal | None = None
    progress_basis: str = ""
    activity_type: str = ""
    source_metadata: dict[str, Any] | None = None
    field_provenance: dict[str, Any] | None = None


class BaselinePopulationService:
    """Create BaselineTaskState snapshots for a draft baseline."""

    @classmethod
    def populate(
        cls,
        baseline: BaselineVersion,
        *,
        mode: PopulationSourceMode,
        actor: AbstractUser | None = None,
        dtos: list[TaskStateDTO] | None = None,
        replace_existing: bool = False,
    ) -> int:
        """Bulk-populate task states. Returns count created."""
        BaselineVersionService._assert_draft(baseline)
        if baseline.task_states.exists() and not replace_existing:
            raise BaselineValidationError(
                "Baseline already has task states — use replace_existing=True."
            )
        if replace_existing:
            baseline.task_states.all().delete()

        if mode == PopulationSourceMode.EXPLICIT_DTO:
            if not dtos:
                raise BaselineValidationError("explicit_dto mode requires dtos.")
            rows = cls._rows_from_dtos(baseline, dtos)
        elif mode == PopulationSourceMode.CURRENT_OPERATIONAL:
            rows = cls._rows_from_current_tasks(baseline)
        elif mode == PopulationSourceMode.SOURCE_VERSION:
            rows = cls._rows_from_source_version(baseline)
        else:
            raise BaselineValidationError(f"Unknown population mode: {mode}")

        created = cls._bulk_insert(baseline, rows)
        meta = dict(baseline.metadata or {})
        meta["population_mode"] = mode.value
        meta["population_count"] = created
        baseline.metadata = meta
        baseline.save(update_fields=["metadata", "updated_at"])
        record_baseline_event(
            baseline=baseline,
            event_type=BaselineAuditEvent.EventType.BASELINE_POPULATION_COMPLETED,
            actor=actor,
            previous_status=baseline.status,
            new_status=baseline.status,
            metadata={"mode": mode.value, "count": created},
        )
        logger.info(
            "Populated baseline %s with %s task states (mode=%s)",
            baseline.id,
            created,
            mode.value,
        )
        return created

    @classmethod
    def _bulk_insert(cls, baseline: BaselineVersion, rows: list[BaselineTaskState]) -> int:
        if not rows:
            return 0
        with transaction.atomic():
            BaselineTaskState.objects.bulk_create(rows, batch_size=BULK_BATCH)
        return len(rows)

    @classmethod
    def _rows_from_current_tasks(cls, baseline: BaselineVersion) -> list[BaselineTaskState]:
        project_id = baseline.project_id
        tasks = (
            Task.objects.filter(project_id=project_id)
            .exclude(schedule_activity_id__isnull=True)
            .select_related("schedule_activity")
        )
        rows: list[BaselineTaskState] = []
        for task in tasks:
            duration = None
            if task.start_date and task.end_date:
                duration = (task.end_date - task.start_date).days
            rows.append(
                cls._task_to_state(
                    baseline,
                    task.schedule_activity,
                    task=task,
                    provenance={"mode": PopulationSourceMode.CURRENT_OPERATIONAL.value},
                    duration=duration,
                )
            )
        return rows

    @classmethod
    def _rows_from_source_version(cls, baseline: BaselineVersion) -> list[BaselineTaskState]:
        if baseline.source_version_id is None:
            raise BaselineValidationError("source_version mode requires baseline.source_version.")
        sv_id = baseline.source_version_id
        project_id = baseline.project_id
        tasks = (
            Task.objects.filter(project_id=project_id, source_version_id=sv_id)
            .exclude(schedule_activity_id__isnull=True)
            .select_related("schedule_activity")
        )
        rows: list[BaselineTaskState] = []
        for task in tasks:
            duration = None
            if task.start_date and task.end_date:
                duration = (task.end_date - task.start_date).days
            rows.append(
                cls._task_to_state(
                    baseline,
                    task.schedule_activity,
                    task=task,
                    provenance={
                        "mode": PopulationSourceMode.SOURCE_VERSION.value,
                        "source_version_id": str(sv_id),
                    },
                    duration=duration,
                )
            )
        return rows

    @classmethod
    def _rows_from_dtos(
        cls,
        baseline: BaselineVersion,
        dtos: list[TaskStateDTO],
    ) -> list[BaselineTaskState]:
        project_id = baseline.project_id
        activity_ids = {d.schedule_activity_id for d in dtos}
        activities = {
            str(a.pk): a
            for a in ScheduleActivity.objects.filter(
                project_id=project_id,
                pk__in=activity_ids,
            )
        }
        if len(activities) != len(activity_ids):
            raise BaselineValidationError("One or more schedule activities not found in project.")
        rows: list[BaselineTaskState] = []
        for dto in dtos:
            activity = activities[dto.schedule_activity_id]
            rows.append(
                BaselineTaskState(
                    baseline_version=baseline,
                    schedule_activity=activity,
                    source_task_id=dto.source_task_id,
                    activity_code=dto.activity_code,
                    name_snapshot=dto.name_snapshot,
                    planned_start=dto.planned_start,
                    planned_finish=dto.planned_finish,
                    duration_days=dto.duration_days,
                    calendar_reference=dto.calendar_reference,
                    baseline_cost=dto.baseline_cost,
                    planned_resource_units=dto.planned_resource_units,
                    progress_basis=dto.progress_basis,
                    activity_type=dto.activity_type,
                    source_metadata=dto.source_metadata or {},
                    field_provenance=dto.field_provenance
                    or {"mode": PopulationSourceMode.EXPLICIT_DTO.value},
                )
            )
        return rows

    @staticmethod
    def _task_to_state(
        baseline: BaselineVersion,
        activity: ScheduleActivity,
        *,
        task: Task,
        provenance: dict[str, Any],
        duration: int | None,
    ) -> BaselineTaskState:
        return BaselineTaskState(
            baseline_version=baseline,
            schedule_activity=activity,
            source_task_id=str(task.pk),
            activity_code=task.activity_code or "",
            name_snapshot=task.name,
            planned_start=task.start_date,
            planned_finish=task.end_date,
            duration_days=duration,
            calendar_reference=task.calendar_object_id or "",
            baseline_cost=task.cost,
            planned_resource_units=None,
            progress_basis="",
            activity_type=task.activity_type or "",
            source_metadata={"task_id": str(task.pk)},
            field_provenance=provenance,
        )

    @staticmethod
    def coverage_summary(baseline: BaselineVersion) -> dict[str, Any]:
        """Task and cost coverage for a baseline version."""
        qs = baseline.task_states.all()
        total = qs.count()
        with_dates = qs.filter(
            planned_start__isnull=False,
            planned_finish__isnull=False,
        ).count()
        with_cost = qs.filter(baseline_cost__isnull=False).count()
        return {
            "task_state_count": total,
            "dated_task_count": with_dates,
            "cost_task_count": with_cost,
            "task_coverage_pct": round(100.0 * with_dates / total, 2) if total else None,
            "cost_coverage_pct": round(100.0 * with_cost / total, 2) if total else None,
        }
