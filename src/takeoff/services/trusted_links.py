# takeoff/services/trusted_links.py
"""Main-compatible trusted schedule–model link reads for Model Readiness.

origin/main TaskEntityBinding uses needs_review=False as the accepted/trusted
signal. This module intentionally does not depend on the package governance
stack (BindingGovernanceReader) so Facilities/writeback/main scheduling stay
untouched during Phase 1.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import Count, QuerySet


def _trusted_qs(project_id: str | UUID) -> QuerySet:
    from scheduling.models import TaskEntityBinding

    return TaskEntityBinding.objects.filter(
        task__project_id=project_id,
        needs_review=False,
    )


def linked_entity_gids_for_project(project_id: str | UUID) -> set[str]:
    """Accepted/trusted entity GlobalIds for the project."""
    return set(_trusted_qs(project_id).values_list("entity_global_id", flat=True).distinct())


def trusted_counts(project_id: str | UUID) -> dict[str, int]:
    """Compact trusted binding counts used by readiness charts."""
    qs = _trusted_qs(project_id)
    return {
        "trusted_bindings": qs.count(),
        "trusted_tasks": qs.values("task_id").distinct().count(),
        "trusted_entities": qs.values("entity_global_id").distinct().count(),
    }


def trusted_task_ids(project_id: str | UUID) -> set[str]:
    """Task IDs that have at least one accepted/trusted binding."""
    return {
        str(tid) for tid in _trusted_qs(project_id).values_list("task_id", flat=True).distinct()
    }


def trusted_fanout_sizes(project_id: str | UUID) -> list[int]:
    """Per-task distinct entity counts for accepted bindings, sorted ascending."""
    rows = (
        _trusted_qs(project_id)
        .values("task_id")
        .annotate(n=Count("entity_global_id", distinct=True))
        .values_list("n", flat=True)
    )
    return sorted(int(n) for n in rows)


def entities_with_multiple_trusted_tasks(project_id: str | UUID) -> dict[str, list[str]]:
    """Map GlobalId → task ids when more than one accepted task links the entity."""
    rows = (
        _trusted_qs(project_id)
        .values("entity_global_id")
        .annotate(n=Count("task_id", distinct=True))
        .filter(n__gt=1)
        .values_list("entity_global_id", flat=True)
    )
    multi: dict[str, list[str]] = {}
    if not rows:
        return multi
    gids = list(rows)
    for gid, task_id in (
        _trusted_qs(project_id)
        .filter(entity_global_id__in=gids)
        .values_list("entity_global_id", "task_id")
        .distinct()
    ):
        multi.setdefault(str(gid), []).append(str(task_id))
    return multi


def link_coverage_for_overview(
    *,
    project_id: str | UUID,
    total_entities: int,
    trusted_gids: set[str] | None = None,
) -> dict[str, Any]:
    """Overview link coverage fields for Model Readiness."""
    gids = trusted_gids if trusted_gids is not None else linked_entity_gids_for_project(project_id)
    linked = len(gids)
    unlinked = max(0, total_entities - linked)
    pct = round(100.0 * linked / total_entities, 1) if total_entities else None
    return {
        "trusted_linked_entities": linked,
        "unlinked_entities": unlinked,
        "link_coverage_pct": pct,
        "has_trusted_links": linked > 0,
    }
