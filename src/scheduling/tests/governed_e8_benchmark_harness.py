# scheduling/tests/governed_e8_benchmark_harness.py
"""Deterministic governed E8 analytics benchmark harness (DF-D3.1)."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    ScheduleActivity,
    ScheduleSourceVersion,
    Task,
    TaskEntityBinding,
    WBSNode,
)
from scheduling.services.executive_controls.dimension_mode import (
    MODE_GOVERNED_PARTIAL,
    DimensionModeService,
)
from scheduling.services.executive_controls.governed_mapping_aggregation import (
    GovernedMappingAggregationService,
)
from scheduling.services.executive_controls.governed_mapping_analytics_session import (
    GovernedMappingAnalyticsSession,
)
from scheduling.services.executive_controls.governed_mapping_drilldown import (
    GovernedMappingDrilldownService,
)
from scheduling.services.executive_controls.governed_mapping_reconciliation import (
    GovernedMappingReconciliationService,
)
from scheduling.services.governed_mapping.contracts import WBSBranchMappingPolicyDTO
from scheduling.services.governed_mapping.dimension import AnalyticalDimensionService
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService
from scheduling.services.governed_mapping.wbs_branch_policy import WBSBranchMappingPolicyService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory

BENCHMARK_SEED = 42
DEFAULT_START = date(2025, 1, 1)
DEFAULT_END = date(2025, 12, 31)


@dataclass
class GovernedE8BenchmarkFixture:
    project: Any
    user: Any
    trade_dim: AnalyticalDimension
    package_dim: AnalyticalDimension
    trade_mset: AnalyticalMappingSet
    package_mset: AnalyticalMappingSet
    tasks: list[Task]
    activities: list[ScheduleActivity]
    task_count: int
    setup_seconds: float = 0.0


@dataclass
class StageTiming:
    name: str
    median_s: float
    pct_total: float = 0.0
    query_count: int = 0
    rows: int = 0
    finding: str = ""


@dataclass
class GovernedE8BenchmarkRun:
    task_count: int
    fixture_setup_s: float
    cold_summary_s: float
    summary_median_s: float
    summary_min_s: float
    summary_max_s: float
    summary_query_count: int
    resolve_median_s: float
    readiness_median_s: float
    rollup_median_s: float
    drilldown_value_median_s: float
    drilldown_unmapped_median_s: float
    drilldown_conflict_median_s: float
    drilldown_query_count: int
    governed_value_rows: int
    stages: list[StageTiming] = field(default_factory=list)


def _trade_setup(project, user) -> tuple[AnalyticalDimension, AnalyticalMappingSet, list]:
    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="trade",
        name="Trade",
        dimension_type=AnalyticalDimension.DimensionType.TRADE,
        actor=user,
    )
    vals = [
        AnalyticalDimensionValueService(dim).create_value(name="Electrical", code="electrical"),
        AnalyticalDimensionValueService(dim).create_value(name="Concrete", code="concrete"),
    ]
    AnalyticalDimensionService.activate(dim, actor=user)
    mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade Bench", actor=user)
    return dim, mset, vals


def _package_setup(
    project, user
) -> tuple[AnalyticalDimension, AnalyticalMappingSet, list, WBSNode]:
    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="package",
        name="Package",
        dimension_type=AnalyticalDimension.DimensionType.PACKAGE,
        actor=user,
    )
    val = AnalyticalDimensionValueService(dim).create_value(name="Envelope", code="envelope")
    AnalyticalDimensionService.activate(dim, actor=user)
    mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Pkg Bench", actor=user)
    version = WBSVersionService.create_draft(project=project, name="Bench WBS", actor=user)
    svc = WBSHierarchyService(version)
    root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT))
    branch = svc.create_node(
        WBSNodeDTO(name="Envelope", code="ENV", node_type=WBSNode.NodeType.SUMMARY),
        parent=root,
    )
    WBSVersionService.activate(version, actor=user)
    version.is_selected_for_analysis = True
    version.save(update_fields=["is_selected_for_analysis"])
    return dim, mset, [val], branch


def build_fixture(task_count: int, *, seed: int = BENCHMARK_SEED) -> GovernedE8BenchmarkFixture:
    """Synthetic governed E8 fixture — setup time excluded from benchmarks."""
    t0 = time.perf_counter()
    project = ProjectFactory()
    user = UserFactory()
    ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.XER,
        source_filename=f"gov-e8-bench-{task_count}.xer",
        status=ScheduleSourceVersion.Status.CURRENT,
        imported_at=timezone.now(),
        content_hash=f"gov-e8-{task_count}-{seed}",
    )

    trade_dim, trade_mset, trade_vals = _trade_setup(project, user)
    package_dim, package_mset, package_vals, wbs_branch = _package_setup(project, user)

    activities: list[ScheduleActivity] = []
    act_count = max(task_count // 10, 1)
    for i in range(act_count):
        activities.append(
            ScheduleActivity.objects.create(
                project=project,
                canonical_activity_key=f"bench-act-{i}",
                display_name=f"Activity {i}",
            )
        )

    tasks: list[Task] = []
    batch = 500
    for offset in range(0, task_count, batch):
        chunk = min(batch, task_count - offset)
        tasks.extend(
            TaskFactory.create_batch(
                chunk,
                project=project,
                start_date=DEFAULT_START,
                end_date=DEFAULT_END,
                cost=1000 + (offset % 100),
                physical_percent_complete=40,
                status="in_progress",
            )
        )

    electrical, concrete = trade_vals
    envelope = package_vals[0]
    trade_assignments: list[AnalyticalMappingAssignment] = []
    proposed_assignments: list[AnalyticalMappingAssignment] = []
    rejected_assignments: list[AnalyticalMappingAssignment] = []

    for idx, task in enumerate(tasks):
        mod = idx % 100
        if mod < 35:
            trade_assignments.append(
                AnalyticalMappingAssignment(
                    mapping_set=trade_mset,
                    dimension_value=electrical if mod < 20 else concrete,
                    target_type=AnalyticalMappingAssignment.TargetType.TASK,
                    task=task,
                    mapping_method=AnalyticalMappingAssignment.MappingMethod.MANUAL,
                    authority=AnalyticalMappingAssignment.MappingAuthority.APPROVED,
                    governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
                )
            )
        elif mod < 45:
            activity = activities[idx % len(activities)]
            task.schedule_activity = activity
            if mod < 40:
                trade_assignments.append(
                    AnalyticalMappingAssignment(
                        mapping_set=trade_mset,
                        dimension_value=electrical,
                        target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
                        schedule_activity=activity,
                        mapping_method=AnalyticalMappingAssignment.MappingMethod.IMPORTED,
                        authority=AnalyticalMappingAssignment.MappingAuthority.APPROVED,
                        governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
                    )
                )
        elif mod < 48:
            proposed_assignments.append(
                AnalyticalMappingAssignment(
                    mapping_set=trade_mset,
                    dimension_value=electrical,
                    target_type=AnalyticalMappingAssignment.TargetType.TASK,
                    task=task,
                    governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
                )
            )
        elif mod < 50:
            rejected_assignments.append(
                AnalyticalMappingAssignment(
                    mapping_set=trade_mset,
                    dimension_value=concrete,
                    target_type=AnalyticalMappingAssignment.TargetType.TASK,
                    task=task,
                    governance_status=AnalyticalMappingAssignment.GovernanceStatus.REJECTED,
                )
            )
        elif mod < 52 and idx < 5:
            trade_assignments.append(
                AnalyticalMappingAssignment(
                    mapping_set=trade_mset,
                    dimension_value=electrical,
                    target_type=AnalyticalMappingAssignment.TargetType.TASK,
                    task=task,
                    governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
                )
            )
            trade_assignments.append(
                AnalyticalMappingAssignment(
                    mapping_set=trade_mset,
                    dimension_value=concrete,
                    target_type=AnalyticalMappingAssignment.TargetType.TASK,
                    task=task,
                    governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
                )
            )
        if mod < 25:
            task.wbs_node = wbs_branch
        if mod < 15:
            TaskEntityBinding.objects.create(
                task=task,
                entity_global_id=f"GID-BENCH-{idx}",
                needs_review=(mod % 7 == 0),
                link_method=TaskEntityBinding.LinkMethod.MANUAL,
            )

    Task.objects.bulk_update(
        [t for t in tasks if t.schedule_activity_id or t.wbs_node_id],
        ["schedule_activity", "wbs_node"],
        batch_size=1000,
    )

    if trade_assignments:
        AnalyticalMappingAssignment.objects.bulk_create(trade_assignments, batch_size=1000)
    if proposed_assignments:
        AnalyticalMappingAssignment.objects.bulk_create(proposed_assignments, batch_size=1000)
    if rejected_assignments:
        AnalyticalMappingAssignment.objects.bulk_create(rejected_assignments, batch_size=1000)

    policy = WBSBranchMappingPolicyDTO(
        dimension_key="package",
        mapping_set_id=str(package_mset.pk),
        wbs_version_id=str(wbs_branch.wbs_version_id),
        wbs_node_id=str(wbs_branch.pk),
        dimension_value_id=str(envelope.pk),
        include_descendants=True,
        target_behavior="inherit_to_tasks",
    )
    WBSBranchMappingPolicyService(project).apply_policy(policy, actor=user, auto_approve=True)

    AnalyticalMappingSetService.activate(trade_mset, actor=user)
    AnalyticalMappingSetService.activate(package_mset, actor=user)

    elapsed = time.perf_counter() - t0
    return GovernedE8BenchmarkFixture(
        project=project,
        user=user,
        trade_dim=trade_dim,
        package_dim=package_dim,
        trade_mset=trade_mset,
        package_mset=package_mset,
        tasks=tasks,
        activities=activities,
        task_count=task_count,
        setup_seconds=elapsed,
    )


def _profile_stages(
    project, dimension_key: str = "trade"
) -> tuple[list[StageTiming], GovernedMappingAnalyticsSession]:
    stages: list[StageTiming] = []
    requested = {dimension_key: MODE_GOVERNED_PARTIAL}

    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        DimensionModeService(project).build(requested_modes=requested)
    stages.append(StageTiming("readiness_context", time.perf_counter() - t0, query_count=len(ctx)))

    resolver = EffectiveMappingResolver(project)
    dim = (
        AnalyticalDimension.objects.filter(project=project, dimension_key=dimension_key)
        .order_by("-revision_number")
        .first()
    )
    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        mset = resolver.active_mapping_set(dim) if dim else None
    stages.append(StageTiming("mapping_set_load", time.perf_counter() - t0, query_count=len(ctx)))

    task_ids = list(Task.objects.filter(project=project).values_list("pk", flat=True))
    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        if dim and mset:
            resolver.resolve_many_tasks(task_ids, dim)
    stages.append(
        StageTiming("effective_resolution", time.perf_counter() - t0, query_count=len(ctx))
    )

    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        session = GovernedMappingAnalyticsSession.load(
            project, dimension_keys=[dimension_key], requested_modes=requested
        )
    stages.append(StageTiming("session_load", time.perf_counter() - t0, query_count=len(ctx)))

    t0 = time.perf_counter()
    buckets = session.bucket_task_ids(dimension_key)
    stages.append(
        StageTiming(
            "bucket_classification",
            time.perf_counter() - t0,
            rows=sum(len(v) for v in buckets.values()),
        )
    )

    t0 = time.perf_counter()
    rollup = session.build_dimension_rollup(dimension_key)
    stages.append(StageTiming("rollup_aggregation", time.perf_counter() - t0, rows=len(rollup)))

    t0 = time.perf_counter()
    GovernedMappingReconciliationService(project).reconcile_dimension(session, dimension_key)
    stages.append(StageTiming("reconciliation", time.perf_counter() - t0))

    t0 = time.perf_counter()
    summary = GovernedMappingAggregationService(project).build_summary_from_session(
        session, dimension_key
    )
    stages.append(
        StageTiming(
            "serialization",
            time.perf_counter() - t0,
            rows=len(summary.get("governed_values", [])),
        )
    )

    total = sum(s.median_s for s in stages) or 1.0
    for s in stages:
        s.pct_total = round(100.0 * s.median_s / total, 1)

    return stages, session


def run_benchmark(task_count: int, *, repeats: int = 3) -> GovernedE8BenchmarkRun:
    """Run governed E8 benchmark excluding fixture setup."""
    fixture = build_fixture(task_count)
    project = fixture.project
    agg = GovernedMappingAggregationService(project)
    drill = GovernedMappingDrilldownService(project)
    requested = {"trade": MODE_GOVERNED_PARTIAL}

    summary_times: list[float] = []
    summary_qc: list[int] = []
    resolve_times: list[float] = []
    readiness_times: list[float] = []
    rollup_times: list[float] = []
    drill_value: list[float] = []
    drill_unmapped: list[float] = []
    drill_conflict: list[float] = []
    drill_qc: list[int] = []

    cold_t0 = time.perf_counter()
    agg.build_summary("trade", requested_mode=MODE_GOVERNED_PARTIAL)
    cold_summary = time.perf_counter() - cold_t0

    session: GovernedMappingAnalyticsSession | None = None
    value_rows = 0

    for _ in range(repeats):
        t0 = time.perf_counter()
        DimensionModeService(project).build(requested_modes=requested)
        readiness_times.append(time.perf_counter() - t0)

        resolver = EffectiveMappingResolver(project)
        ids = [t.pk for t in fixture.tasks]
        t0 = time.perf_counter()
        resolver.resolve_many_tasks(ids, fixture.trade_dim)
        resolve_times.append(time.perf_counter() - t0)

        with CaptureQueriesContext(connection) as ctx:
            t0 = time.perf_counter()
            session = GovernedMappingAnalyticsSession.load(
                project, dimension_keys=["trade"], requested_modes=requested
            )
            summary = agg.build_summary_from_session(session, "trade")
            elapsed = time.perf_counter() - t0
        summary_times.append(elapsed)
        summary_qc.append(len(ctx.captured_queries))
        value_rows = len(summary.get("governed_values", []))

        t0 = time.perf_counter()
        session.build_dimension_rollup("trade")
        rollup_times.append(time.perf_counter() - t0)

        sample_value = next(
            (
                r["value_id"]
                for r in summary.get("governed_values", [])
                if not r.get("is_virtual_bucket")
            ),
            None,
        )
        with CaptureQueriesContext(connection) as ctx:
            if sample_value:
                t0 = time.perf_counter()
                drill.tasks_for_value("trade", sample_value, requested_mode=MODE_GOVERNED_PARTIAL)
                drill_value.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            drill.unmapped_tasks("trade")
            drill_unmapped.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            drill.conflicts("trade")
            drill_conflict.append(time.perf_counter() - t0)
        drill_qc.append(len(ctx.captured_queries))

    stages, _ = _profile_stages(project)

    return GovernedE8BenchmarkRun(
        task_count=task_count,
        fixture_setup_s=fixture.setup_seconds,
        cold_summary_s=cold_summary,
        summary_median_s=statistics.median(summary_times),
        summary_min_s=min(summary_times),
        summary_max_s=max(summary_times),
        summary_query_count=int(statistics.median(summary_qc)),
        resolve_median_s=statistics.median(resolve_times),
        readiness_median_s=statistics.median(readiness_times),
        rollup_median_s=statistics.median(rollup_times),
        drilldown_value_median_s=statistics.median(drill_value) if drill_value else 0,
        drilldown_unmapped_median_s=statistics.median(drill_unmapped),
        drilldown_conflict_median_s=statistics.median(drill_conflict) if drill_conflict else 0,
        drilldown_query_count=int(statistics.median(drill_qc)) if drill_qc else 0,
        governed_value_rows=value_rows,
        stages=stages,
    )


if __name__ == "__main__":
    import os
    import sys

    import django

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()

    import json

    from scheduling.tests.governed_e8_benchmark_harness import run_benchmark

    out = {}
    for n in (1000, 5000, 10000):
        run = run_benchmark(n, repeats=3)
        out[str(n)] = {
            "fixture_setup_s": round(run.fixture_setup_s, 2),
            "cold_summary_s": round(run.cold_summary_s, 3),
            "summary_median_s": round(run.summary_median_s, 3),
            "resolve_median_s": round(run.resolve_median_s, 3),
            "rollup_median_s": round(run.rollup_median_s, 3),
            "summary_query_count": run.summary_query_count,
            "value_rows": run.governed_value_rows,
            "stages": [
                {
                    "stage": s.name,
                    "median_s": round(s.median_s, 4),
                    "pct": s.pct_total,
                    "queries": s.query_count,
                    "rows": s.rows,
                }
                for s in run.stages
            ],
        }
    print(json.dumps(out, indent=2))
