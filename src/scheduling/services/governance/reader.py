# scheduling/services/governance/reader.py
"""Project-scoped trusted and review binding reads (E2-A)."""

from __future__ import annotations

import logging
from collections import defaultdict
from uuid import UUID

from django.db.models import Count, QuerySet

logger = logging.getLogger(__name__)


class BindingGovernanceReader:
    """Read-only governance queries over TaskEntityBinding for one project."""

    def __init__(self, project_id: str | UUID) -> None:
        self.project_id = str(project_id)

    def _scoped_bindings(self) -> QuerySet:
        from scheduling.models import TaskEntityBinding

        return TaskEntityBinding.objects.filter(task__project_id=self.project_id)

    def trusted_bindings_qs(self) -> QuerySet:
        """Accepted bindings only (needs_review=False)."""
        return self._scoped_bindings().filter(needs_review=False)

    def review_bindings_qs(self) -> QuerySet:
        """Review-only bindings (needs_review=True)."""
        return self._scoped_bindings().filter(needs_review=True)

    def trusted_entity_gids(self, *, ifc_scope: bool = False) -> set[str]:
        """Distinct entity GlobalIds with at least one accepted binding."""
        gids = set(
            self.trusted_bindings_qs()
            .order_by("entity_global_id")
            .values_list("entity_global_id", flat=True)
        )
        if ifc_scope:
            gids &= self._project_ifc_entity_gids()
        return gids

    def _project_ifc_entity_gids(self) -> set[str]:
        """GlobalIds present on completed IFC files for this project."""
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        return set(
            IFCEntity.objects.filter(ifc_file__in=ifc_files).values_list(
                "global_id",
                flat=True,
            )
        )

    def review_entity_gids(self) -> set[str]:
        """Distinct entity GlobalIds with at least one review binding."""
        return set(
            self.review_bindings_qs()
            .order_by("entity_global_id")
            .values_list("entity_global_id", flat=True)
        )

    def trusted_task_ids(self) -> set[str]:
        """Distinct task IDs with at least one accepted binding."""
        return {
            str(pk)
            for pk in self.trusted_bindings_qs()
            .order_by("task_id")
            .values_list("task_id", flat=True)
            .distinct()
        }

    def trusted_entity_gids_for_task(self, task_id: str | UUID) -> list[str]:
        """Accepted entity GlobalIds for one task, sorted deterministically."""
        return list(
            self.trusted_bindings_qs()
            .filter(task_id=task_id)
            .order_by("entity_global_id")
            .values_list("entity_global_id", flat=True)
        )

    def review_entity_gids_for_task(self, task_id: str | UUID) -> list[str]:
        """Review entity GlobalIds for one task, sorted deterministically."""
        return list(
            self.review_bindings_qs()
            .filter(task_id=task_id)
            .order_by("entity_global_id")
            .values_list("entity_global_id", flat=True)
        )

    def entity_gids_by_task(
        self,
        task_ids: list[str | UUID] | None = None,
        *,
        trusted_only: bool = False,
        review_only: bool = False,
    ) -> dict[str, list[str]]:
        """Return {task_id: [entity_global_id, ...]} for scoped tasks."""
        if trusted_only and review_only:
            return {}
        qs = self._scoped_bindings()
        if trusted_only:
            qs = qs.filter(needs_review=False)
        elif review_only:
            qs = qs.filter(needs_review=True)
        if task_ids is not None:
            qs = qs.filter(task_id__in=task_ids)
        result: dict[str, list[str]] = {}
        for task_id, gid in qs.values_list("task_id", "entity_global_id").order_by(
            "task_id", "entity_global_id"
        ):
            result.setdefault(str(task_id), []).append(gid)
        return result

    def trusted_counts(self) -> dict[str, int]:
        """Aggregate accepted binding counts for summary dashboards."""
        trusted = self.trusted_bindings_qs()
        return {
            "trusted_bindings": trusted.count(),
            "trusted_tasks": trusted.values("task_id").distinct().count(),
            "trusted_entities": trusted.values("entity_global_id").distinct().count(),
            "review_bindings": self.review_bindings_qs().count(),
            "review_tasks": self.review_bindings_qs().values("task_id").distinct().count(),
        }

    def method_distribution(
        self,
        *,
        trusted_only: bool = True,
        review_only: bool = False,
    ) -> dict[str, int]:
        """Count bindings grouped by link_method."""
        if trusted_only:
            qs = self.trusted_bindings_qs()
        elif review_only:
            qs = self.review_bindings_qs()
        else:
            qs = self._scoped_bindings()
        rows = qs.values("link_method").annotate(c=Count("id")).order_by("link_method")
        return {row["link_method"]: row["c"] for row in rows}

    def entities_with_multiple_trusted_tasks(self) -> dict[str, list[str]]:
        """Map entity GlobalId → sorted trusted task IDs when count > 1."""
        grouped: dict[str, list[str]] = defaultdict(list)
        for gid, task_id in (
            self.trusted_bindings_qs()
            .values_list("entity_global_id", "task_id")
            .order_by("entity_global_id", "task_id")
        ):
            grouped[gid].append(str(task_id))
        return {gid: tids for gid, tids in grouped.items() if len(tids) > 1}

    def trusted_tasks_for_entity(self, entity_global_id: str) -> list[str]:
        """Accepted task IDs linked to one entity GlobalId."""
        return [
            str(pk)
            for pk in self.trusted_bindings_qs()
            .filter(entity_global_id=entity_global_id)
            .order_by("task_id")
            .values_list("task_id", flat=True)
        ]

    def review_tasks_for_entity(self, entity_global_id: str) -> list[str]:
        """Review task IDs linked to one entity GlobalId."""
        return [
            str(pk)
            for pk in self.review_bindings_qs()
            .filter(entity_global_id=entity_global_id)
            .order_by("task_id")
            .values_list("task_id", flat=True)
        ]

    def legacy_m2m_only_relation_count(
        self,
        trusted_pairs: set[tuple[str, str]] | None = None,
    ) -> int:
        """M2M task↔entity pairs with no accepted binding for the same GlobalId."""
        if trusted_pairs is not None:
            return self._legacy_m2m_only_from_pairs(trusted_pairs)

        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM castor_scheduling_task_ifc_entities te
                INNER JOIN castor_scheduling_task t ON t.id = te.task_id
                INNER JOIN ifc_processor_ifcentity e ON e.id = te.ifcentity_id
                LEFT JOIN castor_scheduling_taskentitybinding b
                    ON b.task_id = te.task_id
                    AND b.entity_global_id = e.global_id
                    AND b.needs_review = FALSE
                WHERE t.project_id = %s AND b.id IS NULL
                """,
                [self.project_id],
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def _legacy_m2m_only_from_pairs(self, trusted_pairs: set[tuple[str, str]]) -> int:
        from scheduling.models import Task

        count = 0
        tasks = Task.objects.filter(project_id=self.project_id).prefetch_related("ifc_entities")
        for task in tasks:
            for entity in task.ifc_entities.all():
                pair = (str(task.pk), entity.global_id)
                if pair not in trusted_pairs:
                    count += 1
        return count

    def property_hint_entity_count(
        self,
        trusted_gids: set[str] | None = None,
    ) -> int | None:
        """Entities with Activity ID property but no accepted binding (project IFC scope)."""
        try:
            from ifc_processor.models import IFCEntity, IFCFile
        except ImportError:
            return None

        if trusted_gids is None:
            trusted_gids = self.trusted_entity_gids()
        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        entity_qs = IFCEntity.objects.filter(ifc_file__in=ifc_files)
        total_entities = entity_qs.count()
        if total_entities and len(trusted_gids) >= total_entities:
            return 0

        hints = 0
        for entity in (
            entity_qs.exclude(global_id__in=trusted_gids)
            .only("global_id", "properties")
            .iterator(chunk_size=500)
        ):
            props = entity.properties or {}
            if _has_activity_id_property(props):
                hints += 1
        return hints


def _has_activity_id_property(props: dict) -> bool:
    for key, value in props.items():
        if value and key.lower().endswith("activity id"):
            return True
    return False
