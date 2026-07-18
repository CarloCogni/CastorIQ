# scheduling/services/resource_foundation.py
"""DF-E1 Resource Foundation helpers — schema readiness and DF-E3 AC reads.

``sum_actual_cost_by_task`` supports DF-E2/E3 readiness checks. Live EVM AC
loading prefers canonical ResourceAssignment via ``evm._load_actual_costs``
(DF-E3), with P6 fallback when canonical rows are absent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import QuerySet, Sum

from scheduling.resource_foundation_models import Resource, ResourceAssignment

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResourceAssignmentIdentity:
    """Stable source identity for a resource assignment row."""

    project_id: str
    source_system: str
    external_id: str
    source_version_id: str | None = None


def validate_assignment_project_consistency(assignment: ResourceAssignment) -> None:
    """Raise ValidationError when task/resource/project FKs disagree."""
    assignment.clean()


def assignments_for_tasks(
    project_id: str | UUID,
    task_ids: Iterable[str | UUID],
    *,
    include_pending: bool = False,
) -> QuerySet[ResourceAssignment]:
    """Return canonical assignments for the given project tasks."""
    ids = [str(tid) for tid in task_ids]
    qs = ResourceAssignment.objects.filter(project_id=project_id, task_id__in=ids)
    if not include_pending:
        qs = qs.filter(is_pending=False)
    return qs.select_related("resource", "task")


def sum_actual_cost_by_task(
    project_id: str | UUID,
    task_ids: Iterable[str | UUID],
    *,
    include_pending: bool = False,
) -> dict[str, Decimal]:
    """Sum non-NULL actual_cost per task. NULL rows are ignored (not treated as zero)."""
    qs = assignments_for_tasks(project_id, task_ids, include_pending=include_pending).filter(
        actual_cost__isnull=False
    )
    rows = qs.values("task_id").annotate(total=Sum("actual_cost"))
    return {str(row["task_id"]): row["total"] or Decimal("0") for row in rows}


def sum_planned_cost_by_task(
    project_id: str | UUID,
    task_ids: Iterable[str | UUID],
    *,
    include_pending: bool = False,
) -> dict[str, Decimal]:
    """Sum non-NULL planned_cost per task. NULL rows are ignored (not treated as zero)."""
    qs = assignments_for_tasks(project_id, task_ids, include_pending=include_pending).filter(
        planned_cost__isnull=False
    )
    rows = qs.values("task_id").annotate(total=Sum("planned_cost"))
    return {str(row["task_id"]): row["total"] or Decimal("0") for row in rows}


def create_resource(
    *,
    project,
    name: str,
    resource_type: str = Resource.ResourceType.UNKNOWN,
    resource_code: str = "",
    status: str = Resource.Status.ACTIVE,
    **kwargs,
) -> Resource:
    """Create a Resource row (thin helper for tests / future importers)."""
    return Resource.objects.create(
        project=project,
        name=name,
        resource_type=resource_type,
        resource_code=resource_code or "",
        status=status,
        **kwargs,
    )


def create_resource_assignment(
    *,
    project,
    task,
    resource: Resource,
    validate: bool = True,
    **kwargs,
) -> ResourceAssignment:
    """Create a ResourceAssignment with optional clean() validation."""
    if resource.project_id != project.pk:
        raise ValidationError(
            {"resource": "Resource project must match ResourceAssignment.project."}
        )
    if task.project_id != project.pk:
        raise ValidationError({"task": "Task project must match ResourceAssignment.project."})
    assignment = ResourceAssignment(
        project=project,
        task=task,
        resource=resource,
        **kwargs,
    )
    if validate:
        assignment.full_clean()
    assignment.save()
    return assignment
