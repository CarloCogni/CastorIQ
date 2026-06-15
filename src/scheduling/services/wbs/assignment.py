# scheduling/services/wbs/assignment.py
"""Task-to-canonical-WBS assignment — nullable-first, no name guessing (DF-C1)."""

from __future__ import annotations

import logging
from uuid import UUID

from django.db import transaction

from scheduling.models import Task, WBSNode, WBSVersion
from scheduling.services.wbs.exceptions import WBSValidationError

logger = logging.getLogger(__name__)


class TaskWBSAssignmentService:
    """Assign or clear canonical WBS nodes on Tasks."""

    @staticmethod
    def _validate_node_for_task(task: Task, node: WBSNode) -> None:
        if node.wbs_version.project_id != task.project_id:
            raise WBSValidationError("WBS node must belong to the same project as the task.")
        version = node.wbs_version
        if version.status not in {WBSVersion.Status.ACTIVE, WBSVersion.Status.DRAFT}:
            raise WBSValidationError("Tasks can only be assigned to draft or active WBS versions.")
        if task.source_version_id and version.source_version_id:
            if task.source_version_id != version.source_version_id:
                raise WBSValidationError(
                    "Task source version is incompatible with WBS version source version."
                )

    @classmethod
    def assign(cls, task: Task, node: WBSNode) -> Task:
        """Assign one task to a compatible WBS node."""
        cls._validate_node_for_task(task, node)
        task.wbs_node = node
        task.save(update_fields=["wbs_node", "updated_at"])
        return task

    @classmethod
    @transaction.atomic
    def bulk_assign(cls, assignments: list[tuple[Task, WBSNode]]) -> int:
        """Bulk assign tasks; returns count updated."""
        if not assignments:
            return 0
        for task, node in assignments:
            cls._validate_node_for_task(task, node)
            task.wbs_node = node
        Task.objects.bulk_update(
            [t for t, _ in assignments],
            ["wbs_node", "updated_at"],
            batch_size=500,
        )
        return len(assignments)

    @classmethod
    def clear(cls, task: Task) -> Task:
        """Remove WBS assignment from a task."""
        task.wbs_node = None
        task.save(update_fields=["wbs_node", "updated_at"])
        return task

    @classmethod
    @transaction.atomic
    def bulk_clear(cls, task_ids: list[UUID]) -> int:
        """Clear WBS assignment for multiple tasks."""
        return Task.objects.filter(pk__in=task_ids).update(wbs_node=None)
