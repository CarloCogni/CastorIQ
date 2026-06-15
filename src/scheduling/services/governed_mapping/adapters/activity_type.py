# scheduling/services/governed_mapping/adapters/activity_type.py
"""Activity type authoritative adapter when tokens match (DF-D2)."""

from __future__ import annotations

import logging

from scheduling.models import Task
from scheduling.services.executive_controls.scope_classification import (
    AUTHORITATIVE_TYPE_TOKENS,
)
from scheduling.services.governed_mapping.adapters.base import MappingSourceAdapter
from scheduling.services.governed_mapping.contracts import (
    MappingAssignmentPopulationDTO,
    MappingTargetIdentityDTO,
)

logger = logging.getLogger(__name__)


class ActivityTypeSourceAdapter(MappingSourceAdapter):
    """Explicit activity_type tokens may populate authoritative assignments."""

    source_id = "activity_type_authoritative"
    rule_version = "activity_type-v1"

    def collect_authoritative(self, *, limit: int | None = None):
        qs = Task.objects.filter(project=self.project).exclude(activity_type="")
        if limit:
            qs = qs[:limit]
        rows: list[MappingAssignmentPopulationDTO] = []
        for task in qs.iterator(chunk_size=500):
            normalized = (task.activity_type or "").lower()
            matched = None
            for token in AUTHORITATIVE_TYPE_TOKENS:
                if token in normalized:
                    matched = token
                    break
            if not matched:
                continue
            target = MappingTargetIdentityDTO(
                target_type="schedule_activity" if task.schedule_activity_id else "task",
                task_id=str(task.pk) if not task.schedule_activity_id else None,
                schedule_activity_id=str(task.schedule_activity_id)
                if task.schedule_activity_id
                else None,
            )
            rows.append(
                MappingAssignmentPopulationDTO(
                    dimension_key=self.dimension_key,
                    value_code=matched,
                    value_name=matched.replace("_", " ").title(),
                    target=target,
                    mapping_method="imported",
                    authority="authoritative",
                    governance_status="approved",
                    evidence={
                        "field": "activity_type",
                        "value": task.activity_type,
                        "token": matched,
                    },
                    provenance={"source": self.source_id, "rule_version": self.rule_version},
                )
            )
        return rows

    def collect_proposals(self, *, limit: int | None = None):
        return []
