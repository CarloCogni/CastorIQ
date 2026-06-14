# scheduling/services/baseline/audit.py
"""Append-only baseline audit event recording."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser

from scheduling.models import BaselineAuditEvent, BaselineVersion, ScheduleSourceVersion


def record_baseline_event(
    *,
    baseline: BaselineVersion,
    event_type: str,
    actor: AbstractUser | None = None,
    previous_status: str = "",
    new_status: str = "",
    reason: str = "",
    source_version: ScheduleSourceVersion | None = None,
    metadata: dict[str, Any] | None = None,
) -> BaselineAuditEvent:
    """Persist one append-only baseline audit event."""
    return BaselineAuditEvent.objects.create(
        project=baseline.project,
        baseline_version=baseline,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        actor=actor,
        reason=reason,
        source_version=source_version or baseline.source_version,
        metadata=metadata or {},
    )
