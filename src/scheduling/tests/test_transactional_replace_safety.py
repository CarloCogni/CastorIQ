# scheduling/tests/test_transactional_replace_safety.py
"""DF-A1.2 — transactional replace safety and rollback integrity."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    ScheduleImportRun,
    ScheduleSourceVersion,
    Task,
    TaskDependency,
    TaskEntityBinding,
)
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.source_version.content_hash import hash_parsed_tasks_payload
from scheduling.services.source_version.failure_hooks import clear_failure_hooks, set_failure_hook
from scheduling.services.source_version.import_run import ScheduleImportRunService
from scheduling.services.source_version.source_version import ScheduleSourceVersionService
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _seed_save_session(client, project, tasks_data, *, replace=False):
    session = client.session
    session[f"parsed_tasks_{project.pk}"] = json.dumps(tasks_data)
    session[f"schedule_filename_{project.pk}"] = "synthetic.xer"
    session[f"schedule_content_hash_{project.pk}"] = hash_parsed_tasks_payload(tasks_data)
    if replace:
        session[f"schedule_replace_{project.pk}"] = True
    session.save()


def _xer_task(name, code, xer_id):
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
    }


@pytest.fixture(autouse=True)
def _clear_hooks():
    clear_failure_hooks()
    yield
    clear_failure_hooks()


@pytest.mark.django_db
class TestReplaceRollbackIntegrity:
    def test_failure_after_delete_restores_old_tasks(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, name="Keep Me", activity_code="OLD-1")
        TaskFactory(project=project, name="Also Keep", activity_code="OLD-2")
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("Replacement", "NEW-1", "9001")],
            replace=True,
        )
        set_failure_hook(
            "after_schedule_source_create", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        resp = client.post(url)
        assert resp.status_code == 500
        assert Task.objects.filter(project=project).count() == 2
        assert Task.objects.filter(project=project, activity_code="OLD-1").exists()
        assert not Task.objects.filter(project=project, activity_code="NEW-1").exists()

    def test_failure_after_delete_restores_dependencies(self, client):
        project = ProjectFactory()
        t1 = TaskFactory(project=project, activity_code="D1", name="D1")
        t2 = TaskFactory(project=project, activity_code="D2", name="D2")
        TaskDependency.objects.create(predecessor=t1, successor=t2, dep_type="FS", lag_days=0)
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("New", "N1", "8001")],
            replace=True,
        )
        set_failure_hook(
            "before_version_activation", lambda: (_ for _ in ()).throw(RuntimeError("act fail"))
        )
        client.post(url)
        assert TaskDependency.objects.filter(predecessor__project=project).count() == 1

    def test_failure_restores_bindings(self, client):
        project = ProjectFactory()
        task = TaskFactory(project=project, activity_code="BIND-1")
        binding = TaskEntityBinding.objects.create(
            task=task,
            entity_global_id="GID-ROLLBACK",
            link_method="manual",
            confidence=1.0,
            governance_status="trusted",
            is_active=True,
            needs_review=False,
        )
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("New", "N1", "7001")],
            replace=True,
        )
        set_failure_hook(
            "during_task_create", lambda: (_ for _ in ()).throw(RuntimeError("task fail"))
        )
        client.post(url)
        assert TaskEntityBinding.objects.filter(pk=binding.pk).exists()
        assert Task.objects.filter(project=project, activity_code="BIND-1").exists()

    def test_prior_current_version_retained_on_replace_failure(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("V1", "A001", "1001")])
        client.post(url)
        prior = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        )
        _seed_save_session(
            client,
            project,
            [_xer_task("V2 attempt", "B001", "2001")],
            replace=True,
        )
        set_failure_hook(
            "after_provenance_assign", lambda: (_ for _ in ()).throw(RuntimeError("prov fail"))
        )
        client.post(url)
        current = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        )
        assert current.pk == prior.pk
        assert (
            ScheduleSourceVersion.objects.filter(
                status=ScheduleSourceVersion.Status.CURRENT
            ).count()
            == 1
        )

    def test_import_run_survives_rollback_and_is_failed(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, activity_code="X1")
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("Fail", "F1", "6001")],
            replace=True,
        )
        set_failure_hook(
            "after_schedule_source_create", lambda: (_ for _ in ()).throw(RuntimeError("src fail"))
        )
        client.post(url)
        run = ScheduleImportRun.objects.filter(project=project).latest("started_at")
        assert run.status == ScheduleImportRun.Status.FAILED
        assert run.error_summary
        assert run.source_version_id is None

    def test_successful_replace_supersedes_and_replaces(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("First", "A1", "1001")])
        client.post(url)
        v1 = ScheduleSourceVersion.objects.get(project=project, version_number=1)
        TaskFactory(project=project, activity_code="ORPHAN")
        _seed_save_session(
            client,
            project,
            [_xer_task("Second", "B1", "2001"), _xer_task("Third", "B2", "2002")],
            replace=True,
        )
        client.post(url)
        v1.refresh_from_db()
        assert v1.status == ScheduleSourceVersion.Status.SUPERSEDED
        current = ScheduleSourceVersion.objects.get(
            project=project, status=ScheduleSourceVersion.Status.CURRENT
        )
        assert current.version_number == 2
        assert Task.objects.filter(project=project).count() == 2
        assert not Task.objects.filter(project=project, activity_code="ORPHAN").exists()
        tasks = Task.objects.filter(project=project)
        assert tasks.filter(source_version=current).count() == 2

    def test_e8_shows_prior_source_after_failed_replace(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("Stable", "S1", "5001")])
        client.post(url)
        ctx_before = AnalyticalContextService(project).build()
        prior_version_id = ctx_before["source_version"]["id"]
        _seed_save_session(
            client,
            project,
            [_xer_task("Broken", "B1", "6001")],
            replace=True,
        )
        set_failure_hook(
            "before_version_activation", lambda: (_ for _ in ()).throw(RuntimeError("no switch"))
        )
        client.post(url)
        ctx_after = AnalyticalContextService(project).build()
        assert ctx_after["source_version"]["id"] == prior_version_id


@pytest.mark.django_db
class TestImportRunTerminalIdempotency:
    def test_failed_run_cannot_succeed_afterward(self):
        project = ProjectFactory()
        user = UserFactory()
        svc = ScheduleImportRunService(project, user)
        run = svc.start_run(source_type="xer", source_filename="a.xer")
        svc.mark_failed(run.run_id, error_summary="failed")
        again = svc.mark_succeeded(run.run_id, source_version_id=None)
        assert again.error
        assert (
            ScheduleImportRun.objects.get(pk=run.run_id).status == ScheduleImportRun.Status.FAILED
        )


@pytest.mark.django_db
class TestAppendUpdateUnchanged:
    def test_append_still_preserves_untouched_tasks(self, client):
        project = ProjectFactory()
        legacy = TaskFactory(
            project=project,
            activity_code="LEG",
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 5),
        )
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(client, project, [_xer_task("Added", "ADD", "3001")], replace=False)
        client.post(url)
        legacy.refresh_from_db()
        assert legacy.source_version_id is None
        assert Task.objects.filter(project=project).count() == 2


@pytest.mark.django_db
class TestFailureInjectionMatrix:
    @pytest.mark.parametrize(
        "hook_point",
        [
            "after_replace_delete",
            "during_task_create",
            "after_schedule_source_create",
            "after_activity_link",
            "after_provenance_assign",
            "before_version_activation",
            "before_import_run_success",
        ],
    )
    def test_replace_failure_at_hook_restores_schedule(self, client, hook_point):
        project = ProjectFactory()
        TaskFactory(project=project, activity_code=f"HOOK-{hook_point[:8]}")
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("New", "N-HOOK", "9999")],
            replace=True,
        )
        set_failure_hook(
            hook_point, lambda: (_ for _ in ()).throw(RuntimeError(f"fail at {hook_point}"))
        )
        client.post(url)
        assert Task.objects.filter(project=project).count() == 1
        assert ScheduleImportRun.objects.filter(project=project, status="failed").exists()

    def test_activation_failure_via_patch_restores_tasks(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, activity_code="PATCH-OLD")
        _member_client(client, project)
        url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
        _seed_save_session(
            client,
            project,
            [_xer_task("Patch New", "PATCH-NEW", "8888")],
            replace=True,
        )
        with patch.object(
            ScheduleSourceVersionService,
            "accept_as_current",
            side_effect=RuntimeError("activation blocked"),
        ):
            client.post(url)
        assert Task.objects.filter(project=project, activity_code="PATCH-OLD").exists()
        assert not Task.objects.filter(project=project, activity_code="PATCH-NEW").exists()
