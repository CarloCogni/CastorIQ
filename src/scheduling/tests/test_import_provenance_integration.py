# scheduling/tests/test_import_provenance_integration.py
"""DF-A1.1 import provenance integration — coordinator, linkage, rollback."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    ScheduleActivity,
    ScheduleImportRun,
    ScheduleSourceVersion,
    Task,
    TaskEntityBinding,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.services.source_version.content_hash import (
    hash_file_bytes,
    hash_parsed_tasks_payload,
)
from scheduling.services.source_version.identity_adapters import (
    extract_activity_evidence,
    import_batch_code_counts,
)
from scheduling.services.source_version.import_provenance import (
    ImportProvenanceContext,
    ScheduleImportProvenanceCoordinator,
)
from scheduling.services.source_version.import_run import ScheduleImportRunService
from scheduling.services.source_version.source_version import ScheduleSourceVersionService
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _seed_save_session(client, project, tasks_data, deps_data=None):
    session = client.session
    session[f"parsed_tasks_{project.pk}"] = json.dumps(tasks_data)
    session[f"schedule_filename_{project.pk}"] = "synthetic.xer"
    session[f"schedule_content_hash_{project.pk}"] = hash_parsed_tasks_payload(tasks_data)
    if deps_data is not None:
        session[f"parsed_deps_{project.pk}"] = json.dumps(deps_data)
    session.save()


def _xer_task(name, code, xer_id, **extra):
    return {
        "name": name,
        "start_date": "2025-01-01",
        "end_date": "2025-01-10",
        "status": "planned",
        "source": "xer",
        "activity_code": code,
        "color": "#3b82f6",
        "description": "",
        "_xer_task_id": xer_id,
        **extra,
    }


@pytest.mark.django_db
class TestIdentityAdapters:
    def test_p6_external_id_reuse_key(self):
        counts = import_batch_code_counts([{"activity_code": "A1", "_p6_obj_id": "OBJ-1"}])
        ev = extract_activity_evidence(
            {"name": "Task", "activity_code": "A1", "_p6_obj_id": "OBJ-1"},
            source_type="p6xml",
            batch_code_counts=counts,
        )
        assert ev.evidence_type == "external_id"
        assert "p6xml:ext:OBJ-1" in ev.canonical_activity_key

    def test_msp_uid_evidence(self):
        counts = import_batch_code_counts([{"_msp_uid": "42"}])
        ev = extract_activity_evidence(
            {"name": "T", "_msp_uid": "42"},
            source_type="msp",
            batch_code_counts=counts,
        )
        assert ev.external_activity_id == "42"

    def test_ambiguous_code_unresolved(self):
        rows = [{"activity_code": "DUP"}, {"activity_code": "DUP"}]
        counts = import_batch_code_counts(rows)
        ev = extract_activity_evidence(rows[0], source_type="csv", batch_code_counts=counts)
        assert ev.evidence_type == "unresolved"
        assert ev.unresolved_reason == "duplicate_activity_code_in_import"

    def test_name_alone_not_external(self):
        counts = import_batch_code_counts([{"name": "Same Name"}])
        ev = extract_activity_evidence(
            {"name": "Same Name"},
            source_type="excel",
            batch_code_counts=counts,
        )
        assert ev.evidence_type == "unresolved"


@pytest.mark.django_db
class TestImportSaveProvenance:
    def test_successful_xer_import_links_provenance(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("Foundation", "A001", "1001"), _xer_task("Structure", "A002", "1002")],
        )
        resp = client.post(url)
        assert resp.status_code == 200
        tasks = Task.objects.filter(project=project)
        assert tasks.count() == 2
        assert tasks.filter(source_version__isnull=False).count() == 2
        assert tasks.filter(schedule_activity__isnull=False).count() == 2
        assert ScheduleImportRun.objects.filter(project=project, status="succeeded").exists()
        current = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        )
        assert current.source_type == "xer"

    def test_second_import_supersedes_and_reuses_activity(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("T1", "A001", "1001")])
        client.post(url)
        first_activity = Task.objects.get(project=project).schedule_activity_id
        _seed_save_session(
            client,
            project,
            [_xer_task("T1 updated", "A001", "1001"), _xer_task("T2", "A002", "1002")],
        )
        client.post(url)
        versions = ScheduleSourceVersion.objects.filter(project=project).order_by("version_number")
        assert versions.count() == 2
        assert versions.last().status == ScheduleSourceVersion.Status.CURRENT
        assert versions.first().status == ScheduleSourceVersion.Status.SUPERSEDED
        reused = Task.objects.filter(project=project, activity_code="A001").first()
        assert reused.schedule_activity_id == first_activity

    def test_failed_import_preserves_prior_current(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("T1", "A001", "1001")])
        client.post(url)
        prior_id = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        ).pk
        _seed_save_session(client, project, [_xer_task("T2", "A002", "1002")])
        with patch.object(
            ScheduleSourceVersionService,
            "accept_as_current",
            side_effect=RuntimeError("activation blocked"),
        ):
            resp = client.post(url)
        assert resp.status_code == 500
        assert ScheduleImportRun.objects.filter(project=project, status="failed").exists()
        current = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        )
        assert current.pk == prior_id
        assert not Task.objects.filter(project=project, activity_code="A002").exists()

    def test_replace_mode_clears_tasks_before_import(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, name="Legacy", activity_code="OLD")
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        session = client.session
        session[f"schedule_replace_{project.pk}"] = True
        session.save()
        _seed_save_session(client, project, [_xer_task("New", "N001", "9001")])
        client.post(url)
        assert Task.objects.filter(project=project).count() == 1
        assert not Task.objects.filter(activity_code="OLD").exists()

    def test_append_does_not_update_untouched_tasks(self, client):
        project = ProjectFactory()
        untouched = TaskFactory(
            project=project,
            name="Untouched",
            activity_code="U001",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
        )
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("Imported", "I001", "8001")])
        client.post(url)
        untouched.refresh_from_db()
        assert untouched.source_version_id is None
        imported = Task.objects.get(activity_code="I001")
        assert imported.source_version_id is not None

    def test_msp_uid_import(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [
                {
                    "name": "MSP Task",
                    "start_date": "2025-02-01",
                    "end_date": "2025-02-10",
                    "status": "planned",
                    "source": "msp",
                    "activity_code": "M1",
                    "color": "#3b82f6",
                    "description": "",
                    "_msp_uid": "UID-99",
                }
            ],
        )
        client.post(url)
        task = Task.objects.get(project=project)
        assert task.schedule_activity.canonical_activity_key == "msp:ext:UID-99"

    def test_column_import_without_stable_id_unresolved(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [
                {
                    "name": "No ID Row",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-05",
                    "status": "planned",
                    "source": "excel",
                    "activity_code": "",
                    "color": "#3b82f6",
                    "description": "",
                }
            ],
        )
        client.post(url)
        task = Task.objects.get(project=project)
        assert task.schedule_activity.identity_status == ScheduleActivity.IdentityStatus.UNRESOLVED

    def test_e2_binding_unaffected(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("Bound", "B001", "7001")])
        client.post(url)
        task = Task.objects.get(project=project)
        binding = TaskEntityBinding.objects.create(
            task=task,
            entity_global_id="abc-gid",
            link_method="manual",
            confidence=1.0,
            governance_status="trusted",
            is_active=True,
            needs_review=False,
        )
        binding_id = binding.pk
        _seed_save_session(client, project, [_xer_task("Bound", "B001", "7001")])
        client.post(url)
        assert TaskEntityBinding.objects.filter(pk=binding_id).exists()


@pytest.mark.django_db
class TestCoordinatorUnit:
    def test_terminal_idempotency(self):
        project = ProjectFactory()
        user = UserFactory()
        coord = ScheduleImportProvenanceCoordinator(project, user)
        ctx = ImportProvenanceContext(
            source_type="xer",
            source_filename="a.xer",
            content_hash="abc",
            mode=ScheduleImportRun.Mode.UPDATE,
            data_date=None,
        )
        run_id = coord.start_run(ctx)
        svc = ScheduleImportRunService(project, user)
        svc.mark_failed(run_id, error_summary="fail")
        svc.mark_failed(run_id, error_summary="again")
        run = ScheduleImportRun.objects.get(pk=run_id)
        assert run.status == ScheduleImportRun.Status.FAILED


@pytest.mark.django_db
class TestCapabilityAfterImport:
    def test_provenance_available_after_import(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("T", "C001", "6001")])
        client.post(url)
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        prov = profile["provenance_capabilities"]
        assert prov["source_version_identity"]["available"] is True
        assert prov["import_run_traceability"]["available"] is True
        ctx = AnalyticalContextService(project).build(profile)
        assert ctx["source_version"] is not None
        assert profile["capabilities"][FeatureId.HISTORICAL_SPI_TREND.value]["available"] is False


@pytest.mark.django_db
class TestProvenanceAPIAfterImport:
    def test_lists_reflect_import(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("API", "P001", "5001")])
        client.post(save_url)
        task = Task.objects.get(project=project)
        sv_url = reverse("scheduling:schedule_source_versions", kwargs={"pk": project.pk})
        ir_url = reverse("scheduling:schedule_import_runs", kwargs={"pk": project.pk})
        prov_url = reverse(
            "scheduling:task_provenance",
            kwargs={"pk": project.pk, "task_pk": task.pk},
        )
        assert client.get(sv_url).status_code == 200
        assert client.get(ir_url).status_code == 200
        data = client.get(prov_url).json()
        assert data["source_version"] is not None
        assert data["schedule_activity"] is not None
        assert client.post(sv_url).status_code == 405


@pytest.mark.django_db
class TestMetadataHashing:
    def test_content_hash_stable(self):
        payload = b"same-bytes"
        assert hash_file_bytes(payload) == hash_file_bytes(payload)

    def test_missing_hash_fallback(self):
        digest = hash_parsed_tasks_payload([{"name": "a", "start_date": "2025-01-01"}])
        assert len(digest) == 64


@pytest.mark.django_db
class TestLegacyCompatibility:
    def test_legacy_task_without_import_still_valid(self):
        project = ProjectFactory()
        task = TaskFactory(project=project)
        assert task.source_version_id is None
        assert task.schedule_activity_id is None
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["provenance_capabilities"]["source_version_identity"]["state"] in (
            "available_with_caveats",
            "unavailable",
        )
