# scheduling/tests/capability_fixtures.py
"""Synthetic non-proprietary capability profile fixtures for cross-source regression."""

from __future__ import annotations

import datetime
from decimal import Decimal

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import (
    P6Calendar,
    P6ResourceAssignment,
    P6WBSNode,
    ScheduleSource,
    Task,
    TaskDependency,
    TaskEntityBinding,
)


def _sched_source(project, *, fmt: str, data_date=None) -> ScheduleSource:
    return ScheduleSource.objects.create(
        project=project,
        filename=f"synthetic_{fmt}.xml",
        source_format=fmt,
        task_count=0,
        data_date=data_date,
    )


def build_empty_project():
    """Empty project — no tasks."""
    return ProjectFactory()


def build_sparse_column_project(*, with_cost: bool = False):
    """Column-mapped-like: dates + progress only."""
    project = ProjectFactory()
    _sched_source(project, fmt=Task.Source.CSV)
    for i in range(5):
        Task.objects.create(
            project=project,
            name=f"Sparse {i}",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 1),
            activity_code=f"S{i:03d}",
            physical_percent_complete=0.25,
            source=Task.Source.CSV,
            cost=Decimal("1000") if with_cost else None,
        )
    return project


def build_msp_like_project():
    """MSP-like: dates, progress, deps — no P6 enrichment."""
    project = ProjectFactory()
    _sched_source(project, fmt=Task.Source.MSP)
    tasks = []
    for i in range(6):
        t = Task.objects.create(
            project=project,
            name=f"MSP {i}",
            start_date=datetime.date(2025, 1, 1) + datetime.timedelta(days=i * 7),
            end_date=datetime.date(2025, 2, 1) + datetime.timedelta(days=i * 7),
            activity_code=f"M{i:03d}",
            physical_percent_complete=0.5,
            source=Task.Source.MSP,
        )
        tasks.append(t)
    TaskDependency.objects.create(
        predecessor=tasks[0],
        successor=tasks[1],
        dep_type="FS",
        lag_days=0,
    )
    return project


def build_xer_like_project():
    """XER-like: tasks + deps + CPM float — no cost/calendar/RA."""
    project = ProjectFactory()
    _sched_source(project, fmt=Task.Source.XER)
    tasks = []
    for i in range(8):
        t = Task.objects.create(
            project=project,
            name=f"XER {i}",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 3, 1),
            activity_code=f"X{i:03d}",
            physical_percent_complete=0.4,
            total_float=2 if i > 0 else 0,
            is_critical=i == 0,
            source=Task.Source.XER,
        )
        tasks.append(t)
    TaskDependency.objects.create(predecessor=tasks[0], successor=tasks[1], dep_type="FS")
    return project


def build_p6xml_full_project():
    """Full P6 XML-like: calendars, costs, RA, activity type, data date."""
    project = ProjectFactory()
    src = _sched_source(project, fmt=Task.Source.P6XML, data_date=datetime.date(2025, 6, 1))
    cal = P6Calendar.objects.create(
        project=project,
        schedule_source=src,
        p6_calendar_id="cal-1",
        name="Standard",
        hours_per_day=8.0,
        working_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        is_pending=False,
    )
    tasks = []
    for i in range(10):
        t = Task.objects.create(
            project=project,
            name=f"P6 {i}",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 4, 1),
            activity_code=f"P{i:03d}",
            activity_type="Task Dependent" if i > 2 else "WBS Summary",
            physical_percent_complete=0.6,
            cost=Decimal("50000"),
            calendar_object_id=cal.p6_calendar_id,
            total_float=1,
            source=Task.Source.P6XML,
        )
        tasks.append(t)
        P6ResourceAssignment.objects.create(
            project=project,
            schedule_source=src,
            task=t,
            p6_activity_object_id=f"act-{i}",
            resource_type="Labor",
            planned_cost=Decimal("50000"),
            actual_cost=Decimal("30000") if i < 5 else Decimal("0"),
            planned_units=Decimal("400"),
            actual_units=Decimal("200"),
            is_pending=False,
        )
    P6WBSNode.objects.create(
        project=project,
        schedule_source=src,
        p6_object_id="wbs-1",
        code="1",
        name="Root",
        is_pending=False,
    )
    TaskDependency.objects.create(predecessor=tasks[0], successor=tasks[1], dep_type="FS")
    return project


def build_no_ifc_project():
    return build_sparse_column_project()


def build_ifc_zero_trusted_project():
    project = build_sparse_column_project()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-1")
    return project


def build_partial_trusted_project():
    project = build_sparse_column_project()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-T1")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-T2")
    task = Task.objects.filter(project=project).first()
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-T1",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    return project


def build_no_progress_project():
    project = ProjectFactory()
    Task.objects.create(
        project=project,
        name="No progress",
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 2, 1),
        status="planned",
    )
    return project


def build_no_float_project():
    project = ProjectFactory()
    Task.objects.create(
        project=project,
        name="No float",
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 2, 1),
        physical_percent_complete=0.1,
    )
    return project
