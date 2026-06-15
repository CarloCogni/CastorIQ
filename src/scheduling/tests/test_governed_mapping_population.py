# scheduling/tests/test_governed_mapping_population.py
"""DF-D2 governed mapping population, adoption, and schedule identity."""

from __future__ import annotations

import time
import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    ScheduleActivity,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.enums import CapabilityState
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.dimension import AnalyticalDimensionService
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.population import GovernedMappingPopulationService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.review import MappingReviewService
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService
from scheduling.tests.factories import TaskFactory

IBS_ID = "eb3b0c76-4812-4ce0-8927-ad85a111763a"


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _trade_stack(project, user=None):
    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="trade",
        name="Trade",
        dimension_type=AnalyticalDimension.DimensionType.TRADE,
        actor=user,
    )
    val_svc = AnalyticalDimensionValueService(dim)
    for code, name in [("electrical", "Electrical"), ("concrete", "Concrete")]:
        val_svc.create_value(name=name, code=code)
    AnalyticalDimensionService.activate(dim, actor=user)
    mset = AnalyticalMappingSetService.create_draft(
        dimension=dim, name="Trade population", actor=user
    )
    return dim, mset


@pytest.mark.django_db
class TestScheduleActivityTarget:
    def test_schedule_activity_assignment(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key="xer:ext:100",
            identity_status=ScheduleActivity.IdentityStatus.ACTIVE,
        )
        value = dim.values.get(code="electrical")
        assignment = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
            schedule_activity=activity,
            auto_approve=True,
        )
        assert assignment.schedule_activity_id == activity.pk

    def test_exactly_one_target_with_activity(self):
        project = ProjectFactory()
        dim, mset = _trade_stack(project)
        value = dim.values.first()
        with pytest.raises(Exception):
            AnalyticalMappingAssignmentService.create_proposal(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
                task=TaskFactory(project=project),
                schedule_activity=ScheduleActivity.objects.create(
                    project=project, canonical_activity_key="k1"
                ),
            )


@pytest.mark.django_db
class TestProposalAdoption:
    def test_dry_run_no_writes(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, sub_stage="electrical")
        dim, mset = _trade_stack(project, user)
        before = AnalyticalMappingAssignment.objects.count()
        result = GovernedMappingPopulationService(project, actor=user).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            dry_run=True,
            mapping_set_id=str(mset.pk),
        )
        assert result.adoption is not None
        assert result.adoption.dry_run
        assert AnalyticalMappingAssignment.objects.count() == before

    def test_write_proposals_only(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, sub_stage="electrical")
        dim, mset = _trade_stack(project, user)
        result = GovernedMappingPopulationService(project, actor=user).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        assert result.adoption.proposals_created >= 1
        a = AnalyticalMappingAssignment.objects.filter(mapping_set=mset).first()
        assert a.governance_status == AnalyticalMappingAssignment.GovernanceStatus.PROPOSED

    def test_idempotent_adoption(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, sub_stage="electrical")
        dim, mset = _trade_stack(project, user)
        svc = GovernedMappingPopulationService(project, actor=user)
        svc.run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        second = svc.run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            write_proposals=True,
            mapping_set_id=str(mset.pk),
        )
        assert second.adoption.duplicates_skipped >= 1

    def test_heuristic_authoritative_rejected(self):
        project = ProjectFactory()
        user = UserFactory()
        TaskFactory(project=project, sub_stage="electrical")
        dim, mset = _trade_stack(project, user)
        with pytest.raises(Exception):
            GovernedMappingPopulationService(project, actor=user).run_adoption(
                source="sub_stage_trade",
                dimension_key="trade",
                write_authoritative=True,
                mapping_set_id=str(mset.pk),
            )


@pytest.mark.django_db
class TestReviewAndResolution:
    def _approved_stack(self, project, user):
        dim, mset = _trade_stack(project, user)
        task = TaskFactory(project=project, sub_stage="electrical")
        value = dim.values.get(code="electrical")
        proposal = AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            actor=user,
        )
        MappingReviewService.submit_for_review(proposal, actor=user)
        MappingReviewService.bulk_approve([proposal.pk], actor=user)
        AnalyticalMappingSetService.activate(mset, actor=user)
        return dim, mset, task

    def test_bulk_approve(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset, task = self._approved_stack(project, user)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution == "direct"

    def test_schedule_activity_resolves_task(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key=f"act-{uuid.uuid4().hex[:8]}",
            identity_status=ScheduleActivity.IdentityStatus.ACTIVE,
        )
        task = TaskFactory(project=project, schedule_activity=activity)
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
            schedule_activity=activity,
            auto_approve=True,
        )
        AnalyticalMappingSetService.activate(mset, actor=user)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution == "logical_identity"
        assert result.provenance.cross_version_outcome

    def test_replacement_task_resolves_same_activity(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        activity = ScheduleActivity.objects.create(
            project=project,
            canonical_activity_key=f"act-{uuid.uuid4().hex[:8]}",
            identity_status=ScheduleActivity.IdentityStatus.ACTIVE,
        )
        TaskFactory(project=project, name="Old")
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
            schedule_activity=activity,
            auto_approve=True,
        )
        AnalyticalMappingSetService.activate(mset, actor=user)
        new_task = TaskFactory(project=project, schedule_activity=activity, name="New")
        result = EffectiveMappingResolver(project).resolve_task(new_task, dim)
        assert result.resolution == "logical_identity"

    def test_proposed_ignored(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        task = TaskFactory(project=project)
        value = dim.values.first()
        AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
        )
        AnalyticalMappingSetService.activate(mset, actor=user)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution in {"unmapped", "proposed_only"}


@pytest.mark.django_db
class TestCapabilitiesAndAPI:
    def test_proposed_only_unavailable(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        task = TaskFactory(project=project, sub_stage="electrical")
        value = dim.values.get(code="electrical")
        AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
        )
        caps = ProjectAnalyticsCapabilityProfile(project).build()
        gm = caps["governed_mapping_capabilities"]
        assert gm["mapping_governance_readiness"]["state"] != CapabilityState.AVAILABLE.value

    def test_adoption_diagnostics_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_mapping_adoption_diagnostics", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 200

    def test_post_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_mapping_adoption_diagnostics", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405


@pytest.mark.django_db
class TestIBSReadOnly:
    def test_ibs_dry_run_allowed(self):
        try:
            call_command(
                "adopt_governed_mappings",
                project=IBS_ID,
                dimension="trade",
                source="sub_stage_trade",
                dry_run=True,
            )
        except CommandError as exc:
            if "not found" in str(exc).lower():
                pytest.skip("IBS not in database")
            raise

    def test_ibs_write_blocked(self):
        try:
            with pytest.raises(CommandError, match="read-only"):
                call_command(
                    "adopt_governed_mappings",
                    project=IBS_ID,
                    dimension="trade",
                    source="sub_stage_trade",
                    write_proposals=True,
                )
        except CommandError as exc:
            if "not found" in str(exc).lower():
                pytest.skip("IBS not in database")


@pytest.mark.django_db
class TestMappingPopulationPerformance:
    def test_resolve_1k_under_budget(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, mset = _trade_stack(project, user)
        value = dim.values.get(code="electrical")
        tasks = []
        for _ in range(1000):
            t = TaskFactory(project=project)
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=t,
                auto_approve=True,
            )
            tasks.append(t)
        AnalyticalMappingSetService.activate(mset, actor=user)
        ids = [t.pk for t in tasks]
        t0 = time.perf_counter()
        EffectiveMappingResolver(project).resolve_many_tasks(ids, dim)
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0

    def test_5k_scaling_diagnostic(self):
        from scheduling.tests.mapping_population_benchmark_harness import run_benchmark

        results = run_benchmark((5000,))
        metrics = results["5000"]
        assert metrics["resolve_seconds"] < 5.0
        assert metrics["coverage_seconds"] < 1.0

    def test_10k_recorded(self):
        from scheduling.tests.mapping_population_benchmark_harness import run_benchmark

        results = run_benchmark((10000,))
        metrics = results["10000"]
        assert metrics["resolve_seconds"] < 5.0
        assert metrics["dry_run_seconds"] < 5.0
        # Recorded metrics for DF-D2 report (fixture setup excluded).
        print(f"DF-D2_BENCHMARK_10K={results}")
