# scheduling/services/governed_mapping/coverage.py
"""Governed mapping coverage and conflict summaries (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count, Q

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    Task,
)
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver

logger = logging.getLogger(__name__)

_APPROVED = AnalyticalMappingAssignment.GovernanceStatus.APPROVED


class MappingCoverageService:
    """Coverage and conflict summaries for governed mappings."""

    def __init__(self, project) -> None:
        self.project = project
        self.resolver = EffectiveMappingResolver(project)

    def summarize(self, *, dimension_key: str | None = None) -> dict[str, Any]:
        """Project-wide or per-dimension coverage summary."""
        dims = AnalyticalDimension.objects.filter(
            project=self.project,
            is_selected_for_analysis=True,
            status=AnalyticalDimension.Status.ACTIVE,
        )
        if dimension_key:
            dims = dims.filter(dimension_key=dimension_key)
        dimensions_out: list[dict[str, Any]] = []
        for dim in dims:
            dimensions_out.append(self._dimension_summary(dim))
        return {
            "project_id": str(self.project.pk),
            "dimensions": dimensions_out,
        }

    def _dimension_summary(self, dimension: AnalyticalDimension) -> dict[str, Any]:
        mapping_set = self.resolver.active_mapping_set(dimension)
        tasks_total = Task.objects.filter(project=self.project).count()
        if mapping_set is None:
            return {
                "dimension_key": dimension.dimension_key,
                "dimension_id": str(dimension.pk),
                "mapping_set_id": None,
                "status": "no_active_set",
                "tasks_total": tasks_total,
                "mapped_effective": 0,
                "unmapped": tasks_total,
                "proposed": 0,
                "rejected": 0,
                "conflicts": 0,
                "coverage_pct": 0.0 if tasks_total else None,
            }

        counts = AnalyticalMappingAssignment.objects.filter(mapping_set=mapping_set).aggregate(
            proposed=Count("pk", filter=Q(governance_status="proposed")),
            rejected=Count("pk", filter=Q(governance_status="rejected")),
            approved=Count("pk", filter=Q(governance_status=_APPROVED)),
        )
        task_ids = list(Task.objects.filter(project=self.project).values_list("pk", flat=True))
        mapped = 0
        conflicts = 0
        for tid in task_ids:
            result = self.resolver.resolve_task(
                Task(pk=tid, project_id=self.project.pk),
                dimension,
                mapping_set=mapping_set,
            )
            if result.resolution in {"direct", "inherited"}:
                mapped += 1
            elif result.resolution == "conflict":
                conflicts += 1

        unmapped = max(0, tasks_total - mapped - conflicts)
        cov = round(100.0 * mapped / tasks_total, 2) if tasks_total else None
        return {
            "dimension_key": dimension.dimension_key,
            "dimension_id": str(dimension.pk),
            "mapping_set_id": str(mapping_set.pk),
            "mapping_set_status": mapping_set.status,
            "mapping_set_revision": mapping_set.revision,
            "tasks_total": tasks_total,
            "mapped_effective": mapped,
            "unmapped": unmapped,
            "proposed": counts["proposed"] or 0,
            "rejected": counts["rejected"] or 0,
            "approved_assignments": counts["approved"] or 0,
            "conflicts": conflicts,
            "coverage_pct": cov,
        }
