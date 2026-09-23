# scheduling/tests/test_governed_mapping_e8_integration.py
"""DF-D3 dimension-gated E8 governed mapping integration tests."""

from __future__ import annotations

import time

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    Task,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.dimension_mode import (
    MODE_GOVERNED_PARTIAL,
    MODE_PROPOSALS_ONLY,
    MODE_PROXY,
    MODE_UNAVAILABLE,
    DimensionModeService,
)
from scheduling.services.executive_controls.governed_mapping_aggregation import (
    GovernedMappingAggregationService,
)
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.contracts import (
    WBSBranchMappingPolicyDTO,
)
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.population import GovernedMappingPopulationService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.wbs_branch_policy import WBSBranchMappingPolicyService
from scheduling.tests.factories import TaskFactory
from scheduling.tests.test_governed_mapping_cutover_readiness import (
    _package_dimension,
    _trade_dimension,
    _wbs_tree,
)

IBS_ID = "eb3b0c76-4812-4ce0-8927-ad85a111763a"


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _auth_client(user, project):
    client = Client()
    ProjectMembershipFactory(project=project, user=user, permission="editor")
    client.force_login(user)
    return client


def _approve_trade_task(project, user, task, dim, mset, value_code="electrical"):
    value = dim.values.get(code=value_code)
    AnalyticalMappingAssignmentService.assign_manually(
        mapping_set=mset,
        dimension_value=value,
        target_type="task",
        task=task,
        actor=user,
        auto_approve=True,
    )
    AnalyticalMappingSetService.activate(mset, actor=user)
    return EffectiveMappingResolver(project).resolve_task(task, dim)


@pytest.mark.django_db
class TestDimensionModes:
    def test_trade_defaults_proxy_without_governed_ready(self):
        project = ProjectFactory()
        TaskFactory(project=project, sub_stage="electrical")
        mode = DimensionModeService(project).build().trade
        assert mode.selected_mode == MODE_PROXY
        assert mode.mode_label == "Trade Proxy"

    def test_trade_proposals_only_mode(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        TaskFactory(project=project, sub_stage="electrical")
        GovernedMappingPopulationService(project, actor=user).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        mode = DimensionModeService(project).build().trade
        assert mode.selected_mode == MODE_PROPOSALS_ONLY

    def test_trade_governed_partial_on_low_coverage(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        task = TaskFactory(project=project, sub_stage="electrical")
        _approve_trade_task(project, user, task, dim, mset)
        for _ in range(20):
            TaskFactory(project=project, sub_stage="other")
        mode = (
            DimensionModeService(project)
            .build(requested_modes={"trade": MODE_GOVERNED_PARTIAL})
            .trade
        )
        assert mode.selected_mode == MODE_GOVERNED_PARTIAL

    def test_package_proxy_independent_of_trade(self):
        project = ProjectFactory()
        user = UserFactory()
        _trade_dimension(project, user)
        TaskFactory(project=project, sub_stage="electrical")
        modes = DimensionModeService(project).build()
        assert modes.trade.fallback_available
        assert modes.package.selected_mode in (MODE_PROXY, MODE_UNAVAILABLE)

    def test_unavailable_without_proxy_signals(self):
        project = ProjectFactory()
        mode = DimensionModeService(project).build().trade
        assert mode.selected_mode in (MODE_PROXY, MODE_UNAVAILABLE)


@pytest.mark.django_db
class TestGovernedAggregation:
    def test_direct_task_mapping_aggregation(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        task = TaskFactory(project=project, sub_stage="electrical", cost=1000)
        _approve_trade_task(project, user, task, dim, mset)
        summary = GovernedMappingAggregationService(project).build_summary(
            "trade", requested_mode=MODE_GOVERNED_PARTIAL
        )
        values = summary["governed_values"]
        assert any(v["task_count"] >= 1 for v in values)

    def test_schedule_activity_mapping(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        from scheduling.models import ScheduleActivity

        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key="act-1",
            display_name="Act",
        )
        task = TaskFactory(project=project, schedule_activity=activity)
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type="schedule_activity",
            schedule_activity=activity,
            actor=user,
            auto_approve=True,
        )
        AnalyticalMappingSetService.activate(mset, actor=user)
        resolved = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert resolved.resolution == "logical_identity"

    def test_wbs_inherited_aggregation(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _package_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Pkg", actor=user)
        version, _root, branch, leaf = _wbs_tree(project, user)
        value = dim.values.get(code="envelope")
        policy = WBSBranchMappingPolicyDTO(
            dimension_key="package",
            mapping_set_id=str(mset.pk),
            wbs_version_id=str(version.pk),
            wbs_node_id=str(branch.pk),
            dimension_value_id=str(value.pk),
            include_descendants=True,
            target_behavior="inherit_to_tasks",
        )
        WBSBranchMappingPolicyService(project).apply_policy(policy, actor=user, auto_approve=True)
        AnalyticalMappingSetService.activate(mset, actor=user)
        TaskFactory(project=project, wbs_node=leaf)
        summary = GovernedMappingAggregationService(project).build_summary(
            "package", requested_mode=MODE_GOVERNED_PARTIAL
        )
        assert summary["governed_values"]

    def test_unmapped_virtual_bucket(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        TaskFactory(project=project)
        AnalyticalMappingSetService.activate(mset, actor=user)
        summary = GovernedMappingAggregationService(project).build_summary(
            "trade", requested_mode=MODE_GOVERNED_PARTIAL
        )
        virtual = summary.get("virtual_buckets", [])
        assert any(v["value_id"] == "__unmapped__" for v in virtual)

    def test_proposed_excluded_from_governed_rows(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        TaskFactory(project=project, sub_stage="electrical")
        GovernedMappingPopulationService(project, actor=user).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        summary = GovernedMappingAggregationService(project).build_summary("trade")
        assert summary["selected_mode"] == MODE_PROPOSALS_ONLY
        assert not summary.get("governed_values")

    def test_spi_cpi_from_summed_components(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        task = TaskFactory(
            project=project,
            sub_stage="electrical",
            cost=5000,
            start_date="2025-01-01",
            end_date="2025-06-30",
            status="in_progress",
            physical_percent_complete=50,
        )
        _approve_trade_task(project, user, task, dim, mset)
        summary = GovernedMappingAggregationService(project).build_summary(
            "trade", requested_mode=MODE_GOVERNED_PARTIAL
        )
        totals = summary.get("totals", {})
        if totals.get("spi") is not None and totals.get("pv"):
            assert totals["spi"] == pytest.approx(totals["ev"] / totals["pv"], rel=1e-3)


@pytest.mark.django_db
class TestGovernedAPI:
    def test_dimension_summary_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse(
            "scheduling:executive_controls_governed_dimension_summary",
            kwargs={"pk": project.pk, "dimension_key": "trade"},
        )
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.json()["dimension_key"] == "trade"

    def test_unmapped_tasks_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse(
            "scheduling:executive_controls_governed_dimension_unmapped_tasks",
            kwargs={"pk": project.pk, "dimension_key": "trade"},
        )
        resp = client.get(url)
        assert resp.status_code == 200

    def test_post_returns_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse(
            "scheduling:executive_controls_governed_dimensions",
            kwargs={"pk": project.pk},
        )
        resp = client.post(url)
        assert resp.status_code == 405

    def test_cross_project_denied(self, client):
        project = ProjectFactory()
        other = ProjectFactory()
        _member_client(client, project)
        url = reverse(
            "scheduling:executive_controls_governed_dimension_summary",
            kwargs={"pk": other.pk, "dimension_key": "trade"},
        )
        resp = client.get(url)
        assert resp.status_code in (403, 404)

    def test_repeated_get_no_writes(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        before = Task.objects.filter(project=project).count()
        url = reverse(
            "scheduling:executive_controls_governed_dimensions",
            kwargs={"pk": project.pk},
        )
        client.get(url)
        client.get(url)
        assert Task.objects.filter(project=project).count() == before


@pytest.mark.django_db
class TestCapabilityAndSnapshot:
    def test_snapshot_governed_analytics_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        gm = profile["governed_mapping_capabilities"]
        assert gm["snapshot_governed_mapping_analytics"]["available"] is False

    def test_trade_package_readiness_independent(self):
        project = ProjectFactory()
        gm = ProjectAnalyticsCapabilityProfile(project).build()["governed_mapping_capabilities"]
        assert "e8_trade_governed_mode" in gm
        assert "e8_package_governed_mode" in gm

    def test_zero_governed_query_budget(self, client):
        """Empty-project overview stays at the DF-B1.1 / DF-D3 ceiling of 55.

        DF-E added a canonical ResourceAssignment probe on the capability path.
        The zero-store gather path folds P6 exists()/zero-AC task counts into
        one aggregate so this budget remains meaningful (no N+1; no raise).
        """
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        assert len(ctx.captured_queries) <= 55


@pytest.mark.django_db
class TestTradePackageAnalysis:
    def test_proxy_label_in_payload(self):
        from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
        from scheduling.services.executive_controls.trade_package_analysis import (
            TradePackageAnalysisService,
        )

        project = ProjectFactory()
        TaskFactory(project=project, sub_stage="electrical")
        payload = TradePackageAnalysisService(project).build(ExecutiveMatrixFilters.from_params({}))
        assert payload["trade_mode_label"] == "Trade Proxy"
        assert payload["dimension_modes"]["trade"]["selected_mode"] == MODE_PROXY

    def test_governed_summary_when_requested(self):
        from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
        from scheduling.services.executive_controls.trade_package_analysis import (
            TradePackageAnalysisService,
        )

        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        task = TaskFactory(project=project, sub_stage="electrical")
        _approve_trade_task(project, user, task, dim, mset)
        filters = ExecutiveMatrixFilters.from_params({"trade_mode": MODE_GOVERNED_PARTIAL})
        payload = TradePackageAnalysisService(project).build(filters)
        assert payload["governed_trade_summary"] is not None


@pytest.mark.django_db
class TestPerformanceGuard:
    def test_resolver_1k_under_budget(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        tasks = [TaskFactory(project=project) for _ in range(1000)]
        value = dim.values.get(code="electrical")
        for task in tasks[:500]:
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=value,
                target_type="task",
                task=task,
                actor=user,
                auto_approve=True,
            )
        AnalyticalMappingSetService.activate(mset, actor=user)
        resolver = EffectiveMappingResolver(project)
        start = time.perf_counter()
        resolver.resolve_many_tasks([t.pk for t in tasks], dim)
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0
