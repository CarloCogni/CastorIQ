# scheduling/tests/test_analytical_snapshot_manifest.py
"""DF-B1 analytical snapshot manifest — models, lifecycle, fingerprints, API."""

from __future__ import annotations

import time
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotAuditEvent,
    BaselineVersion,
    ScheduleSourceVersion,
    Task,
)
from scheduling.services.analytical_snapshot.exceptions import (
    SnapshotTransitionError,
    SnapshotValidationError,
)
from scheduling.services.analytical_snapshot.fingerprint import (
    build_input_fingerprint,
    build_scope_fingerprint,
    canonical_json,
)
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.evm import compute_evm
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _source_version(project, user=None, status=ScheduleSourceVersion.Status.CURRENT, **kwargs):
    defaults = {
        "project": project,
        "version_number": 1,
        "source_type": Task.Source.XER,
        "source_filename": "test.xer",
        "status": status,
        "imported_at": timezone.now(),
        "created_by": user,
        "content_hash": "abc123",
    }
    defaults.update(kwargs)
    return ScheduleSourceVersion.objects.create(**defaults)


def _approved_baseline(project, user, source=None):
    baseline = BaselineVersionService.create_draft(
        project=project,
        name="Approved BL",
        baseline_type=BaselineVersion.BaselineType.APPROVED,
        source_version=source,
    )
    BaselineVersionService.publish(baseline, actor=user)
    BaselineVersionService.select_for_analysis(baseline, actor=user)
    return baseline


def _pipeline(project, user, **kwargs):
    defaults = {
        "project": project,
        "name": "Checkpoint",
        "snapshot_type": AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
        "actor": user,
    }
    defaults.update(kwargs)
    snap = AnalyticalSnapshotService.request_snapshot(**defaults)
    if snap.status in (AnalyticalSnapshot.Status.COMPLETED, AnalyticalSnapshot.Status.PUBLISHED):
        return snap
    AnalyticalSnapshotService.begin_calculation(snap, actor=user)
    return AnalyticalSnapshotService.complete_manifest(snap, actor=user)


@pytest.mark.django_db
class TestSnapshotModels:
    def test_snapshot_type_status_choices(self):
        assert AnalyticalSnapshot.SnapshotType.REPORT_FREEZE
        assert AnalyticalSnapshot.Status.PUBLISHED

    def test_cross_project_source_rejected_by_service(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        sv = _source_version(p1)
        with pytest.raises(SnapshotValidationError):
            AnalyticalSnapshotService._validate_source(p2.pk, sv)

    def test_same_project_supersession(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        s1 = _pipeline(project, user, name="First")
        AnalyticalSnapshotService.publish(s1, actor=user)
        _pipeline(project, user, name="Second", force=True)
        AnalyticalSnapshotService.supersede(s1, actor=user)
        s1.refresh_from_db()
        assert s1.status == AnalyticalSnapshot.Status.SUPERSEDED

    def test_sequence_constraint(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        s1 = _pipeline(project, user)
        s2 = _pipeline(project, user, name="Two", force=True)
        assert s1.sequence_number != s2.sequence_number

    def test_published_provenance_immutable(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        snap = _pipeline(project, user)
        AnalyticalSnapshotService.publish(snap, actor=user)
        snap.refresh_from_db()
        snap.input_fingerprint = "x" * 64
        with pytest.raises(ValueError):
            snap.save()

    def test_completed_provenance_immutable(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        snap = _pipeline(project, user)
        snap.methodology_version = "changed"
        with pytest.raises(ValueError):
            snap.save()

    def test_failed_snapshot_cannot_publish(self):
        project = ProjectFactory()
        user = UserFactory()
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Fail",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        AnalyticalSnapshotService.mark_failed(snap, actor=user, reason="test failure")
        with pytest.raises(SnapshotTransitionError):
            AnalyticalSnapshotService.publish(snap, actor=user)


@pytest.mark.django_db
class TestSnapshotLifecycle:
    def test_request_with_source_and_baseline(self):
        project = ProjectFactory()
        user = UserFactory()
        sv = _source_version(project)
        bl = _approved_baseline(project, user, source=sv)
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Full",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        assert snap.source_version_id == sv.pk
        assert snap.baseline_version_id == bl.pk

    def test_request_without_baseline(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="No BL",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        assert snap.baseline_version_id is None

    def test_manifest_only_legacy_project(self):
        project = ProjectFactory()
        user = UserFactory()
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Legacy",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        assert snap.repeatability_status == AnalyticalSnapshot.RepeatabilityStatus.MANIFEST_ONLY

    def test_begin_complete_fail_publish_supersede_archive(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Flow",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        AnalyticalSnapshotService.begin_calculation(snap, actor=user)
        AnalyticalSnapshotService.complete_manifest(snap, actor=user)
        snap.refresh_from_db()
        assert snap.status == AnalyticalSnapshot.Status.COMPLETED
        AnalyticalSnapshotService.publish(snap, actor=user)
        AnalyticalSnapshotService.supersede(snap, actor=user)
        snap.refresh_from_db()
        assert snap.status == AnalyticalSnapshot.Status.SUPERSEDED

    def test_invalid_transition_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        snap = _pipeline(project, user)
        with pytest.raises(SnapshotTransitionError):
            AnalyticalSnapshotService.begin_calculation(snap, actor=user)

    def test_actor_timestamps_recorded(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        snap = _pipeline(project, user)
        assert snap.calculation_completed_at
        assert AnalyticalSnapshotAuditEvent.objects.filter(snapshot=snap).exists()


@pytest.mark.django_db
class TestSnapshotFingerprints:
    def _base_kwargs(self, project_id: str):
        return dict(
            project_id=project_id,
            source_version_id="sv1",
            source_content_hash="hash1",
            baseline_version_id="bl1",
            baseline_revision=1,
            data_date="2025-06-01",
            as_of_date="2025-06-01",
            methodology_version=E8_METHODOLOGY_VERSION,
            capability_profile_version="project-analytics-capabilities-v1",
            trust_policy_version="trusted-binding-v1",
            calculation_engine_version="manifest-v1",
            methodology_mode="derived_current_schedule_evm",
            trust_binding_fingerprint="bind1",
        )

    def test_deterministic_same_input(self):
        kwargs = self._base_kwargs("p1")
        assert build_input_fingerprint(**kwargs) == build_input_fingerprint(**kwargs)

    def test_different_source_baseline_methodology_scope(self):
        k1 = self._base_kwargs("p1")
        assert build_input_fingerprint(**k1) != build_input_fingerprint(
            **{**k1, "source_version_id": "sv2"}
        )
        assert build_input_fingerprint(**k1) != build_input_fingerprint(
            **{**k1, "baseline_version_id": "bl2"}
        )
        assert build_input_fingerprint(**k1) != build_input_fingerprint(
            **{**k1, "methodology_version": "e8-v2"}
        )
        assert build_scope_fingerprint(filter_context={"a": 1}) != build_scope_fingerprint(
            filter_context={"b": 2}
        )

    def test_key_ordering_and_no_timestamp(self):
        payload = {"z": 1, "a": 2}
        assert canonical_json(payload) == canonical_json({"a": 2, "z": 1})
        fp = build_scope_fingerprint(filter_context={"x": 1, "requested_at": "now"})
        assert "now" not in fp


@pytest.mark.django_db
class TestSnapshotIdempotency:
    def test_duplicate_active_and_completed(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        s1 = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="One",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        s2 = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Two",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        assert s1.pk == s2.pk
        done = _pipeline(project, user)
        again = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Dup",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        assert again.pk == done.pk

    def test_force_creates_new_revision(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        s1 = _pipeline(project, user)
        s2 = _pipeline(project, user, name="Forced", force=True)
        assert s1.pk != s2.pk


@pytest.mark.django_db
class TestSnapshotProvenance:
    def test_provenance_fields_and_immutability(self):
        project = ProjectFactory()
        user = UserFactory()
        sv = _source_version(project, content_hash="deadbeef", data_date=date(2025, 5, 15))
        bl = _approved_baseline(project, user, source=sv)
        snap = _pipeline(project, user)
        assert snap.source_version_id == sv.pk
        assert snap.baseline_version_id == bl.pk
        assert snap.methodology_version == E8_METHODOLOGY_VERSION
        assert snap.trust_policy_version == "trusted-binding-v1"
        original_fp = snap.input_fingerprint
        sv.status = ScheduleSourceVersion.Status.SUPERSEDED
        sv.save(update_fields=["status"])
        snap.refresh_from_db()
        assert snap.input_fingerprint == original_fp
        assert snap.baseline_version_id == bl.pk

    def test_failed_summary_sanitized(self):
        project = ProjectFactory()
        user = UserFactory()
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Fail",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        AnalyticalSnapshotService.mark_failed(
            snap, actor=user, reason=r"D:\Projects\secret\error.txt"
        )
        snap.refresh_from_db()
        assert "secret" not in snap.failure_summary.lower()
        assert "\\" not in snap.failure_summary
        assert "[path]" in snap.failure_summary or "[drive]" in snap.failure_summary


@pytest.mark.django_db
class TestSnapshotCapabilitiesE8:
    def test_snapshot_schema_historical_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        caps = profile["snapshot_capabilities"]
        assert caps["snapshot_manifest_schema"]["available"] is True
        assert caps["historical_snapshot_series"]["available"] is False
        assert caps["historical_evm"]["available"] is False

    def test_context_latest_and_not_historical(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        done = _pipeline(project, user)
        ctx_before = AnalyticalContextService(project).build(
            capability_profile=ProjectAnalyticsCapabilityProfile(project).build()
        )
        assert ctx_before["latest_completed_snapshot"]["id"] == str(done.pk)
        AnalyticalSnapshotService.publish(done, actor=user)
        ctx = AnalyticalContextService(project).build(
            capability_profile=ProjectAnalyticsCapabilityProfile(project).build()
        )
        assert ctx["latest_published_snapshot"]["status"] == "published"
        assert ctx["historical_authority"] is False
        assert ctx["snapshot_available"] is False

    def test_live_evm_unchanged(self):
        project = ProjectFactory()
        TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 6, 1))
        before = compute_evm(str(project.pk))
        user = UserFactory()
        _source_version(project)
        _pipeline(project, user)
        after = compute_evm(str(project.pk))
        assert before["spi"] == after["spi"]


@pytest.mark.django_db
class TestSnapshotAPI:
    def test_list_detail_latest_filters_post_auth(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        _source_version(project)
        snap = _pipeline(project, user)
        list_url = reverse("scheduling:schedule_analytical_snapshots", kwargs={"pk": project.pk})
        assert client.get(list_url).status_code == 200
        assert client.get(list_url, {"status": "completed"}).json()["items"]
        assert client.get(list_url, {"page_size": 100}).json()["pagination"]["page_size"] <= 50
        detail_url = reverse(
            "scheduling:schedule_analytical_snapshot_detail",
            kwargs={"pk": project.pk, "snapshot_pk": snap.pk},
        )
        assert client.get(detail_url).json()["snapshot"]["input_manifest"]
        latest_url = reverse(
            "scheduling:schedule_analytical_snapshots_latest", kwargs={"pk": project.pk}
        )
        assert client.get(latest_url).json()["latest_completed"] is not None
        assert client.post(list_url).status_code == 405

    def test_unauthorized_cross_project_no_writes(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = _member_client(client, p1)
        _source_version(p1)
        snap = _pipeline(p1, user)
        list_url = reverse("scheduling:schedule_analytical_snapshots", kwargs={"pk": p1.pk})
        assert client.get(
            reverse("scheduling:schedule_analytical_snapshots", kwargs={"pk": p2.pk})
        ).status_code in (302, 403)
        assert client.get(
            reverse(
                "scheduling:schedule_analytical_snapshot_detail",
                kwargs={"pk": p2.pk, "snapshot_pk": snap.pk},
            )
        ).status_code in (403, 404)
        before = AnalyticalSnapshot.objects.filter(project=p1).count()
        client.get(list_url)
        client.get(list_url)
        assert AnalyticalSnapshot.objects.filter(project=p1).count() == before


@pytest.mark.django_db
class TestSnapshotIBSNoWrite:
    def test_no_ibs_snapshot_rows(self):
        from environments.models import Project

        from scheduling.models import AnalyticalSnapshotResult, AnalyticalSnapshotSeriesPoint

        ibs_ids = list(Project.objects.filter(name__icontains="IBS").values_list("pk", flat=True))
        assert AnalyticalSnapshot.objects.filter(project_id__in=ibs_ids).count() == 0
        assert AnalyticalSnapshotResult.objects.filter(snapshot__project_id__in=ibs_ids).count() == 0
        assert AnalyticalSnapshotSeriesPoint.objects.filter(snapshot__project_id__in=ibs_ids).count() == 0


@pytest.mark.django_db
class TestSnapshotPerformance:
    def test_fingerprint_batch_under_two_seconds(self):
        kwargs = dict(
            project_id="perf",
            source_version_id="sv",
            source_content_hash="h",
            baseline_version_id=None,
            baseline_revision=None,
            data_date="2025-01-01",
            as_of_date="2025-01-01",
            methodology_version=E8_METHODOLOGY_VERSION,
            capability_profile_version="v1",
            trust_policy_version="v1",
            calculation_engine_version="manifest-v1",
            methodology_mode="derived_current_schedule_evm",
            trust_binding_fingerprint="bfp",
        )
        start = time.perf_counter()
        for i in range(10_000):
            build_input_fingerprint(**{**kwargs, "source_version_id": f"sv{i}"})
        assert time.perf_counter() - start < 2.0

    def test_latest_lookup_bounded_queries(self):
        project = ProjectFactory()
        user = UserFactory()
        _source_version(project)
        _pipeline(project, user)
        with CaptureQueriesContext(connection) as ctx:
            AnalyticalSnapshotService.get_latest_completed(project)
        assert len(ctx) <= 5
