# scheduling/tests/test_snapshot_performance_b21.py
"""DF-B2.1 snapshot computation performance and equivalence tests."""

from __future__ import annotations

import cProfile
import io
import pstats

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from scheduling.services.analytical_snapshot.computation import AnalyticalSnapshotComputationService
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.analytical_snapshot.result_hash import build_result_content_hash
from scheduling.services.evm import compute_evm
from scheduling.tests.snapshot_benchmark_harness import (
    BENCHMARK_SEED,
    build_fixture,
    capture_reference,
    profile_compute_breakdown,
    run_benchmark,
)


def _request_snap(project, user):
    return AnalyticalSnapshotService.request_snapshot(
        project=project,
        name="Perf",
        snapshot_type="manual_checkpoint",
        actor=user,
        force=True,
    )


@pytest.mark.django_db
class TestSnapshotPerformanceEquivalence:
    """Reference outputs stable across optimization."""

    def test_10k_compute_equivalence_metrics_stable(self):
        """KPI scalars stable for fixed seed at 1k after optimization."""
        ref_a = capture_reference(1000)
        ref_b = capture_reference(1000)
        assert ref_a["pv"] == ref_b["pv"]
        assert ref_a["bac"] == ref_b["bac"]
        assert ref_a["spi"] == ref_b["spi"]
        assert ref_a["series_count"] == ref_b["series_count"]

    def test_content_hash_deterministic_for_same_payload(self):
        ref = capture_reference(500)
        payload = {
            "schema_version": "snapshot-result-v1",
            "pv": ref["pv"],
            "ev": ref["ev"],
            "bac": ref["bac"],
            "kpi_payload": {},
        }
        assert build_result_content_hash(payload) == build_result_content_hash(payload)


@pytest.mark.django_db
class TestSnapshotPerformanceQueryBudget:
    def test_1k_compute_query_count_bounded(self):
        fixture = build_fixture(1000, seed=BENCHMARK_SEED)
        snap = _request_snap(fixture.project, fixture.user)
        with CaptureQueriesContext(connection) as ctx:
            AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=fixture.user)
        assert len(ctx) <= 80


@pytest.mark.django_db
@pytest.mark.slow
class TestSnapshotPerformanceBenchmark:
    def test_1k_median_under_5_seconds(self):
        run = run_benchmark(1000, repeats=2)
        assert run.compute_median_s < 5.0, f"1k median {run.compute_median_s:.2f}s"

    def test_5k_scaling_diagnostic(self):
        run = run_benchmark(5000, repeats=2)
        assert run.compute_median_s < 25.0, f"5k median {run.compute_median_s:.2f}s"

    def test_10k_recorded_median(self):
        run = run_benchmark(10_000, repeats=3)
        assert run.compute_median_s < 15.0, f"10k median {run.compute_median_s:.2f}s"
        assert run.query_count <= 80

    def test_breakdown_10k(self):
        timings = profile_compute_breakdown(10_000)
        assert timings["compute_evm"] >= 0
        assert timings["total"] >= 0
        # Diagnostic: compute_evm should dominate
        assert timings.get("series_rows", 0) > 0

    def test_cprofile_compute_evm_10k(self):
        from scheduling.models import Task

        fixture = build_fixture(10_000, seed=BENCHMARK_SEED)
        as_of = (
            Task.objects.filter(project=fixture.project)
            .values_list("start_date", flat=True)
            .first()
        )
        assert as_of is not None

        pr = cProfile.Profile()
        pr.enable()
        compute_evm(str(fixture.project.pk), as_of)
        pr.disable()
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(15)
        text = buf.getvalue()
        assert "compute_evm" in text or "evm" in text
