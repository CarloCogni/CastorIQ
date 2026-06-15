# scheduling/services/governed_mapping/cross_version.py
"""Cross-version ScheduleActivity mapping resolution (DF-D2)."""

from __future__ import annotations

import logging
from enum import StrEnum

from scheduling.models import AnalyticalMappingAssignment, ScheduleActivity, Task

logger = logging.getLogger(__name__)


class CrossVersionOutcome(StrEnum):
    CARRIED_FORWARD = "carried_forward"
    RETAINED_ON_LOGICAL_IDENTITY = "retained_on_logical_identity"
    BLOCKED_AMBIGUOUS_IDENTITY = "blocked_ambiguous_identity"
    BLOCKED_CONFLICT = "blocked_conflict"
    BLOCKED_RETIRED_ACTIVITY = "blocked_retired_activity"
    BLOCKED_POLICY = "blocked_policy"
    UNRESOLVED_NO_CURRENT_TASK = "unresolved_no_current_task"


class CrossVersionMappingService:
    """Validate schedule-activity mapping resolution after replacement."""

    def resolve_activity_for_task(self, task: Task) -> tuple[ScheduleActivity | None, CrossVersionOutcome]:
        """Return linked activity and cross-version outcome for a task."""
        if not task.schedule_activity_id:
            return None, CrossVersionOutcome.UNRESOLVED_NO_CURRENT_TASK

        activity = task.schedule_activity
        if activity is None:
            return None, CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY

        if activity.project_id != task.project_id:
            return None, CrossVersionOutcome.BLOCKED_POLICY

        if activity.identity_status in {
            ScheduleActivity.IdentityStatus.RETIRED,
            ScheduleActivity.IdentityStatus.SUPERSEDED,
        }:
            return activity, CrossVersionOutcome.BLOCKED_RETIRED_ACTIVITY

        if activity.identity_status == ScheduleActivity.IdentityStatus.UNRESOLVED:
            return activity, CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY

        tasks_for_activity = Task.objects.filter(
            project_id=task.project_id,
            schedule_activity_id=activity.pk,
        ).count()
        if tasks_for_activity > 1:
            return activity, CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY

        return activity, CrossVersionOutcome.RETAINED_ON_LOGICAL_IDENTITY

    def activity_assignments_for_task(
        self,
        mapping_set,
        task: Task,
    ) -> list[AnalyticalMappingAssignment]:
        """Approved schedule-activity assignments applicable to current task."""
        activity, outcome = self.resolve_activity_for_task(task)
        if activity is None or outcome in {
            CrossVersionOutcome.BLOCKED_AMBIGUOUS_IDENTITY,
            CrossVersionOutcome.BLOCKED_RETIRED_ACTIVITY,
            CrossVersionOutcome.BLOCKED_POLICY,
            CrossVersionOutcome.UNRESOLVED_NO_CURRENT_TASK,
        }:
            return []

        direct_task_conflict = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task_id=task.pk,
        ).exists()
        if direct_task_conflict:
            return []

        return list(
            AnalyticalMappingAssignment.objects.filter(
                mapping_set=mapping_set,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
                target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
                schedule_activity_id=activity.pk,
            ).select_related("dimension_value")
        )
