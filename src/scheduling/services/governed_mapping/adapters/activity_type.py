# scheduling/services/governed_mapping/adapters/activity_type.py
"""P6 activity_type scope adapter — proposals only unless explicitly configured (DF-D2.1)."""

from __future__ import annotations

import logging

from scheduling.models import AnalyticalDimension, Task
from scheduling.services.executive_controls.scope_classification import (
    AUTHORITATIVE_ACTIVITY_TYPE_MAP,
    AUTHORITATIVE_TYPE_TOKENS,
)
from scheduling.services.governed_mapping.adapters.base import MappingSourceAdapter
from scheduling.services.governed_mapping.contracts import (
    MappingAssignmentPopulationDTO,
    MappingProposalDTO,
    MappingTargetIdentityDTO,
)

logger = logging.getLogger(__name__)

# P6 Type values are planner activity categories (Task Dependent, Milestone, LOE),
# not construction trades or procurement packages.
_P6_PLANNER_TYPES = frozenset(AUTHORITATIVE_ACTIVITY_TYPE_MAP.keys())


class ActivityTypeSourceAdapter(MappingSourceAdapter):
    """Scope-category evidence from Task.activity_type — never implicit Trade/Package truth."""

    source_id = "activity_type_scope"
    rule_version = "activity_type_scope-v1"

    def _dimension(self) -> AnalyticalDimension | None:
        return (
            AnalyticalDimension.objects.filter(
                project=self.project,
                dimension_key=self.dimension_key,
            )
            .order_by("-revision_number")
            .first()
        )

    def _configured_authoritative_map(self) -> dict[str, str]:
        """Explicit project token→value map from dimension source_metadata."""
        dim = self._dimension()
        if dim is None:
            return {}
        sources = dim.source_metadata.get("authoritative_sources") or {}
        cfg = sources.get(self.source_id) or {}
        token_map = cfg.get("token_map") or {}
        return {str(k).lower(): str(v) for k, v in token_map.items()}

    def _scope_token_for_task(self, task: Task) -> str | None:
        activity_type = (task.activity_type or "").strip()
        if not activity_type:
            return None
        normalized = activity_type.lower()
        if normalized in _P6_PLANNER_TYPES:
            return None
        for token in AUTHORITATIVE_TYPE_TOKENS:
            if token in normalized:
                return token
        return None

    def collect_authoritative(self, *, limit: int | None = None):
        """Authoritative rows only when dimension declares explicit token_map."""
        token_map = self._configured_authoritative_map()
        if not token_map:
            return []
        qs = Task.objects.filter(project=self.project).exclude(activity_type="")
        if limit:
            qs = qs[:limit]
        rows: list[MappingAssignmentPopulationDTO] = []
        for task in qs.iterator(chunk_size=500):
            token = self._scope_token_for_task(task)
            if not token or token not in token_map:
                continue
            value_code = token_map[token]
            target = MappingTargetIdentityDTO(
                target_type="task",
                task_id=str(task.pk),
                schedule_activity_id=str(task.schedule_activity_id)
                if task.schedule_activity_id
                else None,
            )
            rows.append(
                MappingAssignmentPopulationDTO(
                    dimension_key=self.dimension_key,
                    value_code=value_code,
                    value_name=value_code.replace("_", " ").title(),
                    target=target,
                    mapping_method="imported",
                    authority="configured_authoritative",
                    governance_status="proposed",
                    evidence={
                        "field": "activity_type",
                        "value": task.activity_type,
                        "token": token,
                        "configured_map": True,
                    },
                    provenance={"source": self.source_id, "rule_version": self.rule_version},
                )
            )
        return rows

    def collect_proposals(self, *, limit: int | None = None) -> list[MappingProposalDTO]:
        """Proposals for scope tokens — excluded from Trade/Package unless configured."""
        dim = self._dimension()
        if dim is None:
            return []
        if dim.dimension_type in {
            AnalyticalDimension.DimensionType.TRADE,
            AnalyticalDimension.DimensionType.PACKAGE,
        }:
            allowed = dim.source_metadata.get("allowed_proposal_sources") or []
            if self.source_id not in allowed:
                return []

        qs = Task.objects.filter(project=self.project).exclude(activity_type="")
        if limit:
            qs = qs[:limit]
        proposals: list[MappingProposalDTO] = []
        for task in qs.iterator(chunk_size=500):
            token = self._scope_token_for_task(task)
            if not token:
                continue
            target = MappingTargetIdentityDTO(
                target_type="task",
                task_id=str(task.pk),
                schedule_activity_id=str(task.schedule_activity_id)
                if task.schedule_activity_id
                else None,
            )
            proposals.append(
                MappingProposalDTO(
                    dimension_key=self.dimension_key,
                    proposed_value=token.replace("_", " ").title(),
                    proposed_value_code=token,
                    target_type="task",
                    target_id=str(task.pk),
                    target_identity=target,
                    source=self.source_id,
                    rule_version=self.rule_version,
                    confidence=0.45,
                    evidence={
                        "field": "activity_type",
                        "value": task.activity_type,
                        "token": token,
                    },
                    caveats=(
                        "P6 activity_type is a planner scope category — not a construction trade or package.",
                        "Requires explicit dimension configuration for Trade/Package proposals.",
                    ),
                )
            )
        return proposals
