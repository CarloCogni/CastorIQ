# scheduling/tests/snapshot_benchmark_harness.py
"""Reproducible DF-B2.1 snapshot computation benchmark harness."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotResult,
    ScheduleSourceVersion,
    Task,
)
from scheduling.services.analytical_snapshot.computation import AnalyticalSnapshotComputationService
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.analytical_snapshot.snapshot_evm_session import SnapshotEVMComputeSession
from scheduling.tests.factories import TaskFactory

BENCHMARK_SEED = 42
DEFAULT_START = date(2025, 1, 1)
DEFAULT_END = date(2025, 12, 31)


@dataclass
class BenchmarkFixture:
    """Synthetic project ready for snapshot computation."""

    project: Any
    user: Any
    task_count: int
    setup_seconds: float = 0.0


@dataclass
class StageTiming:
    name: str
    seconds: float
    query_count: int = 0


@dataclass
class BenchmarkRun:
    task_count: int
    fixture_setup_s: float
    compute_median_s: float
    compute_min_s: float
    compute_max_s: float
    query_count: int
    series_points: int
    period_rows: int
    result_hash: str
    stages: list[StageTiming] = field(default_factory=list)


def build_fixture(task_count: int, *, seed: int = BENCHMARK_SEED) -> BenchmarkFixture:
    """Create deterministic synthetic project (excluded from compute timing)."""
    import random

    rng = random.Random(seed)
    t0 = time.perf_counter()
    project = ProjectFactory()
    user = UserFactory()
    ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.XER,
        source_filename="bench.xer",
        status=ScheduleSourceVersion.Status.CURRENT,
        imported_at=timezone.now(),
        content_hash=f"bench-{task_count}-{seed}",
    )
    batch_size = 500
    for offset in range(0, task_count, batch_size):
        chunk = min(batch_size, task_count - offset)
        TaskFactory.create_batch(
            chunk,
            project=project,
            start_date=DEFAULT_START,
            end_date=DEFAULT_END,
            cost=100 + (offset % 50),
            physical_percent_complete=rng.randint(1, 100) / 100.0,
        )
    elapsed = time.perf_counter() - t0
    return BenchmarkFixture(
        project=project, user=user, task_count=task_count, setup_seconds=elapsed
    )


def _request_snapshot(project, user) -> AnalyticalSnapshot:
    return AnalyticalSnapshotService.request_snapshot(
        project=project,
        name=f"Bench {project.pk}",
        snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
        actor=user,
        force=True,
    )


def run_compute_once(project, user) -> tuple[float, int, AnalyticalSnapshotResult]:
    """Time one full compute_and_persist; return seconds, query count, result."""
    snap = _request_snapshot(project, user)
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        result = AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=user)
        elapsed = time.perf_counter() - t0
    return elapsed, len(ctx.captured_queries), result


def run_benchmark(task_count: int, *, repeats: int = 3) -> BenchmarkRun:
    """Run cold + warm measurements; fixture excluded from compute timing."""
    fixture = build_fixture(task_count)
    timings: list[float] = []
    query_counts: list[int] = []
    result: AnalyticalSnapshotResult | None = None
    for _ in range(repeats):
        elapsed, qc, result = run_compute_once(fixture.project, fixture.user)
        timings.append(elapsed)
        query_counts.append(qc)
    assert result is not None
    snap = result.snapshot
    return BenchmarkRun(
        task_count=task_count,
        fixture_setup_s=fixture.setup_seconds,
        compute_median_s=statistics.median(timings),
        compute_min_s=min(timings),
        compute_max_s=max(timings),
        query_count=int(statistics.median(query_counts)),
        series_points=snap.series_points.count(),
        period_rows=snap.periods.count(),
        result_hash=result.content_hash,
    )


def profile_compute_breakdown(task_count: int = 10_000) -> dict[str, float]:
    """Stage timings for one compute (fixture excluded)."""
    from scheduling.services.analytical_snapshot.persistence import persist_snapshot_analytics
    from scheduling.services.executive_controls.capability_profile import (
        ProjectAnalyticsCapabilityProfile,
    )
    from scheduling.services.executive_controls.current_evm_analytics import (
        CurrentEVMAnalyticsService,
    )
    from scheduling.services.executive_controls.derived_asof_scurve import DerivedAsOfSCurveService
    from scheduling.services.executive_controls.evm_filters import EVMFilters

    fixture = build_fixture(task_count)
    snap = _request_snapshot(fixture.project, fixture.user)
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    if snap.status == AnalyticalSnapshot.Status.REQUESTED:
        AnalyticalSnapshotService.begin_calculation(snap, actor=fixture.user)
        snap.refresh_from_db()
    timings["begin_calculation"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    capability = ProjectAnalyticsCapabilityProfile(snap.project).build()
    timings["capability_profile"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    AnalyticalSnapshotComputationService._validate_repeatability(snap)
    timings["repeatability"] = time.perf_counter() - t0

    session = SnapshotEVMComputeSession(
        str(snap.project_id),
        as_of_date=snap.as_of_date,
        baseline_version_id=str(snap.baseline_version_id) if snap.baseline_version_id else None,
    )
    filters = EVMFilters.from_params({"granularity": "weekly", "page": "1", "page_size": "500"})

    t0 = time.perf_counter()
    evm_raw = session.evm()
    timings["compute_evm"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    evm_point = CurrentEVMAnalyticsService(
        snap.project, capability_profile=capability, session=session
    ).build()
    timings["evm_point"] = time.perf_counter() - t0

    curve_svc = DerivedAsOfSCurveService(
        snap.project, capability_profile=capability, session=session
    )
    t0 = time.perf_counter()
    scurve = curve_svc.build_scurve(filters)
    timings["scurve"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    periods = curve_svc.build_periods(filters)
    timings["periods"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    point = AnalyticalSnapshotComputationService._extract_point_metrics(evm_point, evm_raw)
    series_rows = AnalyticalSnapshotComputationService._build_series_rows(scurve)
    period_rows = AnalyticalSnapshotComputationService._build_period_rows(periods)
    timings["serialize_rows"] = time.perf_counter() - t0

    result_data = {
        **point,
        "schema_version": AnalyticalSnapshotResult.SCHEMA_VERSION,
        "currency": "",
        "historical_authority": False,
        "caveats": [],
        "kpi_payload": {},
        "engine_metadata": {},
    }

    t0 = time.perf_counter()
    with transaction.atomic():
        persist_snapshot_analytics(
            snapshot=snap,
            result_data=result_data,
            series_rows=series_rows,
            period_rows=period_rows,
        )
        AnalyticalSnapshotService.complete_manifest(snap, actor=fixture.user)
    timings["persist"] = time.perf_counter() - t0
    timings["series_rows"] = float(len(series_rows))
    timings["period_rows"] = float(len(period_rows))
    timings["total"] = sum(v for k, v in timings.items() if k not in ("series_rows", "period_rows"))
    return timings


def capture_reference(task_count: int = 1000) -> dict[str, Any]:
    """Deterministic reference outputs for equivalence checks."""
    fixture = build_fixture(task_count, seed=BENCHMARK_SEED)
    _, _, result = run_compute_once(fixture.project, fixture.user)
    snap = result.snapshot
    return {
        "task_count": task_count,
        "content_hash": result.content_hash,
        "pv": float(result.pv) if result.pv is not None else None,
        "ev": float(result.ev) if result.ev is not None else None,
        "ac": float(result.ac) if result.ac is not None else None,
        "bac": float(result.bac) if result.bac is not None else None,
        "spi": float(result.spi) if result.spi is not None else None,
        "cpi": float(result.cpi) if result.cpi is not None else None,
        "series_count": snap.series_points.count(),
        "period_count": snap.periods.count(),
        "methodology_mode": result.methodology_mode,
    }
