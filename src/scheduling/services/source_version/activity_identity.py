# scheduling/services/source_version/activity_identity.py
"""Logical schedule activity identity — no name-only matching."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import transaction

from scheduling.models import ScheduleActivity, Task
from scheduling.services.source_version.contracts import ActivityIdentityResult

logger = logging.getLogger(__name__)


class ScheduleActivityIdentityService:
    """Create or resolve ScheduleActivity from explicit stable evidence."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    @staticmethod
    def build_canonical_key(
        *,
        source_type: str,
        external_activity_id: str | None = None,
        activity_code: str | None = None,
        manual_key: str | None = None,
        unresolved_token: str | None = None,
    ) -> tuple[str, str]:
        """Return (canonical_activity_key, expected_identity_status).

        Priority:
        1. stable external activity ID
        2. activity code (caller must verify uniqueness)
        3. explicit manual UUID token
        4. generated unresolved token
        """
        ext = (external_activity_id or "").strip()
        if ext:
            return f"{source_type}:ext:{ext}", ScheduleActivity.IdentityStatus.ACTIVE

        code = (activity_code or "").strip()
        if code:
            return f"{source_type}:code:{code}", ScheduleActivity.IdentityStatus.ACTIVE

        if manual_key:
            return f"manual:{manual_key}", ScheduleActivity.IdentityStatus.ACTIVE

        token = unresolved_token or str(uuid.uuid4())
        return f"unresolved:{token}", ScheduleActivity.IdentityStatus.UNRESOLVED

    def _code_is_unique(self, activity_code: str, exclude_pk: str | None = None) -> bool:
        qs = ScheduleActivity.objects.filter(
            project_id=self.project_id,
            activity_code=activity_code,
        ).exclude(activity_code="")
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return not qs.exists()

    @transaction.atomic
    def get_or_create_from_evidence(
        self,
        *,
        source_type: str,
        external_activity_id: str | None = None,
        activity_code: str | None = None,
        display_name: str | None = None,
        origin: str = ScheduleActivity.Origin.IMPORTED,
        metadata: dict[str, Any] | None = None,
        allow_generated_manual: bool = False,
    ) -> ActivityIdentityResult:
        """Resolve activity identity without matching on name or dates."""
        code = (activity_code or "").strip()
        ext = (external_activity_id or "").strip()

        if not ext and not code and not allow_generated_manual:
            key, status = self.build_canonical_key(
                source_type=source_type,
                unresolved_token=str(uuid.uuid4()),
            )
        elif not ext and code and not self._code_is_unique(code):
            key, status = self.build_canonical_key(
                source_type=source_type,
                unresolved_token=str(uuid.uuid4()),
            )
            status = ScheduleActivity.IdentityStatus.UNRESOLVED
            meta = dict(metadata or {})
            meta["ambiguity"] = "duplicate_activity_code"
            metadata = meta
        elif allow_generated_manual and not ext and not code:
            key, status = self.build_canonical_key(
                source_type=source_type,
                manual_key=str(uuid.uuid4()),
            )
            origin = ScheduleActivity.Origin.MANUAL
        else:
            key, status = self.build_canonical_key(
                source_type=source_type,
                external_activity_id=ext or None,
                activity_code=code or None,
            )

        existing = ScheduleActivity.objects.filter(
            project_id=self.project_id,
            canonical_activity_key=key,
        ).first()
        if existing:
            return ActivityIdentityResult(
                activity_id=str(existing.pk),
                canonical_activity_key=existing.canonical_activity_key,
                identity_status=existing.identity_status,
                created=False,
            )

        activity = ScheduleActivity.objects.create(
            project_id=self.project_id,
            canonical_activity_key=key,
            external_activity_id=ext,
            activity_code=code,
            display_name=(display_name or "")[:500],
            source_identity_hint=source_type,
            origin=origin,
            identity_status=status,
            metadata=metadata or {},
        )
        logger.info(
            "Created ScheduleActivity %s for project %s key=%s",
            activity.pk,
            self.project_id,
            key,
        )
        return ActivityIdentityResult(
            activity_id=str(activity.pk),
            canonical_activity_key=activity.canonical_activity_key,
            identity_status=activity.identity_status,
            created=True,
        )

    def create_for_manual_task(self, task: Task) -> ActivityIdentityResult:
        """Assign a generated logical identity to a manual task."""
        return self.get_or_create_from_evidence(
            source_type=Task.Source.MANUAL,
            display_name=task.name,
            allow_generated_manual=True,
            origin=ScheduleActivity.Origin.MANUAL,
            metadata={"task_id": str(task.pk)},
        )
