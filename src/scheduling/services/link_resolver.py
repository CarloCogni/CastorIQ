# scheduling/services/link_resolver.py
"""Authoritative Task ↔ IFC link resolution via TaskEntityBinding.

TaskEntityBinding is the source of truth for schedule-to-model links.
Task.ifc_entities M2M may lag behind and is not read here.
"""

from __future__ import annotations

from uuid import UUID


def entity_gids_for_task(task_id: str | UUID) -> list[str]:
    """Return entity global_ids bound to *task_id* (binding table only)."""
    from scheduling.models import TaskEntityBinding

    return list(
        TaskEntityBinding.objects.filter(task_id=task_id)
        .order_by("entity_global_id")
        .values_list("entity_global_id", flat=True)
    )


def entity_gids_by_task(
    project_id: str | UUID,
    task_ids: list[str | UUID] | None = None,
) -> dict[str, list[str]]:
    """Return {str(task_id): [entity_global_id, ...]} for *project_id*."""
    from scheduling.models import TaskEntityBinding

    qs = TaskEntityBinding.objects.filter(task__project_id=project_id)
    if task_ids is not None:
        qs = qs.filter(task_id__in=task_ids)
    result: dict[str, list[str]] = {}
    for task_id, gid in qs.values_list("task_id", "entity_global_id"):
        tid = str(task_id)
        result.setdefault(tid, []).append(gid)
    for gids in result.values():
        gids.sort()
    return result


def linked_entity_gids_for_project(project_id: str | UUID) -> set[str]:
    """All IFC entity global_ids with at least one TaskEntityBinding on *project_id*."""
    from scheduling.models import TaskEntityBinding

    return set(
        TaskEntityBinding.objects.filter(task__project_id=project_id).values_list(
            "entity_global_id", flat=True
        )
    )


def task_is_linked(task_id: str | UUID, binding_gids: list[str] | None = None) -> bool:
    """True when *task_id* has one or more bindings."""
    if binding_gids is not None:
        return len(binding_gids) > 0
    from scheduling.models import TaskEntityBinding

    return TaskEntityBinding.objects.filter(task_id=task_id).exists()


def link_status_for_task(task, binding_gids: list[str] | None = None) -> str:
    """Return link display status from TaskEntityBinding review flags.

    Returns one of: non_physical | linked | needs_review | unlinked.
    Trusted links require at least one binding with needs_review=False.
  """
    if task.is_non_physical:
        return "non_physical"
    if binding_gids is not None and not binding_gids:
        return "unlinked"

    from scheduling.models import TaskEntityBinding

    bindings = TaskEntityBinding.objects.filter(task_id=task.pk)
    if not bindings.exists():
        return "unlinked"
    if bindings.filter(needs_review=False).exists():
        return "linked"
    return "needs_review"
