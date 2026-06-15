# scheduling/tests/test_executive_wbs_matrix.py
"""DF-C3 E8 canonical WBS matrix integration tests."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import ScheduleSourceVersion, Task
from scheduling.services.executive_controls.hierarchy_mode import (
    STAGE_PROXY_LABEL,
    HierarchyMode,
    HierarchyModeResolver,
)
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.wbs_analytics_session import (
    UNASSIGNED_KEY,
    WBSAnalyticsSession,
)
from scheduling.services.executive_controls.wbs_matrix import WBSMatrixService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory
from scheduling.tests.test_executive_matrix import _bind

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _source_version(project):
    return ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.MANUAL,
        source_filename="t.xml",
        status=ScheduleSourceVersion.Status.CURRENT,
        imported_at=timezone.now(),
    )


def _wbs_tree(project, user=None):
    sv = _source_version(project)
    version = WBSVersionService.create_draft(
        project=project,
        name="Import WBS",
        source_version=sv,
        actor=user,
    )
    svc = WBSHierarchyService(version)
    root = svc.create_node(WBSNodeDTO(name="Root", external_id="r1", code="1"))
    child = svc.create_node(
        WBSNodeDTO(name="Child", external_id="c1", code="1.1", external_parent_id="r1"),
        parent=root,
    )
    WBSVersionService.activate(version, actor=user)
    return version, root, child


@pytest.mark.django_db
class TestHierarchyMode:
    def test_stage_proxy_when_no_wbs(self):
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure")
        ctx = HierarchyModeResolver(project).resolve()
        assert ctx.hierarchy_mode == HierarchyMode.STAGE_PROXY.value
        assert ctx.hierarchy_name == STAGE_PROXY_LABEL

    def test_unavailable_when_no_stage_or_wbs(self):
        project = ProjectFactory()
        TaskFactory(project=project, stage="")
        ctx = HierarchyModeResolver(project).resolve()
        assert ctx.hierarchy_mode == HierarchyMode.UNAVAILABLE.value

    def test_canonical_partial(self):
        project = ProjectFactory()
        user = UserFactory()
        version, root, child = _wbs_tree(project, user)
        t1 = TaskFactory(project=project)
        TaskFactory(project=project)
        t1.wbs_node = child
        t1.save(update_fields=["wbs_node"])
        ctx = HierarchyModeResolver(project).resolve()
        assert ctx.hierarchy_mode == HierarchyMode.CANONICAL_WBS_PARTIAL.value
        assert ctx.unassigned_task_count == 1

    def test_canonical_available_full_coverage(self):
        project = ProjectFactory()
        user = UserFactory()
        version, root, child = _wbs_tree(project, user)
        t1 = TaskFactory(project=project)
        t1.wbs_node = child
        t1.save(update_fields=["wbs_node"])
        ctx = HierarchyModeResolver(project).resolve()
        assert ctx.hierarchy_mode == HierarchyMode.CANONICAL_WBS.value


@pytest.mark.django_db
class TestWBSMatrixAggregation:
    def test_unassigned_virtual_row(self):
        project = ProjectFactory()
        user = UserFactory()
        version, root, child = _wbs_tree(project, user)
        assigned = TaskFactory(project=project)
        TaskFactory(project=project)
        assigned.wbs_node = child
        assigned.save(update_fields=["wbs_node"])
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        payload = WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())
        keys = [r["key"] for r in payload["rows"]]
        assert UNASSIGNED_KEY in keys
        assert payload["summary"]["reconciliation"]["reconciles"] is True

    def test_rollup_deduplicates_tasks(self):
        project = ProjectFactory()
        user = UserFactory()
        version, root, child = _wbs_tree(project, user)
        task = TaskFactory(project=project)
        task.wbs_node = child
        task.save(update_fields=["wbs_node"])
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        root_row = next(
            r
            for r in WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())["rows"]
            if r["key"] == str(root.pk)
        )
        assert root_row["population"]["activity_count"] == 1

    def test_trusted_entities_deduped_on_rollup(self):
        project = ProjectFactory()
        user = UserFactory()
        version, root, child = _wbs_tree(project, user)
        t1, t2 = TaskFactory.create_batch(2, project=project)
        t1.wbs_node = root
        t2.wbs_node = child
        Task.objects.bulk_update([t1, t2], ["wbs_node"])
        entity = IFCEntityFactory(ifc_file__project=project)
        for t in (t1, t2):
            _bind(t, entity.global_id)
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        root_row = next(
            r
            for r in WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())["rows"]
            if r["key"] == str(root.pk)
        )
        assert root_row["model_impact"]["trusted_entity_count"] == 1


@pytest.mark.django_db
class TestWBSMatrixAPI:
    def test_hierarchy_context_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_hierarchy_context", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert "hierarchy_mode" in resp.json()["hierarchy"]

    def test_wbs_node_denied_cross_project(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = _member_client(client, p1)
        version, root, _ = _wbs_tree(p2, user)
        url = reverse(
            "scheduling:executive_controls_wbs_node",
            kwargs={"pk": p1.pk, "node_pk": str(root.pk)},
        )
        assert client.get(url).status_code == 404

    def test_post_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_hierarchy_context", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405
