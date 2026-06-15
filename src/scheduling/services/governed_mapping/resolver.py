# scheduling/services/governed_mapping/resolver.py
"""Effective governed mapping resolution — read-only (DF-D1/DF-D2)."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from uuid import UUID

from django.db.models import Count

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    ScheduleActivity,
    Task,
    WBSNode,
)
from scheduling.services.governed_mapping.contracts import (
    EffectiveMappingProvenance,
    EffectiveMappingResult,
)
from scheduling.services.governed_mapping.cross_version import (
    CrossVersionMappingService,
    CrossVersionOutcome,
)

logger = logging.getLogger(__name__)

_APPROVED = AnalyticalMappingAssignment.GovernanceStatus.APPROVED


class EffectiveMappingResolver:
    """Resolve effective governed mappings for analytical targets."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk
        self._cross_version = CrossVersionMappingService()

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
        """Resolve effective mapping with Task > ScheduleActivity > WBS precedence."""
        mapping_set = mapping_set or self.active_mapping_set(dimension)
        if mapping_set is None:
            return self._unmapped(
                dimension, resolution="unmapped", caveats=("No active mapping set.",)
            )

        direct = list(
            self._approved_assignments(mapping_set).filter(
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task_id=task.pk,
            )
        )
        if direct:
            return self._finalize(
                dimension,
                mapping_set,
                direct,
                resolution="direct",
                cross_outcome="",
            )

        activity_rows = self._cross_version.activity_assignments_for_task(mapping_set, task)
        if activity_rows:
            _, outcome = self._cross_version.resolve_activity_for_task(task)
            return self._finalize(
                dimension,
                mapping_set,
                activity_rows,
                resolution="logical_identity",
                cross_outcome=str(outcome),
            )

        inherited = self._resolve_wbs_inheritance(task, mapping_set)
        if inherited:
            return self._finalize(
                dimension,
                mapping_set,
                inherited,
                resolution="inherited",
                cross_outcome="",
                inherited_from=str(inherited[0].pk),
            )

        if self._has_proposed_for_task(mapping_set, task):
            return self._unmapped(dimension, resolution="proposed_only")
        return self._unmapped(dimension, resolution="unmapped")

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
        return self._finalize(dimension, mapping_set, list(direct), resolution="direct")

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
        return self._finalize(dimension, mapping_set, list(direct), resolution="direct")

    def task_provenance(self, task: Task) -> dict[str, Any]:
        """Full provenance across active dimensions for one task."""
        dimensions = self.active_dimensions()
        by_dimension: dict[str, Any] = {}
        for dim in dimensions:
            result = self.resolve_task(task, dim)
            by_dimension[dim.dimension_key] = result.to_dict()
        return {
            "task_id": str(task.pk),
            "schedule_activity_id": str(task.schedule_activity_id)
            if task.schedule_activity_id
            else None,
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
    ) -> list[AnalyticalMappingAssignment]:
        """WBS-node mapping inherited by descendant Tasks when enabled."""
        if not mapping_set.inherit_wbs_to_tasks or not task.wbs_node_id:
            return []
        node = task.wbs_node
        if node is None:
            return []
        nodes_by_id = {
            n.pk: n
            for n in WBSNode.objects.filter(wbs_version_id=node.wbs_version_id).only(
                "pk", "parent_id", "depth", "path"
            )
        }
        ancestors = self._ancestor_nodes_cached(node, nodes_by_id)
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

    @staticmethod
    def _path_prefixes(path: str) -> list[str]:
        parts = [p for p in path.strip("/").split("/") if p]
        prefixes: list[str] = []
        acc = ""
        for part in parts[:-1]:
            acc += f"/{part}/"
            prefixes.append(acc)
        return prefixes

    def _finalize(
        self,
        dimension: AnalyticalDimension,
        mapping_set: AnalyticalMappingSet,
        assignments: list[AnalyticalMappingAssignment],
        *,
        resolution: str,
        cross_outcome: str = "",
        inherited_from: str | None = None,
    ) -> EffectiveMappingResult:
        if not assignments:
            return self._unmapped(dimension, resolution="unmapped")
        value_ids = {a.dimension_value_id for a in assignments}
        if dimension.cardinality == AnalyticalDimension.Cardinality.SINGLE and len(value_ids) > 1:
            return EffectiveMappingResult(
                dimension_key=dimension.dimension_key,
                dimension_id=str(dimension.pk),
                resolution="conflict",
                authority=AnalyticalMappingAssignment.MappingAuthority.APPROVED,
                governance_status=_APPROVED,
                mapping_method=assignments[0].mapping_method,
                values=[],
                conflicts=[
                    {
                        "reason": "single_cardinality_conflict",
                        "value_ids": [str(v) for v in value_ids],
                    }
                ],
                caveats=("Conflicting approved values — effective resolution blocked.",),
                provenance=EffectiveMappingProvenance(
                    mapping_set_id=str(mapping_set.pk),
                    mapping_set_revision=mapping_set.revision,
                    dimension_revision=dimension.revision_number,
                    resolution_method=resolution,
                    cross_version_outcome=cross_outcome,
                ),
            )
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
            conflicts=[],
            inherited_from=inherited_from,
            assignment_ids=[str(a.pk) for a in assignments],
            provenance=EffectiveMappingProvenance(
                mapping_set_id=str(mapping_set.pk),
                mapping_set_revision=mapping_set.revision,
                dimension_revision=dimension.revision_number,
                source_assignment_id=str(assignments[0].pk),
                resolution_method=resolution,
                cross_version_outcome=cross_outcome,
                inherited_from_target=inherited_from,
                evidence_summary=assignments[0].evidence or {},
            ),
        )

    def _has_proposed_for_task(self, mapping_set: AnalyticalMappingSet, task: Task) -> bool:
        return AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task_id=task.pk,
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
        """Batch resolve with bounded queries (no per-task DB loops)."""
        mapping_set = self.active_mapping_set(dimension)
        if mapping_set is None:
            unmapped = self._unmapped(dimension)
            return {str(tid): unmapped for tid in task_ids}

        tasks = list(
            Task.objects.filter(pk__in=task_ids, project_id=self.project_id).select_related(
                "wbs_node", "schedule_activity"
            )
        )

        approved = list(
            self._approved_assignments(mapping_set).select_related("dimension_value", "wbs_node")
        )
        by_task: dict[UUID, list[AnalyticalMappingAssignment]] = defaultdict(list)
        by_activity: dict[UUID, list[AnalyticalMappingAssignment]] = defaultdict(list)
        by_wbs: dict[UUID, list[AnalyticalMappingAssignment]] = defaultdict(list)
        for assignment in approved:
            if (
                assignment.target_type == AnalyticalMappingAssignment.TargetType.TASK
                and assignment.task_id
            ):
                by_task[assignment.task_id].append(assignment)
            elif (
                assignment.target_type == AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY
                and assignment.schedule_activity_id
            ):
                by_activity[assignment.schedule_activity_id].append(assignment)
            elif (
                assignment.target_type == AnalyticalMappingAssignment.TargetType.WBS_NODE
                and assignment.wbs_node_id
            ):
                by_wbs[assignment.wbs_node_id].append(assignment)

        proposed_task_ids = set(
            AnalyticalMappingAssignment.objects.filter(
                mapping_set=mapping_set,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task_id__in=task_ids,
            ).values_list("task_id", flat=True)
        )

        activity_ids = {task.schedule_activity_id for task in tasks if task.schedule_activity_id}
        activity_task_counts: dict[UUID, int] = {}
        if activity_ids:
            activity_task_counts = dict(
                Task.objects.filter(
                    project_id=self.project_id,
                    schedule_activity_id__in=activity_ids,
                )
                .values("schedule_activity_id")
                .annotate(task_count=Count("pk"))
                .values_list("schedule_activity_id", "task_count")
            )

        wbs_inherited: dict[UUID, list[AnalyticalMappingAssignment]] = {}
        if mapping_set.inherit_wbs_to_tasks and by_wbs:
            wbs_inherited = self._batch_wbs_inheritance(tasks, by_wbs)

        results: dict[str, EffectiveMappingResult] = {}
        for task in tasks:
            direct = by_task.get(task.pk, [])
            if direct:
                results[str(task.pk)] = self._finalize(
                    dimension,
                    mapping_set,
                    direct,
                    resolution="direct",
                    cross_outcome="",
                )
                continue

            activity_rows = self._activity_rows_from_index(
                task,
                by_activity,
                activity_task_counts,
            )
            if activity_rows:
                outcome = self._activity_outcome(task, activity_task_counts)
                results[str(task.pk)] = self._finalize(
                    dimension,
                    mapping_set,
                    activity_rows,
                    resolution="logical_identity",
                    cross_outcome=str(outcome),
                )
                continue

            inherited = wbs_inherited.get(task.pk, [])
            if inherited:
                results[str(task.pk)] = self._finalize(
                    dimension,
                    mapping_set,
                    inherited,
                    resolution="inherited",
                    cross_outcome="",
                    inherited_from=str(inherited[0].pk),
                )
                continue

            if task.pk in proposed_task_ids:
                results[str(task.pk)] = self._unmapped(dimension, resolution="proposed_only")
            else:
                results[str(task.pk)] = self._unmapped(dimension, resolution="unmapped")

        for tid in task_ids:
            if str(tid) not in results:
                results[str(tid)] = self._unmapped(dimension)
        return results

    def _activity_outcome(
        self,
        task: Task,
        activity_task_counts: dict[UUID, int],
    ) -> CrossVersionOutcome:
        """Cross-version outcome without extra queries."""
        if not task.schedule_activity_id:
            return CrossVersionOutcome.UNRESOLVED_NO_CURRENT_TASK
        activity = task.schedule_activity
        if activity is None:
            return CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY
        if activity.project_id != task.project_id:
            return CrossVersionOutcome.BLOCKED_POLICY
        if activity.identity_status in {
            ScheduleActivity.IdentityStatus.RETIRED,
            ScheduleActivity.IdentityStatus.SUPERSEDED,
        }:
            return CrossVersionOutcome.BLOCKED_RETIRED_ACTIVITY
        if activity.identity_status == ScheduleActivity.IdentityStatus.UNRESOLVED:
            return CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY
        if activity_task_counts.get(activity.pk, 0) > 1:
            return CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY
        return CrossVersionOutcome.RETAINED_ON_LOGICAL_IDENTITY

    def _activity_rows_from_index(
        self,
        task: Task,
        by_activity: dict[UUID, list[AnalyticalMappingAssignment]],
        activity_task_counts: dict[UUID, int],
    ) -> list[AnalyticalMappingAssignment]:
        """Schedule-activity assignments when cross-version policy allows."""
        outcome = self._activity_outcome(task, activity_task_counts)
        if outcome in {
            CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY,
            CrossVersionOutcome.BLOCKED_RETIRED_ACTIVITY,
            CrossVersionOutcome.BLOCKED_POLICY,
            CrossVersionOutcome.UNRESOLVED_NO_CURRENT_TASK,
        }:
            return []
        if not task.schedule_activity_id:
            return []
        return by_activity.get(task.schedule_activity_id, [])

    def _batch_wbs_inheritance(
        self,
        tasks: list[Task],
        by_wbs: dict[UUID, list[AnalyticalMappingAssignment]],
    ) -> dict[UUID, list[AnalyticalMappingAssignment]]:
        """Resolve WBS inheritance for many tasks with bounded ancestor lookups."""
        task_nodes: dict[UUID, WBSNode] = {}
        version_ids: set[UUID] = set()
        for task in tasks:
            if task.wbs_node_id and task.wbs_node is not None:
                task_nodes[task.pk] = task.wbs_node
                version_ids.add(task.wbs_node.wbs_version_id)

        if not task_nodes:
            return {}

        all_nodes = list(WBSNode.objects.filter(wbs_version_id__in=version_ids))
        nodes_by_id = {node.pk: node for node in all_nodes}

        inherited: dict[UUID, list[AnalyticalMappingAssignment]] = {}
        for task_id, node in task_nodes.items():
            chain = self._ancestor_nodes_cached(node, nodes_by_id)
            candidates: list[AnalyticalMappingAssignment] = []
            for chain_node in chain:
                candidates.extend(by_wbs.get(chain_node.pk, []))
            if not candidates:
                continue
            deepest = max(candidates, key=lambda a: a.wbs_node.depth if a.wbs_node else 0)
            inherited[task_id] = [deepest]
        return inherited

    def _ancestor_nodes_cached(
        self,
        node: WBSNode,
        nodes_by_id: dict[UUID, WBSNode],
    ) -> list[WBSNode]:
        chain: list[WBSNode] = []
        current: WBSNode | None = node
        seen: set[UUID] = set()
        while current is not None and current.pk not in seen:
            chain.append(current)
            seen.add(current.pk)
            current = nodes_by_id.get(current.parent_id) if current.parent_id else None
        return chain
