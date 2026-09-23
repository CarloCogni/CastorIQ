# scheduling/services/executive_controls/wbs_drilldown.py
"""WBS node drilldowns — tasks, delays, model scope (DF-C3)."""

from __future__ import annotations

import logging
from typing import Any

from django.shortcuts import get_object_or_404
from django.urls import reverse

from scheduling.models import WBSNode
from scheduling.services.executive_controls.matrix_filters import (
    MAX_PAGE_SIZE,
    ExecutiveMatrixFilters,
)
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.scope_classification import ScopeClassificationResolver
from scheduling.services.executive_controls.wbs_analytics_session import (
    UNASSIGNED_KEY,
    WBSAnalyticsSession,
)

logger = logging.getLogger(__name__)


class WBSDrilldownService:
    """Read-only drilldowns for canonical WBS nodes."""

    def __init__(self, project, session: WBSAnalyticsSession) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.session = session
        self._scope = ScopeClassificationResolver()

    def node_detail(self, node_pk: str, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        if node_pk == UNASSIGNED_KEY:
            return self._unassigned_detail(filters)
        node = get_object_or_404(
            WBSNode,
            pk=node_pk,
            wbs_version=self.session.wbs_version,
            wbs_version__project_id=self.project_id,
        )
        aggregation = filters.aggregation_mode or "rolled_up"
        scope = self.session.rollup_task_ids_by_node.get(str(node.pk), set())
        if aggregation == "direct":
            scope = self.session.direct_task_ids_by_node.get(str(node.pk), set())
        tasks = self.session.tasks_for_scope(scope)
        return {
            "section": "wbs_node",
            "methodology_version": E8_METHODOLOGY_VERSION,
            "hierarchy": self.session.hierarchy.to_dict(),
            "aggregation_mode": aggregation,
            "node": self._serialize_node(node),
            "metrics": self._metrics_for_scope(scope, tasks, filters),
            "filters": filters.to_query(),
        }

    def task_list(self, node_pk: str, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        if node_pk == UNASSIGNED_KEY:
            scope = set(self.session.unassigned_task_ids)
        else:
            get_object_or_404(
                WBSNode,
                pk=node_pk,
                wbs_version=self.session.wbs_version,
                wbs_version__project_id=self.project_id,
            )
            aggregation = filters.aggregation_mode or "rolled_up"
            scope = self.session.rollup_task_ids_by_node.get(node_pk, set())
            if aggregation == "direct":
                scope = self.session.direct_task_ids_by_node.get(node_pk, set())

        tasks = self.session.tasks_for_scope(scope)
        if filters.status:
            tasks = [t for t in tasks if t.status == filters.status]
        tasks.sort(key=lambda t: (t.activity_code or t.name or "").lower())

        total = len(tasks)
        page_size = min(MAX_PAGE_SIZE, max(1, filters.page_size))
        start = (filters.page - 1) * page_size
        page_tasks = tasks[start : start + page_size]

        rows = []
        for task in page_tasks:
            tid = str(task.pk)
            delay = self.session.classifier.classify_task(  # type: ignore[union-attr]
                task,
                trusted_entity_count=len(self.session.entities_by_task.get(tid, [])),
            )
            rows.append(
                {
                    "task_id": tid,
                    "activity_code": task.activity_code or "",
                    "name": task.name,
                    "status": task.status,
                    "primary_delay_state": delay.primary_delay_type,
                    "wbs_node_id": str(task.wbs_node_id) if task.wbs_node_id else None,
                    "trusted_entity_count": len(self.session.entities_by_task.get(tid, [])),
                    "links": {
                        "gantt": reverse("scheduling:schedule", kwargs={"pk": self.project_id})
                        + f"?highlight={tid}",
                    },
                }
            )

        return {
            "section": "wbs_tasks",
            "wbs_node_id": node_pk,
            "rows": rows,
            "pagination": {
                "page": filters.page,
                "page_size": page_size,
                "total": total,
                "pages": max(1, (total + page_size - 1) // page_size),
            },
        }

    def model_scope(self, node_pk: str, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        if node_pk == UNASSIGNED_KEY:
            scope = set(self.session.unassigned_task_ids)
        else:
            get_object_or_404(
                WBSNode,
                pk=node_pk,
                wbs_version=self.session.wbs_version,
                wbs_version__project_id=self.project_id,
            )
            scope = self.session.rollup_task_ids_by_node.get(node_pk, set())
        entity_gids = sorted(self.session.trusted_entity_gids_unique(scope))
        review_count = sum(len(self.session.review_entities_by_task.get(tid, [])) for tid in scope)
        return {
            "section": "wbs_model_scope",
            "wbs_node_id": node_pk,
            "trusted_entity_gids": entity_gids[:500],
            "trusted_entity_count": len(entity_gids),
            "review_binding_count": review_count,
            "trusted_task_count": sum(1 for tid in scope if tid in self.session.trusted_task_ids),
            "caveats": [
                "Trusted bindings only — review and legacy M2M excluded.",
                "Entity list capped at 500 GlobalIds per response.",
            ],
        }

    def _unassigned_detail(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        scope = set(self.session.unassigned_task_ids)
        tasks = self.session.tasks_for_scope(scope)
        return {
            "section": "wbs_node",
            "hierarchy": self.session.hierarchy.to_dict(),
            "node": {
                "id": UNASSIGNED_KEY,
                "name": "Unassigned",
                "virtual": True,
            },
            "metrics": self._metrics_for_scope(scope, tasks, filters),
            "filters": filters.to_query(),
        }

    def _metrics_for_scope(
        self, scope: set[str], tasks: list, filters: ExecutiveMatrixFilters
    ) -> dict:
        progress = self.session.progress.aggregate(  # type: ignore[union-attr]
            tasks,
            weighting_mode=filters.weighting_mode,
        )
        return {
            "task_count": len(tasks),
            "progress": progress,
            "evm": self.session.aggregate_evm(scope),
            "trusted_entities": len(self.session.trusted_entity_gids_unique(scope)),
        }

    @staticmethod
    def _serialize_node(node: WBSNode) -> dict[str, Any]:
        return {
            "id": str(node.pk),
            "external_id": node.external_id or None,
            "code": node.code or None,
            "name": node.name,
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "depth": node.depth,
            "path": node.path,
        }
