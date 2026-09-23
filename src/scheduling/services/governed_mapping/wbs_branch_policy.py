# scheduling/services/governed_mapping/wbs_branch_policy.py
"""Explicit WBS branch → governed value policy (DF-D2.1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AbstractUser
from django.db import transaction

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalDimensionValue,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
    WBSNode,
    WBSVersion,
)
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.contracts import WBSBranchMappingPolicyDTO
from scheduling.services.governed_mapping.exceptions import MappingValidationError

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = frozenset(
    {
        AnalyticalMappingSet.Status.DRAFT,
        AnalyticalMappingSet.Status.UNDER_REVIEW,
    }
)


@dataclass
class WBSBranchPolicyResult:
    """Outcome of applying one explicit WBS branch policy."""

    policy: WBSBranchMappingPolicyDTO
    assignment_id: str | None = None
    created: bool = False
    conflict: bool = False
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "assignment_id": self.assignment_id,
            "created": self.created,
            "conflict": self.conflict,
            "warnings": self.warnings,
        }


class WBSBranchMappingPolicyService:
    """Apply explicit WBS branch policies to draft mapping sets."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk

    def apply_policy(
        self,
        policy: WBSBranchMappingPolicyDTO,
        *,
        actor: AbstractUser | None = None,
        auto_approve: bool = False,
    ) -> WBSBranchPolicyResult:
        """Create or reuse a WBS-node assignment from an explicit policy."""
        result = WBSBranchPolicyResult(policy=policy)
        mapping_set = self._load_mapping_set(policy.mapping_set_id, policy.dimension_key)
        wbs_version = self._load_wbs_version(policy.wbs_version_id)
        wbs_node = self._load_wbs_node(policy.wbs_node_id, wbs_version)
        dimension_value = self._load_value(policy.dimension_value_id, mapping_set.dimension)

        if self._policy_conflict(mapping_set, wbs_node, dimension_value, policy):
            result.conflict = True
            result.warnings.append(
                "Overlapping WBS branch policy for single-cardinality dimension."
            )
            return result

        if policy.target_behavior == "inherit_to_tasks":
            if not mapping_set.inherit_wbs_to_tasks:
                mapping_set.inherit_wbs_to_tasks = True
                mapping_set.save(update_fields=["inherit_wbs_to_tasks", "updated_at"])
        elif policy.target_behavior == "map_wbs_node":
            if mapping_set.inherit_wbs_to_tasks:
                mapping_set.inherit_wbs_to_tasks = False
                mapping_set.save(update_fields=["inherit_wbs_to_tasks", "updated_at"])

        assignment = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mapping_set,
            dimension_value=dimension_value,
            target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
            wbs_node=wbs_node,
            actor=actor,
            auto_approve=auto_approve,
            evidence={
                **policy.evidence,
                "wbs_branch_policy": True,
                "include_descendants": policy.include_descendants,
                "target_behavior": policy.target_behavior,
                "reason": policy.reason,
            },
        )
        assignment.mapping_method = "wbs_branch_policy"
        assignment.authority = policy.authority
        assignment.save(update_fields=["mapping_method", "authority", "updated_at"])

        self._persist_policy_record(mapping_set, policy, assignment.pk)
        result.assignment_id = str(assignment.pk)
        result.created = True
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.ASSIGNMENT_PROPOSED,
            project=self.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            assignment=assignment,
            actor=actor,
            target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
            target_id=str(wbs_node.pk),
            evidence_summary={"wbs_branch_policy": policy.to_dict()},
        )
        return result

    def list_policies(self, mapping_set: AnalyticalMappingSet) -> list[dict[str, Any]]:
        """Return persisted explicit policies for a mapping set."""
        return list((mapping_set.metadata or {}).get("wbs_branch_policies") or [])

    def _load_mapping_set(self, mapping_set_id: str, dimension_key: str) -> AnalyticalMappingSet:
        try:
            mapping_set = AnalyticalMappingSet.objects.select_related("dimension").get(
                pk=mapping_set_id,
                project_id=self.project_id,
            )
        except AnalyticalMappingSet.DoesNotExist as exc:
            raise MappingValidationError("Mapping set not found for project.") from exc
        if mapping_set.dimension.dimension_key != dimension_key:
            raise MappingValidationError("Policy dimension_key mismatch.")
        if mapping_set.status not in _EDITABLE_STATUSES:
            raise MappingValidationError(
                "WBS branch policies require draft or under-review mapping set."
            )
        return mapping_set

    def _load_wbs_version(self, wbs_version_id: str) -> WBSVersion:
        try:
            version = WBSVersion.objects.get(pk=wbs_version_id, project_id=self.project_id)
        except WBSVersion.DoesNotExist as exc:
            raise MappingValidationError("WBS version not found for project.") from exc
        return version

    def _load_wbs_node(self, wbs_node_id: str, wbs_version: WBSVersion) -> WBSNode:
        try:
            node = WBSNode.objects.get(pk=wbs_node_id, wbs_version=wbs_version)
        except WBSNode.DoesNotExist as exc:
            raise MappingValidationError("WBS node not found in selected WBS version.") from exc
        return node

    def _load_value(
        self, dimension_value_id: str, dimension: AnalyticalDimension
    ) -> AnalyticalDimensionValue:
        try:
            value = AnalyticalDimensionValue.objects.get(pk=dimension_value_id, dimension=dimension)
        except AnalyticalDimensionValue.DoesNotExist as exc:
            raise MappingValidationError(
                "Dimension value not found for mapping set dimension."
            ) from exc
        return value

    def _policy_conflict(
        self,
        mapping_set: AnalyticalMappingSet,
        node: WBSNode,
        value: AnalyticalDimensionValue,
        policy: WBSBranchMappingPolicyDTO,
    ) -> bool:
        if mapping_set.dimension.cardinality != AnalyticalDimension.Cardinality.SINGLE:
            return False
        for existing in self.list_policies(mapping_set):
            if existing.get("wbs_node_id") == str(node.pk):
                if existing.get("dimension_value_id") != policy.dimension_value_id:
                    return True
                continue
            other_id = existing.get("wbs_node_id")
            if not other_id:
                continue
            other = WBSNode.objects.filter(pk=other_id, wbs_version_id=node.wbs_version_id).first()
            if other is None:
                continue
            if (
                self._nodes_overlap(node, other)
                and existing.get("dimension_value_id") != policy.dimension_value_id
            ):
                return True
        return False

    @staticmethod
    def _nodes_overlap(a: WBSNode, b: WBSNode) -> bool:
        if not a.path or not b.path:
            return a.pk == b.pk
        return a.path.startswith(b.path) or b.path.startswith(a.path)

    @transaction.atomic
    def _persist_policy_record(
        self,
        mapping_set: AnalyticalMappingSet,
        policy: WBSBranchMappingPolicyDTO,
        assignment_id: UUID,
    ) -> None:
        metadata = dict(mapping_set.metadata or {})
        policies = list(metadata.get("wbs_branch_policies") or [])
        record = policy.to_dict()
        record["assignment_id"] = str(assignment_id)
        policies = [p for p in policies if p.get("wbs_node_id") != policy.wbs_node_id]
        policies.append(record)
        metadata["wbs_branch_policies"] = policies
        mapping_set.metadata = metadata
        mapping_set.save(update_fields=["metadata", "updated_at"])
