# castor/scheduling/services/p6_save.py
"""Persist P6 XML auxiliary data (WBS hierarchy + ResourceAssignments) to the DB.

Called in two phases that mirror the upload → confirm flow:

1. save_p6_pending_data(project, aux_data)
   Called from TaskUploadView / SchedulePreviewView immediately after parsing.
   Wipes any previous pending records for the project and bulk-inserts the new
   ones with is_pending=True.  Nothing is visible to the EVM screen yet.

2. finalise_p6_data(project, schedule_source, p6_obj_id_map)
   Called from TaskSaveView after tasks and ScheduleSource are created.
   Links pending records to the ScheduleSource, resolves Task FKs via the
   p6_obj_id_map, and flips is_pending → False so EVM queries pick them up.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def save_p6_pending_data(project, aux_data: dict) -> None:
    """Delete stale pending P6 records and bulk-save fresh WBS + assignment rows.

    Args:
        project:  Project instance.
        aux_data: Dict returned by parse_p6xml — must contain 'wbs_nodes' and
                  'resource_assignments' lists.
    """
    from castor.scheduling.models import P6Calendar, P6ResourceAssignment, P6WBSNode

    # Clear any unconfirmed records from a previous upload session.
    P6WBSNode.objects.filter(project=project, is_pending=True).delete()
    P6ResourceAssignment.objects.filter(project=project, is_pending=True).delete()
    P6Calendar.objects.filter(project=project, is_pending=True).delete()

    wbs_nodes = aux_data.get("wbs_nodes", [])
    wbs_objs = [
        P6WBSNode(
            project=project,
            p6_object_id=node["p6_object_id"],
            p6_parent_object_id=node.get("p6_parent_object_id") or "",
            code=node.get("code") or "",
            name=node.get("name") or "",
            original_budget=node.get("original_budget"),
            sequence_number=node.get("sequence_number"),
            is_pending=True,
        )
        for node in wbs_nodes
        if node.get("p6_object_id")
    ]
    if wbs_objs:
        P6WBSNode.objects.bulk_create(wbs_objs, batch_size=500)

    assignments = aux_data.get("resource_assignments", [])
    ra_objs = [
        P6ResourceAssignment(
            project=project,
            p6_activity_object_id=ra["activity_object_id"],
            p6_resource_object_id=ra.get("resource_object_id") or "",
            resource_type=ra.get("resource_type") or "",
            planned_cost=ra.get("planned_cost") or 0,
            actual_cost=ra.get("actual_cost") or 0,
            remaining_cost=ra.get("remaining_cost") or 0,
            at_completion_cost=ra.get("at_completion_cost") or 0,
            planned_units=ra.get("planned_units") or 0,
            actual_units=ra.get("actual_units") or 0,
            is_pending=True,
        )
        for ra in assignments
        if ra.get("activity_object_id")
    ]
    if ra_objs:
        P6ResourceAssignment.objects.bulk_create(ra_objs, batch_size=500)

    calendars = aux_data.get("calendars", [])
    cal_objs = [
        P6Calendar(
            project=project,
            p6_calendar_id=cal["p6_calendar_id"],
            name=cal.get("name") or "",
            hours_per_day=cal.get("hours_per_day") or 8.0,
            working_days=cal.get("working_days") or [],
            holidays=cal.get("holidays") or [],
            is_pending=True,
        )
        for cal in calendars
        if cal.get("p6_calendar_id")
    ]
    if cal_objs:
        P6Calendar.objects.bulk_create(cal_objs, batch_size=50)

    logger.info(
        "p6_save: staged %d WBS nodes, %d resource assignments, %d calendars for project %s",
        len(wbs_objs),
        len(ra_objs),
        len(cal_objs),
        project.pk,
    )


def finalise_p6_data(
    project,
    schedule_source,
    p6_obj_id_map: dict[str, str],
) -> None:
    """Promote pending P6 records to confirmed, linking them to the ScheduleSource.

    Args:
        project:        Project instance.
        schedule_source: Newly-created ScheduleSource for this import.
        p6_obj_id_map:  {p6_activity_object_id: str(task.pk)} — built by
                        TaskSaveView during the task upsert loop.
    """
    from castor.scheduling.models import P6Calendar, P6ResourceAssignment, P6WBSNode

    # Delete old confirmed records before promoting the new pending ones.
    old_wbs = P6WBSNode.objects.filter(project=project, is_pending=False).count()
    old_ra = P6ResourceAssignment.objects.filter(project=project, is_pending=False).count()
    old_cal = P6Calendar.objects.filter(project=project, is_pending=False).count()
    if old_wbs or old_ra or old_cal:
        P6WBSNode.objects.filter(project=project, is_pending=False).delete()
        P6ResourceAssignment.objects.filter(project=project, is_pending=False).delete()
        P6Calendar.objects.filter(project=project, is_pending=False).delete()
        logger.info(
            "p6_save: cleared %d old WBS, %d old assignments, %d old calendars for project %s",
            old_wbs,
            old_ra,
            old_cal,
            project.pk,
        )

    # Promote WBS nodes.
    P6WBSNode.objects.filter(project=project, is_pending=True).update(
        schedule_source=schedule_source,
        is_pending=False,
    )

    # Promote calendars.
    P6Calendar.objects.filter(project=project, is_pending=True).update(
        schedule_source=schedule_source,
        is_pending=False,
    )

    # Resolve Task FKs and promote ResourceAssignment rows.
    pending_ras = list(P6ResourceAssignment.objects.filter(project=project, is_pending=True))
    resolved = 0
    for ra in pending_ras:
        task_pk = p6_obj_id_map.get(ra.p6_activity_object_id)
        if task_pk:
            ra.task_id = task_pk
            resolved += 1
        ra.schedule_source = schedule_source
        ra.is_pending = False

    if pending_ras:
        P6ResourceAssignment.objects.bulk_update(
            pending_ras,
            ["task_id", "schedule_source", "is_pending"],
            batch_size=500,
        )

    logger.info(
        "p6_save: confirmed %d WBS nodes and %d assignments (%d task-linked) for source %s",
        P6WBSNode.objects.filter(schedule_source=schedule_source).count(),
        len(pending_ras),
        resolved,
        schedule_source.pk,
    )
