# scheduling/services/wbs/coverage.py
"""WBS assignment coverage summaries for APIs and capability profile (DF-C1)."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q

from scheduling.models import Task, WBSNode, WBSVersion


class WBSCoverageService:
    """Assigned/unassigned counts and coverage for a project or WBS version."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk

    def project_summary(self) -> dict[str, Any]:
        """Coverage across all tasks in the project."""
        total = Task.objects.filter(project_id=self.project_id).count()
        assigned = Task.objects.filter(project_id=self.project_id, wbs_node__isnull=False).count()
        return self._build(total, assigned)

    def version_summary(self, wbs_version: WBSVersion) -> dict[str, Any]:
        """Coverage for tasks assigned to nodes in a specific WBS version."""
        if wbs_version.project_id != self.project_id:
            raise ValueError("WBS version must belong to the project.")
        total = Task.objects.filter(project_id=self.project_id).count()
        assigned = Task.objects.filter(
            project_id=self.project_id,
            wbs_node__wbs_version=wbs_version,
        ).count()
        node_count = WBSNode.objects.filter(wbs_version=wbs_version).count()
        payload = self._build(total, assigned)
        payload["wbs_version_id"] = str(wbs_version.pk)
        payload["node_count"] = node_count
        payload["hierarchy_integrity"] = (
            WBSHierarchyIntegrity.summary(wbs_version) if node_count else None
        )
        return payload

    @staticmethod
    def _build(total: int, assigned: int) -> dict[str, Any]:
        unassigned = max(0, total - assigned)
        pct = round(100.0 * assigned / total, 2) if total else None
        return {
            "total_tasks": total,
            "assigned_tasks": assigned,
            "unassigned_tasks": unassigned,
            "assignment_coverage_pct": pct,
            "fully_assigned": total > 0 and assigned == total,
            "partially_assigned": 0 < assigned < total if total else False,
            "has_assignments": assigned > 0,
        }


class WBSHierarchyIntegrity:
    """Lightweight integrity checks without recursive ORM."""

    @staticmethod
    def summary(wbs_version: WBSVersion) -> dict[str, Any]:
        agg = WBSNode.objects.filter(wbs_version=wbs_version).aggregate(
            node_count=Count("id"),
            root_count=Count("id", filter=Q(depth=0)),
            orphan_count=Count("id", filter=Q(depth__gt=0, parent__isnull=True)),
        )
        valid = (agg["orphan_count"] or 0) == 0
        return {
            "node_count": agg["node_count"] or 0,
            "root_count": agg["root_count"] or 0,
            "orphan_count": agg["orphan_count"] or 0,
            "valid": valid,
        }
