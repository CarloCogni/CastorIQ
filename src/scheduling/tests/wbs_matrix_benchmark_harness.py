# scheduling/tests/wbs_matrix_benchmark_harness.py
"""Deterministic WBS matrix benchmark harness (DF-C3.1)."""

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
from scheduling.models import ScheduleSourceVersion, Task, WBSNode
from scheduling.services.executive_controls.hierarchy_mode import HierarchyModeResolver
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.wbs_analytics_session import WBSAnalyticsSession
from scheduling.services.executive_controls.wbs_drilldown import WBSDrilldownService
from scheduling.services.executive_controls.wbs_matrix import WBSMatrixService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory

BENCHMARK_SEED = 42
DEFAULT_START = date(2025, 1, 1)
DEFAULT_END = date(2025, 12, 31)
BRANCHING = 10
DEPTH = 4


@dataclass
class WBSBenchmarkFixture:
    project: Any
    user: Any
    version: Any
    root: WBSNode
    leaf_nodes: list[WBSNode]
    task_count: int
    setup_seconds: float = 0.0


@dataclass
class WBSBenchmarkRun:
    task_count: int
    node_count: int
    fixture_setup_s: float
    session_load_median_s: float
    matrix_median_s: float
    matrix_min_s: float
    matrix_max_s: float
    matrix_query_count: int
    drilldown_median_s: float
    drilldown_query_count: int
    stages: list[dict[str, float]] = field(default_factory=list)


def _build_wbs_tree(
    version, *, branching: int = BRANCHING, depth: int = DEPTH
) -> tuple[WBSNode, list[WBSNode]]:
    svc = WBSHierarchyService(version)
    root = svc.create_node(
        WBSNodeDTO(name="Root", external_id="bench-root", code="1", node_type=WBSNode.NodeType.ROOT)
    )
    leaves: list[WBSNode] = []
    frontier = [root]
    for level in range(1, depth + 1):
        next_frontier: list[WBSNode] = []
        for parent in frontier:
            for i in range(branching):
                child = svc.create_node(
                    WBSNodeDTO(
                        name=f"L{level}-{i}",
                        external_id=f"l{level}-{parent.pk}-{i}",
                        code=f"{level}.{i}",
                        parent_id=parent.pk,
                    ),
                    parent=parent,
                )
                if level == depth:
                    leaves.append(child)
                else:
                    next_frontier.append(child)
        frontier = next_frontier
    return root, leaves


def build_fixture(
    task_count: int, *, partial: bool = False, seed: int = BENCHMARK_SEED
) -> WBSBenchmarkFixture:
    """Synthetic WBS + tasks (timing excludes this setup)."""
    t0 = time.perf_counter()
    project = ProjectFactory()
    user = UserFactory()
    ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.XER,
        source_filename="wbs_bench.xer",
        status=ScheduleSourceVersion.Status.CURRENT,
        imported_at=timezone.now(),
        content_hash=f"wbs-bench-{task_count}-{seed}",
    )
    version = WBSVersionService.create_draft(
        project=project,
        name="Benchmark WBS",
        source_version=ScheduleSourceVersion.objects.filter(project=project).first(),
        actor=user,
    )
    root, leaves = _build_wbs_tree(version)
    WBSVersionService.activate(version, actor=user)

    batch_size = 500
    tasks: list[Task] = []
    for offset in range(0, task_count, batch_size):
        chunk = min(batch_size, task_count - offset)
        tasks.extend(
            TaskFactory.create_batch(
                chunk,
                project=project,
                start_date=DEFAULT_START,
                end_date=DEFAULT_END,
                cost=100 + (offset % 50),
                stage="structure",
            )
        )

    assign_count = int(task_count * 0.9) if partial else task_count
    leaf_cycle = leaves or [root]
    to_assign = tasks[:assign_count]
    for idx, task in enumerate(to_assign):
        task.wbs_node = leaf_cycle[idx % len(leaf_cycle)]
    Task.objects.bulk_update(to_assign, ["wbs_node"], batch_size=1000)

    elapsed = time.perf_counter() - t0
    return WBSBenchmarkFixture(
        project=project,
        user=user,
        version=version,
        root=root,
        leaf_nodes=leaves or [root],
        task_count=task_count,
        setup_seconds=elapsed,
    )


def _time_session_load(project) -> tuple[float, int, WBSAnalyticsSession]:
    hierarchy = HierarchyModeResolver(project).resolve()
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        session = WBSAnalyticsSession.load(project, hierarchy)
        elapsed = time.perf_counter() - t0
    return elapsed, len(ctx.captured_queries), session


def _time_matrix(project, session: WBSAnalyticsSession) -> tuple[float, int]:
    filters = ExecutiveMatrixFilters()
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        WBSMatrixService(project, session).build_rows(filters)
        elapsed = time.perf_counter() - t0
    return elapsed, len(ctx.captured_queries)


def _time_drilldown(project, session: WBSAnalyticsSession, node_pk: str) -> tuple[float, int]:
    filters = ExecutiveMatrixFilters()
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        WBSDrilldownService(project, session).task_list(node_pk, filters)
        elapsed = time.perf_counter() - t0
    return elapsed, len(ctx.captured_queries)


def run_benchmark(task_count: int, *, repeats: int = 3, partial: bool = False) -> WBSBenchmarkRun:
    fixture = build_fixture(task_count, partial=partial)
    session_times: list[float] = []
    matrix_times: list[float] = []
    matrix_qc: list[int] = []
    drill_times: list[float] = []
    drill_qc: list[int] = []
    session: WBSAnalyticsSession | None = None

    for _ in range(repeats):
        s_elapsed, _, session = _time_session_load(fixture.project)
        session_times.append(s_elapsed)
        (
            m_elapsed,
            m_q,
        ) = _time_matrix(fixture.project, session)
        matrix_times.append(m_elapsed)
        matrix_qc.append(m_q)
        node_pk = str(fixture.leaf_nodes[0].pk)
        d_elapsed, d_q = _time_drilldown(fixture.project, session, node_pk)
        drill_times.append(d_elapsed)
        drill_qc.append(d_q)

    node_count = WBSNode.objects.filter(wbs_version=fixture.version).count()
    return WBSBenchmarkRun(
        task_count=task_count,
        node_count=node_count,
        fixture_setup_s=fixture.setup_seconds,
        session_load_median_s=statistics.median(session_times),
        matrix_median_s=statistics.median(matrix_times),
        matrix_min_s=min(matrix_times),
        matrix_max_s=max(matrix_times),
        matrix_query_count=int(statistics.median(matrix_qc)),
        drilldown_median_s=statistics.median(drill_times),
        drilldown_query_count=int(statistics.median(drill_qc)),
    )
