# scheduling/tests/test_executive_wbs_closure.py
"""DF-C3.1 WBS integration truth, performance, and regression closure tests."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import Task
from scheduling.services.executive_controls.hierarchy_mode import (
    STAGE_PROXY_LABEL,
    HierarchyMode,
    HierarchyModeResolver,
)
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.matrix_hierarchy_options import (
    PROXY_HIERARCHY_DIMENSION_IDS,
    MatrixHierarchyOptionsService,
)
from scheduling.services.executive_controls.wbs_analytics_session import (
    UNASSIGNED_KEY,
    WBSAnalyticsSession,
)
from scheduling.services.executive_controls.wbs_matrix import WBSMatrixService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory
from scheduling.tests.test_executive_wbs_matrix import _source_version, _wbs_tree

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestMatrixHierarchyUITruth:
    def test_canonical_mode_hides_stage_dimensions(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        task = TaskFactory(project=project, stage=Task.Stage.STRUCTURE)
        task.wbs_node = child
        task.save(update_fields=["wbs_node"])
        opts = MatrixHierarchyOptionsService(project).build()
        assert opts["active_hierarchy_mode"] in {
            HierarchyMode.CANONICAL_WBS.value,
            HierarchyMode.CANONICAL_WBS_PARTIAL.value,
        }
        dim_ids = {d["dimension_id"] for d in opts["filter_dimensions"]}
        assert not dim_ids & PROXY_HIERARCHY_DIMENSION_IDS
        assert opts["show_aggregation_selector"] is True
        assert any(
            v["option_id"] == HierarchyMode.CANONICAL_WBS.value for v in opts["hierarchy_views"]
        )

    def test_partial_canonical_shows_coverage_context(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        assigned = TaskFactory(project=project)
        TaskFactory(project=project)
        assigned.wbs_node = child
        assigned.save(update_fields=["wbs_node"])
        hierarchy = HierarchyModeResolver(project).resolve()
        assert hierarchy.hierarchy_mode == HierarchyMode.CANONICAL_WBS_PARTIAL.value
        assert hierarchy.unassigned_task_count == 1

    def test_stage_proxy_mode_labels_and_dimensions(self):
        project = ProjectFactory()
        TaskFactory(project=project, stage=Task.Stage.MEP)
        opts = MatrixHierarchyOptionsService(project).build()
        assert opts["active_hierarchy_mode"] == HierarchyMode.STAGE_PROXY.value
        assert opts["show_dimension_selector"] is True
        dim_ids = {d["dimension_id"] for d in opts["filter_dimensions"]}
        assert "stage" in dim_ids
        assert any(v["label"] == STAGE_PROXY_LABEL for v in opts["hierarchy_views"])

    def test_unavailable_hides_proxy_dimensions(self):
        project = ProjectFactory()
        TaskFactory(project=project, stage="")
        opts = MatrixHierarchyOptionsService(project).build()
        assert opts["active_hierarchy_mode"] == HierarchyMode.UNAVAILABLE.value
        dim_ids = {d["dimension_id"] for d in opts["filter_dimensions"]}
        assert "stage" not in dim_ids

    def test_explicit_stage_proxy_override(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        t = TaskFactory(project=project, stage=Task.Stage.STRUCTURE)
        t.wbs_node = child
        t.save(update_fields=["wbs_node"])
        opts = MatrixHierarchyOptionsService(project).build(
            hierarchy_mode_override=HierarchyMode.STAGE_PROXY.value,
        )
        assert opts["active_hierarchy_mode"] == HierarchyMode.STAGE_PROXY.value
        assert opts["show_dimension_selector"] is True

    def test_matrix_page_context_matches_api(self, client):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        t = TaskFactory(project=project, stage="structure")
        t.wbs_node = child
        t.save(update_fields=["wbs_node"])
        _member_client(client, project)
        page = client.get(
            reverse("scheduling:executive_controls_matrix", kwargs={"pk": project.pk})
        )
        assert page.status_code == 200
        api = client.get(
            reverse("scheduling:executive_controls_hierarchy_context", kwargs={"pk": project.pk})
        )
        assert api.status_code == 200
        assert (
            page.context["hierarchy_context"]["hierarchy_mode"]
            == api.json()["hierarchy"]["hierarchy_mode"]
        )


@pytest.mark.django_db
class TestWBSReconciliationClosure:
    def test_assigned_plus_unassigned_equals_eligible(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        for i in range(5):
            t = TaskFactory(project=project)
            if i < 3:
                t.wbs_node = child
                t.save(update_fields=["wbs_node"])
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        payload = WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())
        rec = payload["summary"]["reconciliation"]
        assert rec["reconciles"] is True
        assert (
            rec["eligible_tasks"] == rec["assigned_unique_tasks"] + rec["unassigned_unique_tasks"]
        )

    def test_session_query_count_bounded(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        TaskFactory.create_batch(50, project=project, wbs_node=child)
        hierarchy = HierarchyModeResolver(project).resolve()
        with CaptureQueriesContext(connection) as ctx:
            WBSAnalyticsSession.load(project, hierarchy)
        assert len(ctx.captured_queries) <= 25

    def test_matrix_no_per_node_queries(self):
        project = ProjectFactory()
        user = UserFactory()
        version = WBSVersionService.create_draft(
            project=project,
            name="Bench",
            source_version=_source_version(project),
            actor=user,
        )
        svc = WBSHierarchyService(version)
        root = svc.create_node(WBSNodeDTO(name="Root", external_id="r"))
        leaves = [
            svc.create_node(
                WBSNodeDTO(name=f"C{i}", external_id=f"c{i}", parent_id=root.pk),
                parent=root,
            )
            for i in range(20)
        ]
        WBSVersionService.activate(version, actor=user)
        tasks = TaskFactory.create_batch(100, project=project)
        for i, t in enumerate(tasks):
            t.wbs_node = leaves[i % len(leaves)]
        Task.objects.bulk_update(tasks, ["wbs_node"], batch_size=500)
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        with CaptureQueriesContext(connection) as ctx:
            WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())
        assert len(ctx.captured_queries) <= 2

    def test_unassigned_virtual_bucket_in_matrix(self):
        project = ProjectFactory()
        user = UserFactory()
        _, _, child = _wbs_tree(project, user)
        t = TaskFactory(project=project)
        t.wbs_node = child
        t.save(update_fields=["wbs_node"])
        TaskFactory(project=project)
        hierarchy = HierarchyModeResolver(project).resolve()
        session = WBSAnalyticsSession.load(project, hierarchy)
        keys = [
            r["key"]
            for r in WBSMatrixService(project, session).build_rows(ExecutiveMatrixFilters())["rows"]
        ]
        assert UNASSIGNED_KEY in keys


@pytest.mark.django_db
class TestWBSBenchmarkGuards:
    def test_1k_matrix_under_eight_seconds(self):
        from scheduling.tests.wbs_matrix_benchmark_harness import run_benchmark

        result = run_benchmark(1000, repeats=1)
        assert result.matrix_median_s < 8.0, f"1k matrix took {result.matrix_median_s:.2f}s"

    @pytest.mark.slow
    def test_5k_matrix_diagnostic(self):
        from scheduling.tests.wbs_matrix_benchmark_harness import run_benchmark

        result = run_benchmark(5000, repeats=1)
        assert result.matrix_median_s < 8.0, f"5k matrix took {result.matrix_median_s:.2f}s"

    @pytest.mark.slow
    def test_10k_matrix_recorded(self):
        from scheduling.tests.wbs_matrix_benchmark_harness import run_benchmark

        result = run_benchmark(10_000, repeats=2)
        assert result.matrix_query_count <= 5
        assert result.matrix_median_s < 8.0, (
            f"10k matrix median {result.matrix_median_s:.2f}s "
            f"(min={result.matrix_min_s:.2f}, max={result.matrix_max_s:.2f})"
        )
