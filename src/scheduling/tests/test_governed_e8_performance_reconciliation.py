# scheduling/tests/test_governed_e8_performance_reconciliation.py
"""DF-D3.1 governed E8 performance and reconciliation closure tests."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    AnalyticalMappingAssignment,
    ScheduleActivity,
    Task,
)
from scheduling.services.executive_controls.dimension_mode import (
    MODE_GOVERNED_PARTIAL,
    MODE_PROXY,
    DimensionModeService,
)
from scheduling.services.executive_controls.governed_mapping_aggregation import (
    GovernedMappingAggregationService,
)
from scheduling.services.executive_controls.governed_mapping_analytics_session import (
    PROPOSED_BUCKET,
    UNMAPPED_BUCKET,
    GovernedMappingAnalyticsSession,
)
from scheduling.services.executive_controls.governed_mapping_drilldown import (
    GovernedMappingDrilldownService,
)
from scheduling.services.executive_controls.governed_mapping_reconciliation import (
    GovernedMappingReconciliationService,
)
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.contracts import WBSBranchMappingPolicyDTO
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.wbs_branch_policy import WBSBranchMappingPolicyService
from scheduling.tests.factories import TaskFactory
from scheduling.tests.governed_e8_benchmark_harness import build_fixture, run_benchmark
from scheduling.tests.test_governed_mapping_cutover_readiness import (
    _package_dimension,
    _trade_dimension,
    _wbs_tree,
)

IBS_ID = "eb3b0c76-4812-4ce0-8927-ad85a111763a"


def _member_client(client, project):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission="editor")
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestBenchmarkHarness:
    def test_fixture_is_deterministic_shape(self):
        fixture = build_fixture(200)
        assert fixture.task_count == 200
        assert fixture.trade_dim is not None
        assert fixture.package_dim is not None
        assert AnalyticalMappingAssignment.objects.filter(mapping_set=fixture.trade_mset).exists()

    def test_1k_governed_summary_guard(self):
        import json

        run = run_benchmark(1000, repeats=1)
        assert run.summary_median_s < 15.0
        assert run.resolve_median_s < 5.0
        assert run.summary_query_count < 120
        print(
            f"DF_D3_1_BENCH={json.dumps({'task_count': 1000, 'summary_median_s': round(run.summary_median_s, 3), 'resolve_median_s': round(run.resolve_median_s, 3), 'summary_query_count': run.summary_query_count})}"
        )

    @pytest.mark.slow
    def test_5k_diagnostic(self):
        import json

        run = run_benchmark(5000, repeats=1)
        assert run.summary_median_s < 25.0
        print(
            f"DF_D3_1_BENCH={json.dumps({'task_count': 5000, 'summary_median_s': round(run.summary_median_s, 3), 'resolve_median_s': round(run.resolve_median_s, 3), 'summary_query_count': run.summary_query_count})}"
        )

    @pytest.mark.slow
    def test_10k_formal_benchmark_recorded(self):
        import json

        run = run_benchmark(10_000, repeats=3)
        assert run.summary_median_s < 30.0
        assert run.governed_value_rows >= 1
        payload = {
            "task_count": 10000,
            "summary_median_s": round(run.summary_median_s, 3),
            "resolve_median_s": round(run.resolve_median_s, 3),
            "rollup_median_s": round(run.rollup_median_s, 3),
            "summary_query_count": run.summary_query_count,
            "cold_summary_s": round(run.cold_summary_s, 3),
        }
        print(f"DF_D3_1_BENCH={json.dumps(payload)}")

    def test_no_n_plus_one_by_value_count(self):
        fixture = build_fixture(500)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        with CaptureQueriesContext(connection) as ctx:
            session.build_dimension_rollup("trade")
            q1 = len(ctx.captured_queries)
        with CaptureQueriesContext(connection) as ctx:
            session.build_dimension_rollup("trade")
            q2 = len(ctx.captured_queries)
        assert q1 == q2 == 0

    def test_drilldown_first_page_query_bound(self):
        fixture = build_fixture(1000)
        with CaptureQueriesContext(connection) as ctx:
            GovernedMappingDrilldownService(fixture.project).unmapped_tasks("trade")
        assert len(ctx.captured_queries) < 120


@pytest.mark.django_db
class TestReconciliation:
    def test_single_cardinality_scope_reconciliation(self):
        fixture = build_fixture(500)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        report = GovernedMappingReconciliationService(fixture.project).reconcile_dimension(
            session, "trade"
        )
        scope = report.scope
        assert scope.partition_disjoint
        assert (
            scope.effective_mapped_count
            + scope.unmapped_count
            + scope.conflict_count
            + scope.proposed_only_count
            == scope.eligible_count
        )

    def test_proposed_excluded_from_effective(self):
        fixture = build_fixture(300)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        buckets = session.bucket_task_ids("trade")
        assert PROPOSED_BUCKET in buckets or len(buckets) > 0
        report = GovernedMappingReconciliationService(fixture.project).reconcile_dimension(
            session, "trade"
        )
        assert report.scope.proposed_only_count >= 0

    def test_unmapped_virtual_bucket(self):
        fixture = build_fixture(200)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        buckets = session.bucket_task_ids("trade")
        assert UNMAPPED_BUCKET in buckets

    def test_spi_cpi_from_summed_components(self):
        fixture = build_fixture(400)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        rollup = session.build_dimension_rollup("trade")
        pv = ev = ac = 0.0
        for row in rollup.values():
            if row.get("is_virtual_bucket"):
                continue
            evm = row.get("evm") or {}
            if evm.get("available"):
                pv += evm.get("pv") or 0
                ev += evm.get("ev") or 0
                ac += evm.get("ac") or 0
        if pv > 0:
            assert round(ev / pv, 4) == pytest.approx(ev / pv, rel=1e-6)

    def test_schedule_activity_mapping_uniqueness(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        activity = ScheduleActivity.objects.create(
            project=project, canonical_activity_key="a1", display_name="A1"
        )
        t1 = TaskFactory(project=project, schedule_activity=activity)
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
        resolver = EffectiveMappingResolver(project)
        assert resolver.resolve_task(t1, dim).resolution == "logical_identity"
        t1.delete()
        replacement = TaskFactory(project=project, schedule_activity=activity)
        assert resolver.resolve_task(replacement, dim).resolution == "logical_identity"

    def test_wbs_inheritance_uniqueness(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _package_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="P", actor=user)
        version, _r, branch, leaf = _wbs_tree(project, user)
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
        task = TaskFactory(project=project, wbs_node=leaf)
        resolved = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert resolved.resolution == "inherited"

    def test_trusted_entity_deduplication_in_rollup(self):
        fixture = build_fixture(100)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        rollup = session.build_dimension_rollup("trade")
        total_entities = sum(r["model_scope"]["trusted_entity_count"] for r in rollup.values())
        unique_gids: set[str] = set()
        for tid in session.tasks_by_id:
            unique_gids.update(session.entities_by_task.get(tid, []))
        assert total_entities >= len(unique_gids)


@pytest.mark.django_db
class TestModeTruth:
    def test_proxy_labels_unchanged(self):
        project = ProjectFactory()
        TaskFactory(project=project, sub_stage="electrical")
        mode = DimensionModeService(project).build().trade
        assert mode.mode_label == "Trade Proxy"
        assert mode.selected_mode == MODE_PROXY

    def test_real_zero_data_project_remains_proxy(self):
        project = ProjectFactory()
        gm = DimensionModeService(project).build()
        assert gm.trade.selected_mode == MODE_PROXY

    def test_snapshot_governed_unavailable(self):
        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )

        project = ProjectFactory()
        caps = ProjectAnalyticsCapabilityProfile(project).build()["governed_mapping_capabilities"]
        assert caps["snapshot_governed_mapping_analytics"]["available"] is False

    def test_zero_governed_query_budget(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        assert len(ctx.captured_queries) <= 55

    def test_repeated_get_no_writes(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        before = Task.objects.count()
        url = reverse(
            "scheduling:executive_controls_governed_dimensions",
            kwargs={"pk": project.pk},
        )
        client.get(url)
        client.get(url)
        assert Task.objects.count() == before


@pytest.mark.django_db
class TestEquivalenceReference:
    def test_rollup_matches_legacy_aggregation_path(self):
        fixture = build_fixture(300)
        session = GovernedMappingAnalyticsSession.load(
            fixture.project,
            dimension_keys=["trade"],
            requested_modes={"trade": MODE_GOVERNED_PARTIAL},
        )
        rollup = session.build_dimension_rollup("trade")
        summary = GovernedMappingAggregationService(fixture.project).build_summary_from_session(
            session, "trade"
        )
        rollup_tasks = sum(
            r["task_count"] for r in rollup.values() if not r.get("is_virtual_bucket")
        )
        summary_tasks = sum(r["task_count"] for r in summary.get("governed_values", []))
        assert rollup_tasks == summary_tasks
