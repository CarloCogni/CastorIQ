# scheduling/services/governed_mapping/adapters/sub_stage_trade.py
"""Sub-stage trade suggestion adapter — proposals only (DF-D2)."""

from __future__ import annotations

import logging

from scheduling.models import Task
from scheduling.services.governed_mapping.adapters.base import MappingSourceAdapter
from scheduling.services.governed_mapping.contracts import (
    MappingProposalDTO,
    MappingTargetIdentityDTO,
)

logger = logging.getLogger(__name__)


class SubStageTradeAdapter(MappingSourceAdapter):
    """Map Task.sub_stage to trade dimension proposals — not authoritative."""

    source_id = "sub_stage_trade"
    rule_version = "sub_stage-v1"

    def collect_proposals(self, *, limit: int | None = None) -> list[MappingProposalDTO]:
        qs = Task.objects.filter(project=self.project).exclude(sub_stage="")
        if limit:
            qs = qs[:limit]
        proposals: list[MappingProposalDTO] = []
        for task in qs.iterator(chunk_size=500):
            sub = task.sub_stage
            if not sub:
                continue
            label = task.get_sub_stage_display() if hasattr(task, "get_sub_stage_display") else sub
            target = MappingTargetIdentityDTO(
                target_type="task",
                task_id=str(task.pk),
            )
            if task.schedule_activity_id:
                target.schedule_activity_id = str(task.schedule_activity_id)
            proposals.append(
                MappingProposalDTO(
                    dimension_key=self.dimension_key,
                    proposed_value=label,
                    proposed_value_code=sub,
                    target_type="task",
                    target_id=str(task.pk),
                    target_identity=target,
                    source=self.source_id,
                    rule_version=self.rule_version,
                    confidence=0.6,
                    evidence={
                        "field": "sub_stage",
                        "value": sub,
                        "task_id": str(task.pk),
                    },
                    caveats=("Suggestion from Task.sub_stage — not governed truth.",),
                )
            )
        return proposals
