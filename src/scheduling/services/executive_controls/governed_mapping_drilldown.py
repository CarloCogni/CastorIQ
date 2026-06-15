# scheduling/services/executive_controls/governed_mapping_drilldown.py
"""Governed mapping drilldowns for E8 (DF-D3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scheduling.models import AnalyticalMappingAssignment, Task
from scheduling.services.executive_controls.governed_mapping_analytics_session import (
    CONFLICT_BUCKET,
    PROPOSED_BUCKET,
    UNMAPPED_BUCKET,
    GovernedMappingAnalyticsSession,
)
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 50


class GovernedMappingDrilldownService:
    """Project-scoped read-only drilldowns for governed dimensions."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def tasks_for_value(
        self,
        dimension_key: str,
        value_id: str,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE,
        requested_mode: str | None = None,
    ) -> dict[str, Any]:
        """Tasks mapped to a governed dimension value or virtual bucket."""
        page_size = min(MAX_PAGE_SIZE, max(1, page_size))
        page = max(1, page)
        requested = {dimension_key: requested_mode} if requested_mode else None
        session = GovernedMappingAnalyticsSession.load(
            self.project, dimension_keys=[dimension_key], requested_modes=requested
        )
        mode = session.mode_context.dimensions.get(dimension_key)
        buckets = session.bucket_task_ids(dimension_key)
        task_ids = sorted(buckets.get(value_id, set()))
        total = len(task_ids)
        start = (page - 1) * page_size
        page_ids = task_ids[start : start + page_size]
        tasks = [
            self._serialize_task(session.tasks_by_id[tid], session)
            for tid in page_ids
            if tid in session.tasks_by_id
        ]
        return self._payload(
            dimension_key,
            value_id,
            mode,
            tasks,
            page,
            page_size,
            total,
            drilldown_type="value_tasks",
        )

    def unmapped_tasks(
        self,
        dimension_key: str,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        return self.tasks_for_value(dimension_key, UNMAPPED_BUCKET, page=page, page_size=page_size)

    def conflicts(
        self,
        dimension_key: str,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Conflict assignments for dimension."""
        page_size = min(MAX_PAGE_SIZE, max(1, page_size))
        page = max(1, page)
        dim = (
            session_dim := GovernedMappingAnalyticsSession.load(
                self.project, dimension_keys=[dimension_key]
            )
        ).dimensions_by_key.get(dimension_key)
        if dim is None:
            return {"error": "Dimension not found"}
        mode = session_dim.mode_context.dimensions.get(dimension_key)
        resolver = EffectiveMappingResolver(self.project)
        mapping_set = resolver.active_mapping_set(dim)
        if mapping_set is None:
            return self._payload(
                dimension_key, CONFLICT_BUCKET, mode, [], page, page_size, 0, "conflicts"
            )

        conflict_task_ids: list[str] = []
        for tid, result in session_dim.resolutions_by_dimension.get(dimension_key, {}).items():
            if result.resolution == "conflict":
                conflict_task_ids.append(tid)
        conflict_task_ids.sort()
        total = len(conflict_task_ids)
        start = (page - 1) * page_size
        page_ids = conflict_task_ids[start : start + page_size]
        tasks = [
            self._serialize_task(session_dim.tasks_by_id[tid], session_dim)
            for tid in page_ids
            if tid in session_dim.tasks_by_id
        ]
        assignments = list(
            AnalyticalMappingAssignment.objects.filter(
                mapping_set=mapping_set,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
            ).select_related("dimension_value", "task")[:page_size]
        )
        conflict_rows = [
            {
                "assignment_id": str(a.pk),
                "task_id": str(a.task_id) if a.task_id else None,
                "value": a.dimension_value.name if a.dimension_value else None,
                "target_type": a.target_type,
            }
            for a in assignments
            if a.task_id and str(a.task_id) in set(conflict_task_ids)
        ]
        payload = self._payload(
            dimension_key, CONFLICT_BUCKET, mode, tasks, page, page_size, total, "conflicts"
        )
        payload["conflict_assignments"] = conflict_rows
        return payload

    def proposed_tasks(
        self,
        dimension_key: str,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        return self.tasks_for_value(dimension_key, PROPOSED_BUCKET, page=page, page_size=page_size)

    @staticmethod
    def _serialize_task(task: Task, session: GovernedMappingAnalyticsSession) -> dict[str, Any]:
        tid = str(task.pk)
        trusted = tid in session.trusted_task_ids
        return {
            "task_id": tid,
            "name": task.name,
            "activity_code": task.activity_code,
            "status": task.status,
            "sub_stage": task.sub_stage,
            "activity_type": task.activity_type,
            "trusted_binding": trusted,
            "entity_count": len(session.entities_by_task.get(tid, [])),
        }

    def _payload(
        self,
        dimension_key: str,
        value_id: str,
        mode,
        tasks: list[dict],
        page: int,
        page_size: int,
        total: int,
        drilldown_type: str,
    ) -> dict[str, Any]:
        return {
            "section": drilldown_type,
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "dimension_key": dimension_key,
            "value_id": value_id,
            "selected_mode": mode.selected_mode if mode else "unavailable",
            "mode_label": mode.mode_label if mode else "",
            "mapping_set_id": mode.active_mapping_set_id if mode else None,
            "mapping_set_revision": mode.mapping_set_revision if mode else None,
            "tasks": tasks,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_next": (page - 1) * page_size + page_size < total,
            },
            "calculated_at": datetime.now(UTC).isoformat(),
        }
