# scheduling/services/governed_mapping/resolver.py
"""Effective governed mapping resolution — read-only (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.db.models import Q

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    Task,
    WBSNode,
)
from scheduling.services.governed_mapping.contracts import EffectiveMappingResult

logger = logging.getLogger(__name__)

_APPROVED = AnalyticalMappingAssignment.GovernanceStatus.APPROVED
_DIRECT_OVERRIDE_INHERITED = True


class EffectiveMappingResolver:
    """Resolve effective governed mappings for analytical targets."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk

    def active_mapping_set(self, dimension: AnalyticalDimension) -> AnalyticalMappingSet | None:
        """Return selected active mapping set for dimension."""
        return (
            AnalyticalMappingSet.objects.filter(
                project_id=self.project_id,
                dimension=dimension,
                is_selected_for_analysis=True,
                status=AnalyticalMappingSet.Status.ACTIVE,
            )
            .select_related("dimension")
            .first()
        )

    def active_dimensions(self) -> list[AnalyticalDimension]:
        """Selected active dimensions for analysis."""
        return list(
            AnalyticalDimension.objects.filter(
                project_id=self.project_id,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            ).order_by("dimension_key", "-revision_number")
        )

    def resolve_task(
        self,
        task: Task,
        dimension: AnalyticalDimension,
        *,
        mapping_set: AnalyticalMappingSet | None = None,
    ) -> EffectiveMappingResult:
        """Resolve effective mapping for a Task within one dimension."""
        mapping_set = mapping_set or self.active_mapping_set(dimension)
        if mapping_set is None:
            return self._unmapped(
                dimension, resolution="unmapped", caveats=("No active mapping set.",)
            )

        direct = self._approved_assignments(mapping_set).filter(
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task_id=task.pk,
        )
        inherited = self._resolve_wbs_inheritance(task, mapping_set, direct.exists())
        return self._merge_results(dimension, direct, inherited)

    def resolve_wbs_node(
        self,
        wbs_node: WBSNode,
        dimension: AnalyticalDimension,
        *,
        mapping_set: AnalyticalMappingSet | None = None,
    ) -> EffectiveMappingResult:
        """Resolve effective mapping for a WBS node."""
        mapping_set = mapping_set or self.active_mapping_set(dimension)
        if mapping_set is None:
            return self._unmapped(dimension, resolution="unmapped")
        direct = self._approved_assignments(mapping_set).filter(
            target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
            wbs_node_id=wbs_node.pk,
        )
        return self._merge_results(dimension, direct, [])

    def resolve_entity(
        self,
        entity_global_id: str,
        dimension: AnalyticalDimension,
        *,
        mapping_set: AnalyticalMappingSet | None = None,
    ) -> EffectiveMappingResult:
        """Resolve effective mapping for stable IFC GlobalId."""
        mapping_set = mapping_set or self.active_mapping_set(dimension)
        if mapping_set is None:
            return self._unmapped(dimension, resolution="unmapped")
        direct = self._approved_assignments(mapping_set).filter(
            target_type=AnalyticalMappingAssignment.TargetType.IFC_ENTITY,
            entity_global_id=entity_global_id,
        )
        return self._merge_results(dimension, direct, [])

    def task_provenance(self, task: Task) -> dict[str, Any]:
        """Full provenance across active dimensions for one task."""
        dimensions = self.active_dimensions()
        by_dimension: dict[str, Any] = {}
        for dim in dimensions:
            result = self.resolve_task(task, dim)
            by_dimension[dim.dimension_key] = result.to_dict()
        return {
            "task_id": str(task.pk),
            "dimensions": by_dimension,
        }

    def _approved_assignments(self, mapping_set: AnalyticalMappingSet):
        return AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status=_APPROVED,
        ).select_related("dimension_value")

    def _resolve_wbs_inheritance(
        self,
        task: Task,
        mapping_set: AnalyticalMappingSet,
        has_direct: bool,
    ) -> list[AnalyticalMappingAssignment]:
        """WBS-node mapping inherited by descendant Tasks when enabled."""
        if not mapping_set.inherit_wbs_to_tasks or has_direct:
            return []
        if not task.wbs_node_id:
            return []
        node = task.wbs_node
        if node is None:
            return []
        ancestors = self._ancestor_nodes(node)
        assignments = list(
            self._approved_assignments(mapping_set).filter(
                target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
                wbs_node_id__in=[n.pk for n in ancestors],
            )
        )
        if not assignments:
            return []
        deepest = max(assignments, key=lambda a: a.wbs_node.depth if a.wbs_node else 0)
        return [deepest]

    def _ancestor_nodes(self, node: WBSNode) -> list[WBSNode]:
        """Node and ancestors by path prefix."""
        if not node.path:
            return [node]
        version_id = node.wbs_version_id
        prefix = node.path
        return list(
            WBSNode.objects.filter(wbs_version_id=version_id)
            .filter(Q(path=prefix) | Q(path__in=self._path_prefixes(prefix)))
            .order_by("-depth")
        )

    @staticmethod
    def _path_prefixes(path: str) -> list[str]:
        parts = [p for p in path.strip("/").split("/") if p]
        prefixes: list[str] = []
        acc = ""
        for part in parts[:-1]:
            acc += f"/{part}/"
            prefixes.append(acc)
        return prefixes

    def _merge_results(
        self,
        dimension: AnalyticalDimension,
        direct_qs,
        inherited: list[AnalyticalMappingAssignment],
    ) -> EffectiveMappingResult:
        direct = list(direct_qs)
        if direct and inherited and _DIRECT_OVERRIDE_INHERITED:
            inherited = []
        assignments = direct or inherited
        if not assignments:
            proposed = self._has_non_effective(dimension, direct_qs)
            if proposed:
                return self._unmapped(dimension, resolution="proposed_only")
            return self._unmapped(dimension, resolution="unmapped")

        value_ids = {a.dimension_value_id for a in assignments}
        conflicts: list[dict[str, Any]] = []
        if dimension.cardinality == AnalyticalDimension.Cardinality.SINGLE and len(value_ids) > 1:
            conflicts = [
                {
                    "reason": "single_cardinality_conflict",
                    "value_ids": [str(v) for v in value_ids],
                }
            ]
            return EffectiveMappingResult(
                dimension_key=dimension.dimension_key,
                dimension_id=str(dimension.pk),
                resolution="conflict",
                authority=AnalyticalMappingAssignment.MappingAuthority.APPROVED,
                governance_status=_APPROVED,
                mapping_method=assignments[0].mapping_method,
                values=[],
                conflicts=conflicts,
                caveats=("Conflicting approved values — effective resolution blocked.",),
            )

        resolution = "direct" if direct else "inherited"
        values = [
            {
                "value_id": str(a.dimension_value_id),
                "name": a.dimension_value.name,
                "code": a.dimension_value.code or None,
            }
            for a in assignments
        ]
        return EffectiveMappingResult(
            dimension_key=dimension.dimension_key,
            dimension_id=str(dimension.pk),
            resolution=resolution,
            authority=assignments[0].authority,
            governance_status=assignments[0].governance_status,
            mapping_method=assignments[0].mapping_method,
            values=values,
            conflicts=conflicts,
            inherited_from=str(inherited[0].pk) if inherited else None,
            assignment_ids=[str(a.pk) for a in assignments],
        )

    def _has_non_effective(self, dimension: AnalyticalDimension, direct_qs) -> bool:
        mapping_set = self.active_mapping_set(dimension)
        if mapping_set is None:
            return False
        return AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
        ).exists()

    def _unmapped(
        self,
        dimension: AnalyticalDimension,
        *,
        resolution: str = "unmapped",
        caveats: tuple[str, ...] = (),
    ) -> EffectiveMappingResult:
        return EffectiveMappingResult(
            dimension_key=dimension.dimension_key,
            dimension_id=str(dimension.pk),
            resolution=resolution,
            authority=AnalyticalMappingAssignment.MappingAuthority.UNAVAILABLE,
            governance_status="unmapped",
            mapping_method="",
            values=[],
            conflicts=[],
            caveats=caveats,
        )

    def resolve_many_tasks(
        self,
        task_ids: list[UUID],
        dimension: AnalyticalDimension,
    ) -> dict[str, EffectiveMappingResult]:
        """Batch resolve for performance harness."""
        mapping_set = self.active_mapping_set(dimension)
        if mapping_set is None:
            unmapped = self._unmapped(dimension)
            return {str(tid): unmapped for tid in task_ids}

        tasks = list(
            Task.objects.filter(pk__in=task_ids, project_id=self.project_id).select_related(
                "wbs_node"
            )
        )
        results: dict[str, EffectiveMappingResult] = {}
        for task in tasks:
            results[str(task.pk)] = self.resolve_task(task, dimension, mapping_set=mapping_set)
        for tid in task_ids:
            if str(tid) not in results:
                results[str(tid)] = self._unmapped(dimension)
        return results
