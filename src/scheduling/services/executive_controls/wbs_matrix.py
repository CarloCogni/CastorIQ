# scheduling/services/executive_controls/wbs_matrix.py
"""Canonical WBS matrix aggregation for E8 (DF-C3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse

from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.performance_cube import DELAYED_PRIMARY
from scheduling.services.executive_controls.wbs_analytics_session import (
    UNASSIGNED_KEY,
    UNASSIGNED_LABEL,
    WBSAnalyticsSession,
)

logger = logging.getLogger(__name__)

WBS_SCHEMA_VERSION = "df-c3-wbs-matrix-v1"


class WBSMatrixService:
    """Build canonical WBS matrix rows from a shared analytics session."""

    def __init__(self, project, session: WBSAnalyticsSession) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.session = session

    def build_snapshot_unavailable(self, *, reason: str) -> dict[str, Any]:
        """Truthful payload when snapshot mode cannot serve live WBS node metrics."""
        return {
            "section": "matrix_rows",
            "schema_version": WBS_SCHEMA_VERSION,
            "hierarchy": self.session.hierarchy.to_dict(),
            "aggregation_mode": "rolled_up",
            "rows": [],
            "summary": {"filtered_task_count": 0, "group_count": 0},
            "warnings": [
                reason,
                "Persisted snapshot WBS node analytics require a future schema extension.",
            ],
            "unavailable_reason": reason,
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_rows(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        """Paginated WBS matrix rows at one tree level."""
        parent_id = filters.wbs_parent_id
        aggregation = filters.aggregation_mode or "rolled_up"
        child_nodes = self.session.children_by_parent.get(parent_id, [])
        if parent_id is None:
            child_nodes = self.session.children_by_parent.get(None, [])

        virtual_unassigned = (
            parent_id is None
            and self.session.unassigned_task_ids
            and not filters.wbs_hide_unassigned
        )

        rows: list[dict[str, Any]] = []
        for node in child_nodes:
            rows.append(self._row_for_node(node, filters=filters, aggregation=aggregation))

        if virtual_unassigned:
            rows.append(self._row_unassigned(filters=filters, aggregation=aggregation))

        rows = self._sort_rows(rows, filters)
        total = len(rows)
        start = (filters.page - 1) * filters.page_size
        page_rows = rows[start : start + filters.page_size]

        filtered_tasks = self._filtered_task_ids(filters)
        return {
            "section": "matrix_rows",
            "schema_version": WBS_SCHEMA_VERSION,
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "hierarchy": self.session.hierarchy.to_dict(),
            "aggregation_mode": aggregation,
            "selected_dimension": {
                "dimension_id": "wbs",
                "label": "Canonical WBS",
                "authority": MetricAuthority.AUTHORITATIVE.value,
            },
            "filters": filters.to_query(),
            "summary": {
                "filtered_task_count": len(filtered_tasks),
                "group_count": total,
                "assigned_task_count": self.session.hierarchy.assigned_task_count,
                "unassigned_task_count": self.session.hierarchy.unassigned_task_count,
                "reconciliation": self._reconciliation(),
            },
            "rows": page_rows,
            "pagination": {
                "page": filters.page,
                "page_size": filters.page_size,
                "total": total,
                "pages": max(1, (total + filters.page_size - 1) // filters.page_size),
            },
            "warnings": list(self.session.hierarchy.caveats)
            + [
                "Rolled-up totals deduplicate tasks across descendant nodes.",
                "Trusted entity rollups deduplicate GlobalIds across tasks.",
            ],
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def _task_scope(self, node_id: str, aggregation: str) -> set[str]:
        if aggregation == "direct":
            return set(self.session.direct_task_ids_by_node.get(node_id, set()))
        return set(self.session.rollup_task_ids_by_node.get(node_id, set()))

    def _row_for_node(
        self,
        node,
        *,
        filters: ExecutiveMatrixFilters,
        aggregation: str,
    ) -> dict[str, Any]:
        nid = str(node.pk)
        scope = self._task_scope(nid, aggregation)
        scope &= self._filtered_task_ids(filters)
        tasks = self.session.tasks_for_scope(scope)
        direct_count = len(self.session.direct_task_ids_by_node.get(nid, set()) & scope)
        child_count = len(self.session.children_by_parent.get(nid, []))

        delay_counts: dict[str, int] = {}
        primary_late = 0
        critical = 0
        budget_sum = 0.0
        budget_tasks = 0
        trusted_tasks = 0
        delayed_trusted = 0
        for task in tasks:
            tid = str(task.pk)
            delay = self.session.classifier.classify_task(  # type: ignore[union-attr]
                task,
                trusted_entity_count=len(self.session.entities_by_task.get(tid, [])),
            )
            primary = delay.primary_delay_type
            delay_counts[primary] = delay_counts.get(primary, 0) + 1
            if primary in DELAYED_PRIMARY:
                primary_late += 1
            if task.is_critical:
                critical += 1
            if task.cost and float(task.cost) > 0:
                budget_sum += float(task.cost)
                budget_tasks += 1
            if tid in self.session.trusted_task_ids:
                trusted_tasks += 1
                if primary in DELAYED_PRIMARY:
                    delayed_trusted += 1

        progress = self.session.progress.aggregate(  # type: ignore[union-attr]
            tasks,
            weighting_mode=filters.weighting_mode,
        )
        evm = self.session.aggregate_evm({str(t.pk) for t in tasks})
        unique_entities = self.session.trusted_entity_gids_unique({str(t.pk) for t in tasks})

        expand_params = filters.to_query()
        expand_params["wbs_parent_id"] = nid
        expand_url = ""
        if child_count:
            expand_url = (
                reverse("scheduling:executive_controls_matrix_rows", kwargs={"pk": self.project_id})
                + "?"
                + urlencode(expand_params)
            )

        activity_params = filters.to_query()
        activity_params["wbs_node_id"] = nid
        activity_params["group_key"] = nid
        activity_params["dimension"] = "wbs"
        activity_drilldown = (
            reverse(
                "scheduling:executive_controls_wbs_tasks",
                kwargs={"pk": self.project_id, "node_pk": nid},
            )
            + "?"
            + urlencode(activity_params)
        )

        return {
            "dimension": "wbs",
            "key": nid,
            "label": node.name,
            "code": node.code or "",
            "authority": MetricAuthority.AUTHORITATIVE.value,
            "wbs_node_id": nid,
            "depth": node.depth,
            "path": node.path,
            "population": {
                "activity_count": len(tasks),
                "direct_task_count": direct_count,
                "descendant_task_count": len(
                    self.session.rollup_task_ids_by_node.get(nid, set()) & scope
                ),
                "child_count": child_count,
            },
            "schedule": {
                "planned_progress_pct": progress.get("planned_progress_pct"),
                "actual_progress_pct": progress.get("actual_progress_pct"),
                "variance_pct": progress.get("variance_pct"),
                "weighting_mode": progress.get("weighting_mode"),
                "weighting_label": progress.get("weighting_label"),
                "critical_count": critical,
                "primary_late_count": primary_late,
                "primary_counts": delay_counts,
            },
            "cost": {
                "budget_total": round(budget_sum, 2) if budget_tasks else None,
                "budget_task_count": budget_tasks,
                "available": budget_tasks > 0,
                "unavailable_reason": "No task cost in scope" if not budget_tasks else "",
            },
            "evm": evm,
            "model_impact": {
                "trusted_task_count": trusted_tasks,
                "trusted_entity_count": len(unique_entities),
                "delayed_trusted_task_count": delayed_trusted,
                "entity_count_caveat": "Rolled-up entity counts deduplicate GlobalIds.",
            },
            "navigation": {
                "expand_url": expand_url,
                "activity_drilldown_url": activity_drilldown,
                "node_detail_url": reverse(
                    "scheduling:executive_controls_wbs_node",
                    kwargs={"pk": self.project_id, "node_pk": nid},
                ),
                "model_scope_url": reverse(
                    "scheduling:executive_controls_wbs_model_scope",
                    kwargs={"pk": self.project_id, "node_pk": nid},
                ),
            },
            "caveats": [],
        }

    def _row_unassigned(
        self, *, filters: ExecutiveMatrixFilters, aggregation: str
    ) -> dict[str, Any]:
        scope = set(self.session.unassigned_task_ids) & self._filtered_task_ids(filters)
        tasks = self.session.tasks_for_scope(scope)
        delay_counts: dict[str, int] = {}
        primary_late = 0
        for task in tasks:
            tid = str(task.pk)
            delay = self.session.classifier.classify_task(  # type: ignore[union-attr]
                task,
                trusted_entity_count=len(self.session.entities_by_task.get(tid, [])),
            )
            primary = delay.primary_delay_type
            delay_counts[primary] = delay_counts.get(primary, 0) + 1
            if primary in DELAYED_PRIMARY:
                primary_late += 1
        progress = self.session.progress.aggregate(  # type: ignore[union-attr]
            tasks,
            weighting_mode=filters.weighting_mode,
        )
        evm = self.session.aggregate_evm(scope)
        activity_params = filters.to_query()
        activity_params["wbs_node_id"] = UNASSIGNED_KEY
        activity_params["group_key"] = UNASSIGNED_KEY
        activity_params["dimension"] = "wbs"
        return {
            "dimension": "wbs",
            "key": UNASSIGNED_KEY,
            "label": UNASSIGNED_LABEL,
            "authority": MetricAuthority.UNAVAILABLE.value,
            "wbs_node_id": None,
            "population": {"activity_count": len(tasks), "direct_task_count": len(tasks)},
            "schedule": {
                "planned_progress_pct": progress.get("planned_progress_pct"),
                "actual_progress_pct": progress.get("actual_progress_pct"),
                "variance_pct": progress.get("variance_pct"),
                "primary_late_count": primary_late,
                "primary_counts": delay_counts,
            },
            "evm": evm,
            "model_impact": {
                "trusted_task_count": sum(
                    1 for t in tasks if str(t.pk) in self.session.trusted_task_ids
                ),
                "trusted_entity_count": len(self.session.trusted_entity_gids_unique(scope)),
            },
            "navigation": {
                "activity_drilldown_url": (
                    reverse(
                        "scheduling:executive_controls_wbs_tasks",
                        kwargs={"pk": self.project_id, "node_pk": UNASSIGNED_KEY},
                    )
                    + "?"
                    + urlencode(activity_params)
                ),
            },
            "caveats": ["Virtual analytical bucket — not a stored WBS node."],
        }

    def _filtered_task_ids(self, filters: ExecutiveMatrixFilters) -> set[str]:
        ids = {str(t.pk) for t in self.session.tasks}
        if filters.status:
            ids = {str(t.pk) for t in self.session.tasks if t.status == filters.status}
        if filters.linked_trusted is True:
            ids &= self.session.trusted_task_ids
        elif filters.linked_trusted is False:
            ids -= self.session.trusted_task_ids
        if filters.critical_only:
            ids = {str(t.pk) for t in self.session.tasks if str(t.pk) in ids and t.is_critical}
        return ids

    def _sort_rows(
        self, rows: list[dict[str, Any]], filters: ExecutiveMatrixFilters
    ) -> list[dict[str, Any]]:
        reverse = filters.sort_dir != "asc"
        key_name = filters.sort

        def _key(row: dict[str, Any]):
            if key_name == "name":
                return row.get("label", "").lower()
            if key_name == "delayed":
                return row.get("schedule", {}).get("primary_late_count", 0)
            return row.get("population", {}).get("activity_count", 0)

        return sorted(rows, key=_key, reverse=reverse)

    def _reconciliation(self) -> dict[str, Any]:
        assigned = self.session.hierarchy.assigned_task_count
        unassigned = len(self.session.unassigned_task_ids)
        eligible = self.session.hierarchy.eligible_task_count
        return {
            "eligible_tasks": eligible,
            "assigned_unique_tasks": assigned,
            "unassigned_unique_tasks": unassigned,
            "reconciles": assigned + unassigned == eligible,
        }
