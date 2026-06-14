# scheduling/services/source_version/import_persistence.py
"""Shared schedule import persistence — task/dependency writes for provenance coordinator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Count

from environments.models import Project
from scheduling.models import ScheduleSource, Task, TaskDependency
from scheduling.services.autolink import autodetect_stages
from scheduling.services.column_mapper import parse_predecessor_string
from scheduling.services.p6_save import finalise_p6_data
from scheduling.services.pct_normalize import normalize_pct_complete
from scheduling.services.source_version.activity_identity import ScheduleActivityIdentityService
from scheduling.services.source_version.failure_hooks import maybe_raise

logger = logging.getLogger(__name__)


def _resolve_import_phys_pct(task_data: dict) -> float | None:
    """Normalize physical progress from import dict keys."""
    for key in ("_p6_phys_pct", "_csv_pct_complete"):
        normalized = normalize_pct_complete(task_data.get(key))
        if normalized is not None:
            return normalized
    return None


@dataclass
class ImportPersistResult:
    """Outcome of task/dependency persistence within an import transaction."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_count: int = 0
    cleaned: int = 0
    dep_count: int = 0
    touched_pks: list[str] = field(default_factory=list)
    touched_task_data: list[dict[str, Any]] = field(default_factory=list)
    current_source: ScheduleSource | None = None
    p6_obj_id_map: dict[str, str] = field(default_factory=dict)


def persist_schedule_import(
    project,
    *,
    tasks_data: list[dict[str, Any]],
    raw_deps: list[dict[str, Any]],
    replace_mode: bool,
    filename: str,
    source_format: str,
    data_date: date | None,
) -> ImportPersistResult:
    """Persist imported tasks and dependencies — caller owns transaction boundary."""
    result = ImportPersistResult()

    if replace_mode:
        Project.objects.select_for_update().get(pk=project.pk)
        existing = Task.objects.filter(project=project).count()
        Task.objects.filter(project=project).delete()
        logger.info("Replace mode: cleared %d tasks for project %s", existing, project.pk)
        maybe_raise("after_replace_delete")

    existing_by_code: dict[str, Task] = {}
    existing_by_name_date: dict[tuple, Task] = {}
    if not replace_mode:
        for t in Task.objects.filter(project=project).only(
            "pk", "activity_code", "name", "start_date", "end_date"
        ):
            if t.activity_code:
                existing_by_code[t.activity_code] = t
            existing_by_name_date[(t.name, str(t.start_date))] = t

        dup_codes = list(
            Task.objects.filter(project=project)
            .exclude(activity_code="")
            .values("activity_code")
            .annotate(cnt=Count("pk"))
            .filter(cnt__gt=1)
            .values_list("activity_code", flat=True)
        )
        for code in dup_codes:
            tasks_for_code = list(
                Task.objects.filter(project=project, activity_code=code).order_by(
                    "start_date", "name"
                )
            )
            to_delete = [t.pk for t in tasks_for_code[1:]]
            Task.objects.filter(pk__in=to_delete).delete()
            result.cleaned += len(to_delete)
        if result.cleaned:
            logger.info("Cleaned %d duplicate tasks for project %s", result.cleaned, project.pk)

    xer_id_map: dict[str, str] = {}
    msp_uid_map: dict[str, str] = {}
    p6_obj_id_map: dict[str, str] = {}
    activity_code_map: dict[str, str] = {}
    tasks_with_preds: list[tuple[str, str]] = []

    for td in tasks_data:
        try:
            cost_str = td.get("cost") or td.get("budgeted_cost")
            actual_start_raw = td.get("actual_start")
            actual_end_raw = td.get("actual_end")
            early_start_raw = td.get("early_start")
            early_finish_raw = td.get("early_finish")
            late_start_raw = td.get("late_start")
            late_finish_raw = td.get("late_finish")
            total_float_val = td.get("total_float_days")
            activity_code = td.get("activity_code", "")

            task_fields = dict(
                name=td["name"],
                description=td.get("description", ""),
                start_date=date.fromisoformat(td["start_date"]),
                end_date=date.fromisoformat(td["end_date"]),
                actual_start=date.fromisoformat(actual_start_raw) if actual_start_raw else None,
                actual_end=date.fromisoformat(actual_end_raw) if actual_end_raw else None,
                status=td.get("status", "planned"),
                source=td.get("source", "excel"),
                activity_code=activity_code,
                color=td.get("color", "#3b82f6"),
                cost=Decimal(cost_str) if cost_str else None,
                activity_type=td.get("activity_type", ""),
                stage=td.get("stage", ""),
                sub_stage=td.get("sub_stage", ""),
                early_start=date.fromisoformat(early_start_raw) if early_start_raw else None,
                early_finish=date.fromisoformat(early_finish_raw) if early_finish_raw else None,
                late_start=date.fromisoformat(late_start_raw) if late_start_raw else None,
                late_finish=date.fromisoformat(late_finish_raw) if late_finish_raw else None,
                total_float=int(total_float_val) if total_float_val is not None else None,
                is_critical=total_float_val is not None and int(total_float_val) <= 0,
                calendar_object_id=td.get("calendar_object_id", ""),
                constraint_type=td.get("constraint_type", ""),
                constraint_date=date.fromisoformat(td["constraint_date"])
                if td.get("constraint_date")
                else None,
                physical_percent_complete=_resolve_import_phys_pct(td),
                duration_percent_complete=normalize_pct_complete(td.get("_p6_dur_pct")),
            )

            existing = None
            if activity_code and activity_code in existing_by_code:
                existing = existing_by_code[activity_code]
            else:
                existing = existing_by_name_date.get(
                    (task_fields["name"], str(task_fields["start_date"]))
                )

            if existing is not None:
                dirty = [f for f, v in task_fields.items() if getattr(existing, f) != v]
                if dirty:
                    for f in dirty:
                        setattr(existing, f, task_fields[f])
                    existing.save(update_fields=dirty)
                    result.updated += 1
                    result.touched_pks.append(str(existing.pk))
                    result.touched_task_data.append(td)
                else:
                    result.unchanged += 1
                task = existing
            else:
                task = Task.objects.create(project=project, **task_fields)
                maybe_raise("during_task_create")
                result.created += 1
                result.touched_pks.append(str(task.pk))
                result.touched_task_data.append(td)
                if task.source == Task.Source.MANUAL and not task.schedule_activity_id:
                    identity = ScheduleActivityIdentityService(project).create_for_manual_task(task)
                    if identity.activity_id:
                        task.schedule_activity_id = identity.activity_id
                        task.save(update_fields=["schedule_activity"])

            pk = str(task.pk)
            if td.get("_xer_task_id"):
                xer_id_map[td["_xer_task_id"]] = pk
            if td.get("_msp_uid"):
                msp_uid_map[td["_msp_uid"]] = pk
            if td.get("_p6_obj_id"):
                p6_obj_id_map[td["_p6_obj_id"]] = pk
            if activity_code:
                activity_code_map[activity_code] = pk
            raw_preds = td.get("_raw_predecessors", "").strip()
            if raw_preds:
                tasks_with_preds.append((pk, raw_preds))
        except (ValueError, KeyError, TypeError) as exc:
            result.skipped_count += 1
            logger.warning("Skipping task row: %s", exc)

    dep_objects: list[TaskDependency] = []
    dep_set: set[tuple] = set()

    def _add(pred_pk: str, succ_pk: str, dep_type: str, lag_days: int) -> None:
        key = (pred_pk, succ_pk, dep_type)
        if key in dep_set or pred_pk == succ_pk:
            return
        dep_set.add(key)
        dep_objects.append(
            TaskDependency(
                predecessor_id=pred_pk,
                successor_id=succ_pk,
                dep_type=dep_type,
                lag_days=lag_days,
            )
        )

    for d in raw_deps:
        if "pred_xer_id" in d:
            pred_pk = xer_id_map.get(d["pred_xer_id"])
            succ_pk = xer_id_map.get(d["succ_xer_id"])
        elif "pred_uid" in d:
            pred_pk = msp_uid_map.get(d["pred_uid"])
            succ_pk = msp_uid_map.get(d["succ_uid"])
        elif "pred_p6_obj_id" in d:
            pred_pk = p6_obj_id_map.get(d["pred_p6_obj_id"])
            succ_pk = p6_obj_id_map.get(d["succ_p6_obj_id"])
        else:
            continue
        if pred_pk and succ_pk:
            _add(pred_pk, succ_pk, d.get("dep_type", "FS"), d.get("lag_days", 0))

    for task_pk, raw_preds in tasks_with_preds:
        for ref in parse_predecessor_string(raw_preds):
            pred_pk = activity_code_map.get(ref["activity_code"])
            if pred_pk:
                _add(pred_pk, task_pk, ref["dep_type"], ref["lag_days"])

    if dep_objects:
        TaskDependency.objects.filter(predecessor__project=project).delete()
        TaskDependency.objects.bulk_create(dep_objects, ignore_conflicts=True)
        result.dep_count = len(dep_objects)
        logger.info("Dependencies saved: %d for project %s", result.dep_count, project.pk)
        maybe_raise("after_dependency_persist")

    all_tasks = list(Task.objects.filter(project=project).only("pk", "name", "stage", "sub_stage"))
    autodetect_stages([t for t in all_tasks if not t.stage])

    current_source = ScheduleSource.objects.create(
        project=project,
        filename=filename,
        source_format=source_format,
        task_count=result.created + result.updated + result.unchanged,
        data_date=data_date,
    )
    maybe_raise("after_schedule_source_create")
    if result.touched_pks:
        Task.objects.filter(pk__in=result.touched_pks).update(schedule_source=current_source)
    if source_format == "p6xml" and p6_obj_id_map:
        finalise_p6_data(project, current_source, p6_obj_id_map)

    result.current_source = current_source
    result.p6_obj_id_map = p6_obj_id_map
    return result
