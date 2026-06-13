# scheduling/services/link_resolver.py
"""Authoritative Task ↔ IFC link resolution via TaskEntityBinding.

TaskEntityBinding is the source of truth for schedule-to-model links.
Task.ifc_entities M2M may lag behind and is not read here.

Trusted (accepted) reads require needs_review=False — see BindingGovernanceReader.
"""

from __future__ import annotations

from uuid import UUID


def entity_gids_for_task(
    task_id: str | UUID,
    *,
    trusted_only: bool = False,
) -> list[str]:
    """Return entity global_ids bound to *task_id*.

    When *trusted_only* is True, review bindings are excluded.
    """
    from scheduling.models import TaskEntityBinding

    qs = TaskEntityBinding.objects.filter(task_id=task_id)
    if trusted_only:
        qs = qs.filter(needs_review=False)
    return list(qs.order_by("entity_global_id").values_list("entity_global_id", flat=True))


def entity_gids_by_task(
    project_id: str | UUID,
    task_ids: list[str | UUID] | None = None,
    *,
    accepted_only: bool = False,
    review_only: bool = False,
) -> dict[str, list[str]]:
    """Return {str(task_id): [entity_global_id, ...]} for *project_id*.

    When *accepted_only* is True, review-only bindings (needs_review=True) are excluded.
    When *review_only* is True, only review bindings are returned.
    """
    from scheduling.services.governance.reader import BindingGovernanceReader

    reader = BindingGovernanceReader(project_id)
    return reader.entity_gids_by_task(
        task_ids,
        trusted_only=accepted_only,
        review_only=review_only,
    )


def linked_entity_gids_for_project(project_id: str | UUID) -> set[str]:
    """Trusted entity global_ids with accepted binding, scoped to project IFC files."""
    from scheduling.services.governance.reader import BindingGovernanceReader

    return BindingGovernanceReader(project_id).trusted_entity_gids(ifc_scope=True)


def trusted_entity_gids_for_project(project_id: str | UUID) -> set[str]:
    """Alias for accepted-only project entity GlobalIds."""
    return linked_entity_gids_for_project(project_id)


def task_is_linked(
    task_id: str | UUID,
    binding_gids: list[str] | None = None,
    *,
    trusted_only: bool = False,
) -> bool:
    """True when *task_id* has one or more bindings (or trusted bindings when flagged)."""
    if binding_gids is not None:
        return len(binding_gids) > 0
    from scheduling.models import TaskEntityBinding

    qs = TaskEntityBinding.objects.filter(task_id=task_id)
    if trusted_only:
        qs = qs.filter(needs_review=False)
    return qs.exists()


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
