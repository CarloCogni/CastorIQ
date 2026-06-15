# scheduling/services/governed_mapping/audit.py
"""Append-only governed mapping audit events (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
)

logger = logging.getLogger(__name__)


def record_mapping_event(
    *,
    event_type: str,
    project,
    dimension: AnalyticalDimension | None = None,
    mapping_set: AnalyticalMappingSet | None = None,
    assignment: AnalyticalMappingAssignment | None = None,
    actor: AbstractUser | None = None,
    previous_state: str = "",
    resulting_state: str = "",
    target_type: str = "",
    target_id: str = "",
    reason_code: str = "",
    reason_text: str = "",
    evidence_summary: dict[str, Any] | None = None,
) -> MappingGovernanceEvent:
    """Persist append-only mapping governance event."""
    event = MappingGovernanceEvent.objects.create(
        project=project,
        dimension=dimension,
        mapping_set=mapping_set,
        assignment=assignment,
        event_type=event_type,
        previous_state=previous_state,
        resulting_state=resulting_state,
        target_type=target_type,
        target_id=target_id,
        reason_code=reason_code,
        reason_text=reason_text,
        evidence_summary=evidence_summary or {},
        actor=actor,
    )
    logger.info(
        "mapping_audit event=%s project=%s dimension=%s set=%s",
        event_type,
        project.pk,
        dimension.pk if dimension else None,
        mapping_set.pk if mapping_set else None,
    )
    return event
