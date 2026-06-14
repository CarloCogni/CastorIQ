# scheduling/tests/test_source_version_foundation.py
"""DF-A1 source version foundation — models, services, API, compatibility."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    ScheduleActivity,
    ScheduleImportRun,
    ScheduleSource,
    ScheduleSourceVersion,
    Task,
    TaskEntityBinding,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.services.source_version.activity_identity import ScheduleActivityIdentityService
from scheduling.services.source_version.contracts import ImportRunCounts
from scheduling.services.source_version.import_run import ScheduleImportRunService
from scheduling.services.source_version.source_version import ScheduleSourceVersionService
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestScheduleActivityModel:
    def test_project_scoped_key_uniqueness(self):
        project = ProjectFactory()
        ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key="p6xml:ext:100",
            origin=ScheduleActivity.Origin.IMPORTED,
        )
        with pytest.raises(Exception):
            ScheduleActivity.objects.create(
                project=project,
                canonical_activity_key="p6xml:ext:100",
                origin=ScheduleActivity.Origin.IMPORTED,
            )

    def test_same_external_id_different_projects_allowed(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        ScheduleActivity.objects.create(
            project=p1,
            canonical_activity_key="p6xml:ext:100",
        )
        ScheduleActivity.objects.create(
            project=p2,
            canonical_activity_key="p6xml:ext:100",
        )

    def test_manual_generated_identity(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        result = svc.get_or_create_from_evidence(
            source_type=Task.Source.MANUAL,
            allow_generated_manual=True,
        )
        assert result.activity_id
        assert result.identity_status == ScheduleActivity.IdentityStatus.ACTIVE


@pytest.mark.django_db
class TestScheduleActivityIdentityService:
    def test_external_id_preferred(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        key, status = svc.build_canonical_key(
            source_type="p6xml",
            external_activity_id="OBJ-1",
        )
        assert key == "p6xml:ext:OBJ-1"
        assert status == ScheduleActivity.IdentityStatus.ACTIVE

    def test_stable_external_reuse(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        r1 = svc.get_or_create_from_evidence(
            source_type=Task.Source.P6XML,
            external_activity_id="9001",
            activity_code="A100",
        )
        r2 = svc.get_or_create_from_evidence(
            source_type=Task.Source.P6XML,
            external_activity_id="9001",
            activity_code="OTHER",
        )
        assert r1.activity_id == r2.activity_id
        assert r2.created is False

    def test_duplicate_activity_code_unresolved(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key="p6xml:code:DUPE",
            activity_code="DUPE",
        )
        result = svc.get_or_create_from_evidence(
            source_type=Task.Source.P6XML,
            activity_code="DUPE",
        )
        assert result.identity_status == ScheduleActivity.IdentityStatus.UNRESOLVED

    def test_name_alone_not_used(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        r1 = svc.get_or_create_from_evidence(
            source_type=Task.Source.CSV,
            display_name="Same Name",
        )
        r2 = svc.get_or_create_from_evidence(
            source_type=Task.Source.CSV,
            display_name="Same Name",
        )
        assert r1.activity_id != r2.activity_id

    def test_msp_uid_style_external(self):
        project = ProjectFactory()
        svc = ScheduleActivityIdentityService(project)
        result = svc.get_or_create_from_evidence(
            source_type=Task.Source.MSP,
            external_activity_id="42",
        )
        assert "ext:42" in result.canonical_activity_key


@pytest.mark.django_db
class TestScheduleSourceVersionService:
    def _candidate(self, project, user, n=1):
        svc = ScheduleSourceVersionService(project, user)
        return svc.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename=f"plan-{n}.xml",
            data_date=date(2026, 6, 14),
        )

    def test_create_candidate_and_accept_current(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleSourceVersionService(project, user)
        c1 = svc.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename="v1.xml",
        )
        assert c1.version_number == 1
        accepted = svc.accept_as_current(c1.version_id)
        assert accepted.status == ScheduleSourceVersion.Status.CURRENT
        current = svc.get_current()
        assert str(current.pk) == c1.version_id

    def test_second_version_supersedes_first(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleSourceVersionService(project, user)
        c1 = self._candidate(project, user, 1)
        svc.accept_as_current(c1.version_id)
        c2 = self._candidate(project, user, 2)
        svc.accept_as_current(c2.version_id)
        v1 = ScheduleSourceVersion.objects.get(pk=c1.version_id)
        v2 = ScheduleSourceVersion.objects.get(pk=c2.version_id)
        assert v1.status == ScheduleSourceVersion.Status.SUPERSEDED
        assert v2.status == ScheduleSourceVersion.Status.CURRENT
        assert v2.supersedes_id == v1.pk

    def test_one_current_per_project_enforced(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleSourceVersionService(project, user)
        c1 = self._candidate(project, user, 1)
        c2 = self._candidate(project, user, 2)
        svc.accept_as_current(c1.version_id)
        svc.accept_as_current(c2.version_id)
        assert (
            ScheduleSourceVersion.objects.filter(
                project=project,
                status=ScheduleSourceVersion.Status.CURRENT,
            ).count()
            == 1
        )

    def test_reject_candidate_never_current(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleSourceVersionService(project, user)
        c = self._candidate(project, user)
        svc.reject_candidate(c.version_id, reason="bad file")
        version = ScheduleSourceVersion.objects.get(pk=c.version_id)
        assert version.status == ScheduleSourceVersion.Status.REJECTED
        assert svc.get_current() is None

    def test_cross_project_isolation(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = UserFactory()
        svc1 = ScheduleSourceVersionService(p1, user)
        c = svc1.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename="x.xml",
        )
        with pytest.raises(ScheduleSourceVersion.DoesNotExist):
            ScheduleSourceVersionService(p2, user).accept_as_current(c.version_id)


@pytest.mark.django_db
class TestScheduleImportRunService:
    def test_failed_run_no_source_version(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleImportRunService(project, user)
        started = svc.start_run(
            source_type=Task.Source.P6XML,
            source_filename="bad.xml",
            mode=ScheduleImportRun.Mode.REPLACE,
        )
        failed = svc.mark_failed(started.run_id, error_summary="parse error")
        run = ScheduleImportRun.objects.get(pk=started.run_id)
        assert failed.status == ScheduleImportRun.Status.FAILED
        assert run.source_version_id is None

    def test_success_attaches_version(self):
        project = ProjectFactory()
        user = UserFactory()
        vsvc = ScheduleSourceVersionService(project, user)
        candidate = vsvc.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename="ok.xml",
        )
        vsvc.accept_as_current(candidate.version_id)
        isvc = ScheduleImportRunService(project, user)
        started = isvc.start_run(
            source_type=Task.Source.P6XML,
            source_filename="ok.xml",
        )
        done = isvc.mark_succeeded(
            started.run_id,
            source_version_id=candidate.version_id,
            counts=ImportRunCounts(task_count=10, dependency_count=5),
        )
        run = ScheduleImportRun.objects.get(pk=started.run_id)
        assert done.status == ScheduleImportRun.Status.SUCCEEDED
        assert str(run.source_version_id) == candidate.version_id

    def test_terminal_transition_idempotent(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleImportRunService(project, user)
        started = svc.start_run(
            source_type=Task.Source.P6XML,
            source_filename="x.xml",
        )
        svc.mark_failed(started.run_id, error_summary="fail")
        again = svc.mark_failed(started.run_id, error_summary="fail again")
        assert again.error == "Import run already terminal."


@pytest.mark.django_db
class TestTaskNullableProvenance:
    def test_legacy_task_nullable_fks(self):
        task = TaskFactory()
        assert task.schedule_activity_id is None
        assert task.source_version_id is None

    def test_optional_linkage(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key="manual:abc",
            origin=ScheduleActivity.Origin.MANUAL,
        )
        vsvc = ScheduleSourceVersionService(project, user)
        ver = vsvc.create_candidate(
            source_type=Task.Source.MANUAL,
            source_filename="manual",
        )
        vsvc.accept_as_current(ver.version_id)
        version = ScheduleSourceVersion.objects.get(pk=ver.version_id)
        task = TaskFactory(project=project)
        task.schedule_activity = activity
        task.source_version = version
        task.save(update_fields=["schedule_activity", "source_version"])
        task.refresh_from_db()
        assert task.schedule_activity_id == activity.pk
        assert task.source_version_id == version.pk


@pytest.mark.django_db
class TestLegacyCompatibility:
    def test_schedule_source_unchanged(self):
        project = ProjectFactory()
        legacy = ScheduleSource.objects.create(
            project=project,
            filename="old.xml",
            source_format=Task.Source.P6XML,
            task_count=5,
            data_date=date(2026, 1, 1),
        )
        task = TaskFactory(project=project)
        assert ScheduleSource.objects.filter(pk=legacy.pk).exists()
        assert task.schedule_source_id is None

    def test_e2_binding_unaffected(self):
        project = ProjectFactory()
        task = TaskFactory(project=project)
        binding = TaskEntityBinding.objects.create(
            task=task,
            entity_global_id="abc",
            link_method="manual",
            confidence=1.0,
            governance_status="trusted",
            is_active=True,
            needs_review=False,
        )
        assert binding.task_id == task.pk


@pytest.mark.django_db
class TestProvenanceAPI:
    def test_source_versions_get(self, client):
        project = ProjectFactory()
        user = UserFactory()
        _member_client(client, project)
        vsvc = ScheduleSourceVersionService(project, user)
        c = vsvc.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename="p.xml",
        )
        vsvc.accept_as_current(c.version_id)
        url = reverse("scheduling:schedule_source_versions", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_source_version_id"] == c.version_id
        assert len(data["items"]) == 1

    def test_import_runs_get(self, client):
        project = ProjectFactory()
        user = UserFactory()
        _member_client(client, project)
        svc = ScheduleImportRunService(project, user)
        svc.start_run(source_type=Task.Source.P6XML, source_filename="f.xml")
        url = reverse("scheduling:schedule_import_runs", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_task_provenance_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        task = TaskFactory(project=project)
        url = reverse(
            "scheduling:task_provenance",
            kwargs={"pk": project.pk, "task_pk": task.pk},
        )
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.json()["provenance_available"] is False

    def test_post_returns_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_source_versions", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_unauthorized_denied(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:schedule_source_versions", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code in (302, 403)

    def test_cross_project_denied(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = UserFactory()
        ProjectMembershipFactory(project=p1, user=user, permission="editor")
        client.force_login(user)
        url = reverse("scheduling:schedule_source_versions", kwargs={"pk": p2.pk})
        resp = client.get(url)
        assert resp.status_code == 403

    def test_pagination_capped(self, client):
        project = ProjectFactory()
        user = UserFactory()
        _member_client(client, project)
        vsvc = ScheduleSourceVersionService(project, user)
        for i in range(3):
            c = vsvc.create_candidate(
                source_type=Task.Source.P6XML,
                source_filename=f"f{i}.xml",
            )
            vsvc.reject_candidate(c.version_id)
        url = reverse("scheduling:schedule_source_versions", kwargs={"pk": project.pk})
        resp = client.get(url, {"page_size": "999"})
        assert resp.json()["pagination"]["page_size"] == 50


@pytest.mark.django_db
class TestCapabilityAndE8Context:
    def test_provenance_capabilities_schema_available(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        prov = profile["provenance_capabilities"]
        assert prov["source_version_identity"]["available"] is True
        assert (
            prov["repeatable_source_version"]["available"] is False
            or prov["repeatable_source_version"]["state"] == "available_with_caveats"
        )

    def test_historical_still_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["capabilities"][FeatureId.HISTORICAL_SPI_TREND.value]["available"] is False

    def test_wbs_still_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["capabilities"][FeatureId.WBS_MATRIX.value]["available"] is False

    def test_e8_context_without_provenance(self):
        project = ProjectFactory()
        ctx = AnalyticalContextService(project).build()
        assert ctx["source_version"] is None
        assert ctx["provenance_caveat"]

    def test_e8_context_with_provenance(self):
        project = ProjectFactory()
        user = UserFactory()
        vsvc = ScheduleSourceVersionService(project, user)
        c = vsvc.create_candidate(
            source_type=Task.Source.P6XML,
            source_filename="p.xml",
        )
        vsvc.accept_as_current(c.version_id)
        ctx = AnalyticalContextService(project).build()
        assert ctx["source_version"]["id"] == c.version_id


@pytest.mark.django_db
class TestMigrationFoundation:
    def test_tables_exist_after_migrate(self):
        assert ScheduleActivity.objects.count() >= 0
        assert ScheduleSourceVersion.objects.count() >= 0
        assert ScheduleImportRun.objects.count() >= 0

    def test_no_backfill_on_legacy_project(self):
        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        ScheduleSource.objects.create(
            project=project,
            filename="legacy.xml",
            source_format=Task.Source.P6XML,
            task_count=3,
        )
        assert Task.objects.filter(project=project, source_version_id__isnull=True).count() == 3


@pytest.mark.django_db
class TestSyntheticScenarios:
    def test_two_versions_supersession_chain(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleSourceVersionService(project, user)
        c1 = svc.create_candidate(source_type=Task.Source.P6XML, source_filename="a.xml")
        svc.accept_as_current(c1.version_id)
        c2 = svc.create_candidate(source_type=Task.Source.P6XML, source_filename="b.xml")
        svc.accept_as_current(c2.version_id)
        assert svc.get_current().version_number == 2

    def test_failed_import_no_current(self):
        project = ProjectFactory()
        user = UserFactory()
        isvc = ScheduleImportRunService(project, user)
        run = isvc.start_run(source_type=Task.Source.XER, source_filename="bad.xer")
        isvc.mark_failed(run.run_id, error_summary="xer parse")
        vsvc = ScheduleSourceVersionService(project, user)
        assert vsvc.get_current() is None
