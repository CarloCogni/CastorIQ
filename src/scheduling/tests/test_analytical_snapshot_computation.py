# scheduling/tests/test_analytical_snapshot_computation.py
"""DF-B2 snapshot computation, persistence, comparison, and report freeze."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotResult,
    BaselineVersion,
    ScheduleSourceVersion,
    Task,
)
from scheduling.services.analytical_snapshot.comparison import AnalyticalSnapshotComparisonService
from scheduling.services.analytical_snapshot.computation import (
    AnalyticalSnapshotComputationService,
    SnapshotComputationError,
)
from scheduling.services.analytical_snapshot.exceptions import SnapshotTransitionError
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.analytical_snapshot.report_data import SnapshotReportDataProvider
from scheduling.services.analytical_snapshot.result_hash import build_result_content_hash
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.evm import compute_evm
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _source_version(project, **kwargs):
    defaults = {
        "project": project,
        "version_number": 1,
        "source_type": Task.Source.XER,
        "source_filename": "test.xer",
        "status": ScheduleSourceVersion.Status.CURRENT,
        "imported_at": timezone.now(),
        "content_hash": "abc123",
    }
    defaults.update(kwargs)
    return ScheduleSourceVersion.objects.create(**defaults)


def _request_snapshot(project, user, **kwargs):
    defaults = {
        "project": project,
        "name": "Checkpoint",
        "snapshot_type": AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
        "actor": user,
        "force": True,
    }
    defaults.update(kwargs)
    return AnalyticalSnapshotService.request_snapshot(**defaults)


def _compute(project, user, **kwargs):
    snap = _request_snapshot(project, user, **kwargs)
    return AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=user)


@pytest.mark.django_db
class TestSnapshotResultModels:
    def test_result_one_to_one_and_immutable(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        result = _compute(project, user)
        assert result.snapshot.result.pk == result.pk
        result.spi = Decimal("9")
        with pytest.raises(ValueError):
            result.save()

    def test_series_and_period_uniqueness(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        _compute(project, user)
        snap = AnalyticalSnapshot.objects.filter(project=project).first()
        assert snap.series_points.exists()
        assert snap.periods.exists()

    def test_result_hash_deterministic(self):
        payload = {"schema_version": "v1", "pv": 1.0, "ev": 2.0, "kpi_payload": {}}
        assert build_result_content_hash(payload) == build_result_content_hash(payload)


@pytest.mark.django_db
class TestSnapshotComputationLifecycle:
    def test_successful_computation_persists_result_and_series(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(
            project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1), cost=100
        )
        _source_version(project)
        result = _compute(project, user)
        snap = result.snapshot
        assert snap.status == AnalyticalSnapshot.Status.COMPLETED
        assert result.pv is not None or result.spi is not None
        assert snap.series_points.count() > 0

    def test_duplicate_computation_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _compute(project, user).snapshot
        with pytest.raises(Exception):
            AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=user)

    def test_published_recalculation_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _compute(project, user).snapshot
        AnalyticalSnapshotService.publish(snap, actor=user)
        with pytest.raises(SnapshotTransitionError):
            AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=user)

    def test_failed_computation_no_result(self):
        from unittest.mock import patch

        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _request_snapshot(project, user)
        with patch.object(
            AnalyticalSnapshotComputationService,
            "_gather_analytics",
            side_effect=RuntimeError("provider failure"),
        ):
            with pytest.raises(SnapshotComputationError):
                AnalyticalSnapshotComputationService.compute_and_persist(snap, actor=user)
        snap.refresh_from_db()
        assert snap.status == AnalyticalSnapshot.Status.FAILED
        assert not AnalyticalSnapshotResult.objects.filter(snapshot=snap).exists()


@pytest.mark.django_db
class TestSnapshotImmutabilityAfterLiveChanges:
    def test_result_unchanged_after_task_and_baseline_changes(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(
            project=project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 1),
            cost=50,
        )
        sv = _source_version(project)
        bl = BaselineVersionService.create_draft(
            project=project,
            name="BL",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
            source_version=sv,
        )
        BaselineVersionService.publish(bl, actor=user)
        BaselineVersionService.select_for_analysis(bl, actor=user)
        result = _compute(project, user)
        original_hash = result.content_hash
        original_spi = result.spi
        Task.objects.filter(project=project).update(cost=9999)
        bl2 = BaselineVersionService.create_draft(
            project=project,
            name="Other",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(bl2, actor=user)
        BaselineVersionService.select_for_analysis(bl2, actor=user)
        result.refresh_from_db()
        assert result.content_hash == original_hash
        assert result.spi == original_spi


@pytest.mark.django_db
class TestHistoricalAndComparison:
    def test_one_result_historical_unavailable(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        _compute(project, user)
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["snapshot_capabilities"]["historical_evm"]["available"] is False

    def test_two_compatible_snapshots_comparable(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        s1 = _compute(project, user, name="A").snapshot
        s2 = _compute(project, user, name="B").snapshot
        payload = AnalyticalSnapshotComparisonService(s1, s2).compare()
        assert payload["verdict"] in ("comparable", "comparable_with_caveats")

    def test_kpi_payload_uses_evm_ids(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        result = _compute(project, user)
        assert "evm.spi" in result.kpi_payload or result.spi is None


@pytest.mark.django_db
class TestReportFreeze:
    def test_report_data_reads_persisted_values(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _compute(
            project,
            user,
            snapshot_type=AnalyticalSnapshot.SnapshotType.REPORT_FREEZE,
        ).snapshot
        data = SnapshotReportDataProvider(snap).build()
        assert data["persisted"] is True
        assert data["live_recompute"] is False
        assert data["result_hash"]

    def test_report_data_requires_result(self):
        project = ProjectFactory()
        user = UserFactory()
        snap = _request_snapshot(project, user)
        with pytest.raises(ValueError):
            SnapshotReportDataProvider(snap).build()


@pytest.mark.django_db
class TestSnapshotComputationAPI:
    def test_result_series_periods_compare_report_get(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _compute(project, user).snapshot
        pk = project.pk
        assert (
            client.get(
                reverse(
                    "scheduling:schedule_analytical_snapshot_result",
                    kwargs={"pk": pk, "snapshot_pk": snap.pk},
                )
            ).status_code
            == 200
        )
        assert client.get(
            reverse(
                "scheduling:schedule_analytical_snapshot_series",
                kwargs={"pk": pk, "snapshot_pk": snap.pk},
            )
        ).json()["items"]
        assert client.get(
            reverse(
                "scheduling:schedule_analytical_snapshot_periods",
                kwargs={"pk": pk, "snapshot_pk": snap.pk},
            )
        ).json()["items"]
        assert client.get(
            reverse(
                "scheduling:schedule_analytical_snapshot_report_data",
                kwargs={"pk": pk, "snapshot_pk": snap.pk},
            )
        ).json()["report_data"]["result_hash"]
        assert (
            client.post(
                reverse(
                    "scheduling:schedule_analytical_snapshot_result",
                    kwargs={"pk": pk, "snapshot_pk": snap.pk},
                )
            ).status_code
            == 405
        )

    def test_repeated_get_no_writes(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        snap = _compute(project, user).snapshot
        url = reverse(
            "scheduling:schedule_analytical_snapshot_result",
            kwargs={"pk": project.pk, "snapshot_pk": snap.pk},
        )
        before = AnalyticalSnapshotResult.objects.count()
        client.get(url)
        client.get(url)
        assert AnalyticalSnapshotResult.objects.count() == before


@pytest.mark.django_db
class TestE8SnapshotReadMode:
    def test_e8_current_reads_persisted_snapshot(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        TaskFactory(
            project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1), cost=100
        )
        _source_version(project)
        snap = _compute(project, user).snapshot
        url = reverse("scheduling:executive_controls_evm_current", kwargs={"pk": project.pk})
        resp = client.get(url, {"snapshot_id": str(snap.pk)})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["persisted"] is True
        assert payload["live_recompute"] is False
        assert payload["result_hash"]
        assert payload["snapshot_id"] == str(snap.pk)


@pytest.mark.django_db
class TestSnapshotLiveEvmUnchanged:
    def test_compute_evm_unchanged_by_snapshot_writes(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        _source_version(project)
        before = compute_evm(str(project.pk))
        _compute(project, user)
        after = compute_evm(str(project.pk))
        assert before.get("spi") == after.get("spi")


@pytest.mark.django_db
class TestSnapshotPerformance:
    @pytest.mark.slow
    def test_large_project_computation_bounded(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        TaskFactory.create_batch(
            200,
            project=project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        import time

        start = time.perf_counter()
        result = _compute(project, user)
        elapsed = time.perf_counter() - start
        assert result.snapshot.series_points.count() > 0
        assert elapsed < 30.0
        with CaptureQueriesContext(connection) as ctx:
            AnalyticalSnapshotResult.objects.select_related("snapshot").get(pk=result.pk)
        assert len(ctx) <= 3

    @pytest.mark.slow
    def test_tenk_task_synthetic_benchmark(self):
        """DF-B2 formal 10k-task benchmark — documents dev-machine timing."""
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        TaskFactory.create_batch(
            10_000,
            project=project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            cost=100,
        )
        import time

        t0 = time.perf_counter()
        result = _compute(project, user)
        compute_elapsed = time.perf_counter() - t0

        t1 = time.perf_counter()
        AnalyticalSnapshotResult.objects.select_related("snapshot").get(pk=result.pk)
        result_fetch_elapsed = time.perf_counter() - t1

        t2 = time.perf_counter()
        series_count = result.snapshot.series_points.count()
        series_fetch_elapsed = time.perf_counter() - t2

        t3 = time.perf_counter()
        SnapshotReportDataProvider(result.snapshot).build()
        report_elapsed = time.perf_counter() - t3

        assert series_count > 0
        assert compute_elapsed < 20.0, f"10k single-run compute {compute_elapsed:.2f}s"
        assert result_fetch_elapsed < 0.3
        assert report_elapsed < 1.0
        assert series_fetch_elapsed < 0.5
