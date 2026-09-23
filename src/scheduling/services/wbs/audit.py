# scheduling/services/wbs/audit.py
"""Canonical WBS population audit logging (DF-C2)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser

from scheduling.models import ScheduleSourceVersion, WBSVersion

logger = logging.getLogger(__name__)

EVENT_STARTED = "wbs_population_started"
EVENT_COMPLETED = "wbs_population_completed"
EVENT_FAILED = "wbs_population_failed"
EVENT_ACTIVATED = "wbs_version_activated"
EVENT_SUPERSEDED = "wbs_version_superseded"
EVENT_BACKFILL_DRY_RUN = "wbs_backfill_dry_run"
EVENT_BACKFILL_WRITTEN = "wbs_backfill_written"


def record_wbs_event(
    *,
    event_type: str,
    project_id,
    source_version: ScheduleSourceVersion | None = None,
    wbs_version: WBSVersion | None = None,
    actor: AbstractUser | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a WBS lifecycle event via structured logging (no raw file paths)."""
    payload: dict[str, Any] = {
        "event_type": event_type,
        "project_id": str(project_id),
        "source_version_id": str(source_version.pk) if source_version else None,
        "wbs_version_id": str(wbs_version.pk) if wbs_version else None,
        "actor_id": str(actor.pk) if actor else None,
        "metadata": metadata or {},
    }
    logger.info("wbs_audit %s", payload)
    return payload
