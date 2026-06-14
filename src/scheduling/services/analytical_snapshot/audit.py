# scheduling/services/analytical_snapshot/audit.py
"""Append-only analytical snapshot audit event recording."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser

from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotAuditEvent,
    BaselineVersion,
    ScheduleSourceVersion,
)


def record_snapshot_event(
    *,
    snapshot: AnalyticalSnapshot,
    event_type: str,
    actor: AbstractUser | None = None,
    previous_status: str = "",
    new_status: str = "",
    reason: str = "",
    source_version: ScheduleSourceVersion | None = None,
    baseline_version: BaselineVersion | None = None,
    methodology_version: str = "",
    metadata: dict[str, Any] | None = None,
) -> AnalyticalSnapshotAuditEvent:
    """Persist one append-only snapshot audit event."""
    return AnalyticalSnapshotAuditEvent.objects.create(
        project=snapshot.project,
        snapshot=snapshot,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        actor=actor,
        reason=reason,
        source_version=source_version or snapshot.source_version,
        baseline_version=baseline_version or snapshot.baseline_version,
        methodology_version=methodology_version or snapshot.methodology_version,
        metadata=metadata or {},
    )
