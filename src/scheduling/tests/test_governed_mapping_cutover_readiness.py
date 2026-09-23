# scheduling/tests/test_governed_mapping_cutover_readiness.py
"""DF-D2.1 governed mapping cutover readiness closure tests."""

from __future__ import annotations

import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    WBSNode,
)
from scheduling.services.governed_mapping.adapters.activity_type import ActivityTypeSourceAdapter
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.contracts import WBSBranchMappingPolicyDTO
from scheduling.services.governed_mapping.cutover_readiness import (
    CutoverReadinessService,
)
from scheduling.services.governed_mapping.dimension import AnalyticalDimensionService
from scheduling.services.governed_mapping.exceptions import MappingValidationError
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.population import GovernedMappingPopulationService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.review import MappingReviewService
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService
from scheduling.services.governed_mapping.wbs_branch_policy import WBSBranchMappingPolicyService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory

IBS_ID = "eb3b0c76-4812-4ce0-8927-ad85a111763a"


def _trade_dimension(project, user=None):
    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="trade",
        name="Trade",
        dimension_type=AnalyticalDimension.DimensionType.TRADE,
        actor=user,
    )
    AnalyticalDimensionValueService(dim).create_value(name="Electrical", code="electrical")
    AnalyticalDimensionService.activate(dim, actor=user)
    return dim


def _package_dimension(project, user=None):
    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="package",
        name="Package",
        dimension_type=AnalyticalDimension.DimensionType.PACKAGE,
        actor=user,
    )
    AnalyticalDimensionValueService(dim).create_value(name="Envelope", code="envelope")
    AnalyticalDimensionService.activate(dim, actor=user)
    return dim


def _wbs_tree(project, user=None):
    version = WBSVersionService.create_draft(project=project, name="Canonical WBS", actor=user)
    svc = WBSHierarchyService(version)
    root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT))
    branch = svc.create_node(
        WBSNodeDTO(name="Envelope", code="ENV", node_type=WBSNode.NodeType.SUMMARY),
        parent=root,
    )
    leaf = svc.create_node(
        WBSNodeDTO(name="Facade", code="FAC", node_type=WBSNode.NodeType.WORK_PACKAGE),
        parent=branch,
    )
    WBSVersionService.activate(version, actor=user)
    version.is_selected_for_analysis = True
    version.save(update_fields=["is_selected_for_analysis"])
    return version, root, branch, leaf


@pytest.mark.django_db
class TestActivityTypeSemantics:
    def test_p6_planner_type_not_authoritative_for_trade(self):
        project = ProjectFactory()
        _trade_dimension(project)
        TaskFactory(project=project, activity_type="Task Dependent")
        adapter = ActivityTypeSourceAdapter(project, dimension_key="trade")
        assert adapter.collect_authoritative() == []
        assert adapter.collect_proposals() == []

    def test_scope_token_proposal_blocked_for_trade_without_config(self):
        project = ProjectFactory()
        _trade_dimension(project)
        TaskFactory(project=project, activity_type="Procurement Package")
        adapter = ActivityTypeSourceAdapter(project, dimension_key="trade")
        assert adapter.collect_authoritative() == []
        assert adapter.collect_proposals() == []

    def test_configured_authoritative_only_with_explicit_map(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        dim.source_metadata = {
            "authoritative_sources": {
                "activity_type_scope": {"token_map": {"procurement": "electrical"}},
            }
        }
        dim.save(update_fields=["source_metadata"])
        TaskFactory(project=project, activity_type="Procurement activity")
        adapter = ActivityTypeSourceAdapter(project, dimension_key="trade")
        rows = adapter.collect_authoritative()
        assert len(rows) == 1
        assert rows[0].value_code == "electrical"
        assert rows[0].authority == "configured_authoritative"

    def test_write_authoritative_rejects_unconfigured_activity_type(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, activity_type="Procurement")
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        with pytest.raises(MappingValidationError):
            GovernedMappingPopulationService(project, actor=user).run_adoption(
                source="activity_type_authoritative",
                dimension_key="trade",
                write_authoritative=True,
                mapping_set_id=str(mset.pk),
            )

    def test_no_package_authority_from_activity_type(self):
        project = ProjectFactory()
        _package_dimension(project)
        TaskFactory(project=project, activity_type="Procurement")
        adapter = ActivityTypeSourceAdapter(project, dimension_key="package")
        assert adapter.collect_authoritative() == []
        assert adapter.collect_proposals() == []


@pytest.mark.django_db
class TestWBSBranchPolicy:
    def test_explicit_branch_mapping_and_inheritance(self):
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
            reason="Explicit envelope branch policy",
        )
        result = WBSBranchMappingPolicyService(project).apply_policy(
            policy, actor=user, auto_approve=True
        )
        assert result.created
        assert not result.conflict
        task = TaskFactory(project=project, wbs_node=leaf)
        AnalyticalMappingSetService.activate(mset, actor=user)
        resolved = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert resolved.resolution == "inherited"

    def test_no_implicit_inheritance_without_policy(self):
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
            include_descendants=False,
            target_behavior="map_wbs_node",
            reason="Node only",
        )
        WBSBranchMappingPolicyService(project).apply_policy(policy, actor=user, auto_approve=True)
        AnalyticalMappingSetService.activate(mset, actor=user)
        task = TaskFactory(project=project, wbs_node=leaf)
        assert EffectiveMappingResolver(project).resolve_task(task, dim).resolution == "unmapped"

    def test_cross_project_blocked(self):
        project = ProjectFactory()
        other = ProjectFactory()
        user = UserFactory()
        dim = _package_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Pkg", actor=user)
        version, _r, branch, _l = _wbs_tree(other, user)
        value = dim.values.get(code="envelope")
        policy = WBSBranchMappingPolicyDTO(
            dimension_key="package",
            mapping_set_id=str(mset.pk),
            wbs_version_id=str(version.pk),
            wbs_node_id=str(branch.pk),
            dimension_value_id=str(value.pk),
        )
        with pytest.raises(MappingValidationError):
            WBSBranchMappingPolicyService(project).apply_policy(policy, actor=user)

    def test_overlapping_policy_conflict(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = AnalyticalDimensionService.create_draft(
            project=project,
            dimension_key="package",
            name="Package",
            dimension_type=AnalyticalDimension.DimensionType.PACKAGE,
            actor=user,
        )
        val_a = AnalyticalDimensionValueService(dim).create_value(name="Envelope", code="envelope")
        val_b = AnalyticalDimensionValueService(dim).create_value(
            name="Structure", code="structure"
        )
        AnalyticalDimensionService.activate(dim, actor=user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Pkg", actor=user)
        version, root, branch, _leaf = _wbs_tree(project, user)
        svc = WBSBranchMappingPolicyService(project)
        p1 = WBSBranchMappingPolicyDTO(
            dimension_key="package",
            mapping_set_id=str(mset.pk),
            wbs_version_id=str(version.pk),
            wbs_node_id=str(root.pk),
            dimension_value_id=str(val_a.pk),
        )
        svc.apply_policy(p1, actor=user)
        p2 = WBSBranchMappingPolicyDTO(
            dimension_key="package",
            mapping_set_id=str(mset.pk),
            wbs_version_id=str(version.pk),
            wbs_node_id=str(branch.pk),
            dimension_value_id=str(val_b.pk),
        )
        conflict = svc.apply_policy(p2, actor=user)
        assert conflict.conflict


@pytest.mark.django_db
class TestGovernedJourneys:
    def test_trade_proposal_to_active_effective(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade", actor=user)
        task = TaskFactory(project=project, sub_stage="electrical")
        adoption = GovernedMappingPopulationService(project, actor=user).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        assert adoption.adoption.proposals_created >= 1
        proposal = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mset, governance_status="proposed"
        ).first()
        MappingReviewService.submit_for_review(proposal, actor=user)
        MappingReviewService.bulk_approve([proposal.pk], actor=user)
        AnalyticalMappingSetService.activate(mset, actor=user)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution == "direct"
        readiness = CutoverReadinessService(project).summarize()
        assert readiness.trade_cutover_readiness.state in {
            "governed_partial",
            "governed_ready_with_caveats",
            "governed_ready",
        }

    def test_package_wbs_policy_journey(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _package_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Package", actor=user)
        version, _root, branch, leaf = _wbs_tree(project, user)
        value = dim.values.get(code="envelope")
        policy = WBSBranchMappingPolicyDTO(
            dimension_key="package",
            mapping_set_id=str(mset.pk),
            wbs_version_id=str(version.pk),
            wbs_node_id=str(branch.pk),
            dimension_value_id=str(value.pk),
            target_behavior="inherit_to_tasks",
        )
        WBSBranchMappingPolicyService(project).apply_policy(policy, actor=user, auto_approve=True)
        mapped = TaskFactory(project=project, wbs_node=leaf)
        unmapped = TaskFactory(project=project)
        AnalyticalMappingSetService.activate(mset, actor=user)
        assert EffectiveMappingResolver(project).resolve_task(mapped, dim).resolution == "inherited"
        assert (
            EffectiveMappingResolver(project).resolve_task(unmapped, dim).resolution == "unmapped"
        )
        readiness = CutoverReadinessService(project).summarize()
        assert readiness.package_cutover_readiness.state != "unavailable"

    def test_proposed_only_not_cutover_ready(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade", actor=user)
        task = TaskFactory(project=project, sub_stage="electrical")
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            actor=user,
        )
        readiness = CutoverReadinessService(project).summarize()
        assert readiness.trade_cutover_readiness.state == "proposals_only"


@pytest.mark.django_db
class TestCrossVersionReadiness:
    def test_ambiguous_activity_blocks_readiness(self):
        from scheduling.models import ScheduleActivity

        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade", actor=user)
        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key=f"act-{uuid.uuid4().hex[:8]}",
            identity_status=ScheduleActivity.IdentityStatus.ACTIVE,
        )
        TaskFactory.create_batch(2, project=project, schedule_activity=activity)
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
            schedule_activity=activity,
            auto_approve=True,
        )
        AnalyticalMappingSetService.activate(mset, actor=user)
        readiness = CutoverReadinessService(project).summarize()
        assert readiness.trade_cutover_readiness.cross_version_blocked >= 1
        assert readiness.trade_cutover_readiness.state in {"governed_partial", "proposals_only"}


@pytest.mark.django_db
class TestQueryBudget:
    def test_zero_governed_rows_budget_at_55(self, client):
        from django.urls import reverse

        from scheduling.tests.test_executive_overview import _member_client

        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        assert len(ctx.captured_queries) <= 55


@pytest.mark.django_db
class TestCutoverPerformance:
    def test_readiness_summary_under_one_second(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="T", actor=user)
        value = dim.values.get(code="electrical")
        for _ in range(200):
            task = TaskFactory(project=project)
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=task,
                auto_approve=True,
            )
        AnalyticalMappingSetService.activate(mset, actor=user)
        t0 = time.perf_counter()
        CutoverReadinessService(project).summarize()
        assert time.perf_counter() - t0 < 1.0


@pytest.mark.django_db
class TestIBSReadOnly:
    def test_ibs_counts_unchanged(self):
        from environments.models import Project
        from scheduling.models import (
            AnalyticalDimension,
            AnalyticalMappingAssignment,
            AnalyticalMappingSet,
            MappingGovernanceEvent,
        )

        if not Project.objects.filter(pk=IBS_ID).exists():
            pytest.skip("IBS not in database")
        before = {
            "dimensions": AnalyticalDimension.objects.filter(project_id=IBS_ID).count(),
            "sets": AnalyticalMappingSet.objects.filter(project_id=IBS_ID).count(),
            "assignments": AnalyticalMappingAssignment.objects.filter(
                mapping_set__project_id=IBS_ID
            ).count(),
            "events": MappingGovernanceEvent.objects.filter(project_id=IBS_ID).count(),
        }
        CutoverReadinessService(Project.objects.get(pk=IBS_ID)).summarize()
        after = {
            "dimensions": AnalyticalDimension.objects.filter(project_id=IBS_ID).count(),
            "sets": AnalyticalMappingSet.objects.filter(project_id=IBS_ID).count(),
            "assignments": AnalyticalMappingAssignment.objects.filter(
                mapping_set__project_id=IBS_ID
            ).count(),
            "events": MappingGovernanceEvent.objects.filter(project_id=IBS_ID).count(),
        }
        assert before == after
