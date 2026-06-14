# scheduling/services/source_version/identity_adapters.py
"""Planner-specific activity identity evidence — no parser scope expansion."""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from scheduling.models import ScheduleActivity, Task
from scheduling.services.source_version.activity_identity import ScheduleActivityIdentityService

logger = logging.getLogger(__name__)

_EVIDENCE_EXTERNAL = "external_id"
_EVIDENCE_CODE = "activity_code"
_EVIDENCE_MANUAL = "manual_generated"
_EVIDENCE_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ActivityEvidence:
    """Explicit identity evidence extracted from an import row."""

    evidence_type: str
    evidence_value: str
    authoritative: bool
    unresolved_reason: str | None
    source_type: str
    external_activity_id: str | None
    activity_code: str | None
    display_name: str | None
    canonical_activity_key: str
    identity_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "evidence_value": self.evidence_value,
            "authoritative": self.authoritative,
            "unresolved_reason": self.unresolved_reason,
            "canonical_activity_key": self.canonical_activity_key,
            "identity_status": self.identity_status,
        }


def import_batch_code_counts(tasks_data: list[dict[str, Any]]) -> Counter[str]:
    """Count activity_code occurrences within one import batch."""
    counts: Counter[str] = Counter()
    for row in tasks_data:
        code = (row.get("activity_code") or "").strip()
        if code:
            counts[code] += 1
    return counts


def extract_activity_evidence(
    task_data: dict[str, Any],
    *,
    source_type: str,
    batch_code_counts: Counter[str],
) -> ActivityEvidence:
    """Map parser row to identity evidence without name-only matching."""
    display_name = (task_data.get("name") or "")[:500]
    code = (task_data.get("activity_code") or "").strip()
    external = _stable_external_id(task_data, source_type)

    if external:
        key, status = ScheduleActivityIdentityService.build_canonical_key(
            source_type=source_type,
            external_activity_id=external,
        )
        return ActivityEvidence(
            evidence_type=_EVIDENCE_EXTERNAL,
            evidence_value=external,
            authoritative=True,
            unresolved_reason=None,
            source_type=source_type,
            external_activity_id=external,
            activity_code=code,
            display_name=display_name,
            canonical_activity_key=key,
            identity_status=status,
        )

    if code and batch_code_counts.get(code, 0) == 1:
        key, status = ScheduleActivityIdentityService.build_canonical_key(
            source_type=source_type,
            activity_code=code,
        )
        return ActivityEvidence(
            evidence_type=_EVIDENCE_CODE,
            evidence_value=code,
            authoritative=True,
            unresolved_reason=None,
            source_type=source_type,
            external_activity_id=None,
            activity_code=code,
            display_name=display_name,
            canonical_activity_key=key,
            identity_status=status,
        )

    reason = (
        "duplicate_activity_code_in_import"
        if code and batch_code_counts.get(code, 0) > 1
        else ("missing_stable_external_id" if not code else "ambiguous_activity_code")
    )
    token = str(uuid.uuid4())
    key, status = ScheduleActivityIdentityService.build_canonical_key(
        source_type=source_type,
        unresolved_token=token,
    )
    return ActivityEvidence(
        evidence_type=_EVIDENCE_UNRESOLVED,
        evidence_value=token,
        authoritative=False,
        unresolved_reason=reason,
        source_type=source_type,
        external_activity_id=None,
        activity_code=code,
        display_name=display_name,
        canonical_activity_key=key,
        identity_status=ScheduleActivity.IdentityStatus.UNRESOLVED,
    )


def extract_manual_task_evidence(task: Task) -> ActivityEvidence:
    """Generated identity for manual operational tasks."""
    token = str(uuid.uuid4())
    key, status = ScheduleActivityIdentityService.build_canonical_key(
        source_type=Task.Source.MANUAL,
        manual_key=token,
    )
    return ActivityEvidence(
        evidence_type=_EVIDENCE_MANUAL,
        evidence_value=token,
        authoritative=True,
        unresolved_reason=None,
        source_type=Task.Source.MANUAL,
        external_activity_id=None,
        activity_code=(task.activity_code or "").strip() or None,
        display_name=task.name,
        canonical_activity_key=key,
        identity_status=status,
    )


def _stable_external_id(task_data: dict[str, Any], source_type: str) -> str:
    """Prefer planner-stable IDs already present in parsed rows."""
    for key in ("_p6_obj_id", "_xer_task_id", "_msp_uid", "_external_activity_id"):
        value = (task_data.get(key) or "").strip()
        if value:
            return value
    mapped = (task_data.get("_mapped_external_id") or "").strip()
    if mapped:
        return mapped
    return ""


class BatchScheduleActivityLinker:
    """Resolve ScheduleActivity rows for an import batch without per-task queries."""

    def __init__(self, project, source_type: str) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self.source_type = source_type

    def link_task_rows(
        self,
        items: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, str]:
        """Return task_pk → schedule_activity_id for imported tasks."""
        if not items:
            return {}

        batch_counts = import_batch_code_counts([td for _, td in items])
        resolved: list[tuple[str, ActivityEvidence]] = []
        for task_pk, task_data in items:
            resolved.append(
                (
                    task_pk,
                    extract_activity_evidence(
                        task_data, source_type=self.source_type, batch_code_counts=batch_counts
                    ),
                )
            )

        keys = [ev.canonical_activity_key for _, ev in resolved]
        existing = {
            a.canonical_activity_key: a
            for a in ScheduleActivity.objects.filter(
                project_id=self.project_id,
                canonical_activity_key__in=keys,
            )
        }

        to_create: list[ScheduleActivity] = []
        key_to_id: dict[str, str] = {}
        for _, ev in resolved:
            if ev.canonical_activity_key in existing:
                key_to_id[ev.canonical_activity_key] = str(existing[ev.canonical_activity_key].pk)
                continue
            if ev.canonical_activity_key in key_to_id:
                continue
            activity = ScheduleActivity(
                project_id=self.project_id,
                canonical_activity_key=ev.canonical_activity_key,
                external_activity_id=ev.external_activity_id or "",
                activity_code=ev.activity_code or "",
                display_name=ev.display_name or "",
                source_identity_hint=ev.source_type,
                origin=ScheduleActivity.Origin.IMPORTED,
                identity_status=ev.identity_status,
                metadata={"evidence": ev.to_dict()},
            )
            to_create.append(activity)

        if to_create:
            ScheduleActivity.objects.bulk_create(to_create, ignore_conflicts=False)
            for activity in ScheduleActivity.objects.filter(
                project_id=self.project_id,
                canonical_activity_key__in=[a.canonical_activity_key for a in to_create],
            ):
                key_to_id[activity.canonical_activity_key] = str(activity.pk)

        for activity in existing.values():
            key_to_id[activity.canonical_activity_key] = str(activity.pk)

        return {
            task_pk: key_to_id[ev.canonical_activity_key]
            for task_pk, ev in resolved
            if ev.canonical_activity_key in key_to_id
        }
