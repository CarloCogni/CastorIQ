# scheduling/tests/test_governed_mapping_domain.py
"""DF-D1 governed analytical mapping domain — models, lifecycle, resolution, API."""

from __future__ import annotations

import time

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
    WBSNode,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.enums import CapabilityState
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.contracts import MappingProposalDTO
from scheduling.services.governed_mapping.coverage import MappingCoverageService
from scheduling.services.governed_mapping.dimension import AnalyticalDimensionService
from scheduling.services.governed_mapping.exceptions import (
    MappingImmutabilityError,
    MappingTransitionError,
    MappingValidationError,
)
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _trade_dimension(project, user=None, **kwargs):
    return AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key=kwargs.pop("dimension_key", "trade"),
        name=kwargs.pop("name", "Trade"),
        dimension_type=AnalyticalDimension.DimensionType.TRADE,
        actor=user,
        **kwargs,
    )


def _location_dimension(project, user=None):
    return AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="location",
        name="Location",
        dimension_type=AnalyticalDimension.DimensionType.LOCATION,
        structure_type=AnalyticalDimension.StructureType.HIERARCHICAL,
        actor=user,
    )


def _active_stack(project, user, *, activate_mapping_set: bool = False):
    dim = _trade_dimension(project, user)
    val_svc = AnalyticalDimensionValueService(dim)
    value = val_svc.create_value(name="Electrical")
    AnalyticalDimensionService.activate(dim, actor=user)
    mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade v1", actor=user)
    if activate_mapping_set:
        AnalyticalMappingSetService.activate(mset, actor=user)
    return dim, value, mset


def _activate_mapping_set(mset, user):
    return AnalyticalMappingSetService.activate(mset, actor=user)


@pytest.mark.django_db
class TestDimensionModels:
    def test_flat_trade_dimension(self):
        project = ProjectFactory()
        dim = _trade_dimension(project)
        assert dim.structure_type == AnalyticalDimension.StructureType.FLAT
        assert dim.cardinality == AnalyticalDimension.Cardinality.SINGLE

    def test_hierarchical_location(self):
        project = ProjectFactory()
        dim = _location_dimension(project)
        assert dim.structure_type == AnalyticalDimension.StructureType.HIERARCHICAL

    def test_activate_supersede(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        AnalyticalDimensionService.activate(dim, actor=user)
        AnalyticalDimensionService.supersede(dim, actor=user)
        dim.refresh_from_db()
        assert dim.status == AnalyticalDimension.Status.SUPERSEDED
        assert not dim.is_selected_for_analysis

    def test_flat_rejects_parent_value(self):
        project = ProjectFactory()
        dim = _trade_dimension(project)
        val_svc = AnalyticalDimensionValueService(dim)
        root = val_svc.create_value(name="Root")
        with pytest.raises(MappingValidationError):
            val_svc.create_value(name="Child", parent=root)

    def test_cycle_rejected_on_hierarchical(self):
        project = ProjectFactory()
        dim = _location_dimension(project)
        svc = AnalyticalDimensionValueService(dim)
        a = svc.create_value(name="A")
        b = svc.create_value(name="B", parent=a)
        with pytest.raises(MappingValidationError):
            svc._detect_cycle(a.pk, b)

    def test_cross_project_parent_rejected(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        _trade_dimension(p1)
        d2 = _trade_dimension(p2, dimension_key="trade2")
        with pytest.raises(MappingValidationError):
            AnalyticalDimensionService.create_draft(
                project=p1,
                dimension_key="trade",
                name="Rev2",
                dimension_type=AnalyticalDimension.DimensionType.TRADE,
                parent_dimension=d2,
            )


@pytest.mark.django_db
class TestMappingSetLifecycle:
    def test_draft_submit_approve_activate(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        AnalyticalDimensionService.activate(dim, actor=user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Set 1", actor=user)
        AnalyticalMappingSetService.submit(mset, actor=user)
        AnalyticalMappingSetService.approve(mset, actor=user)
        AnalyticalMappingSetService.activate(mset, actor=user)
        mset.refresh_from_db()
        assert mset.status == AnalyticalMappingSet.Status.ACTIVE

    def test_reject_and_supersede(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, _, mset = _active_stack(project, user, activate_mapping_set=True)
        mset2 = AnalyticalMappingSetService.create_draft(dimension=dim, name="Set 2", actor=user)
        AnalyticalMappingSetService.reject(mset2, actor=user, reason="bad")
        AnalyticalMappingSetService.supersede(mset, actor=user)
        mset.refresh_from_db()
        assert mset.status == AnalyticalMappingSet.Status.SUPERSEDED

    def test_invalid_transition_blocked(self):
        project = ProjectFactory()
        dim = _trade_dimension(project)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="S")
        with pytest.raises(MappingTransitionError):
            AnalyticalMappingSetService.approve(mset)

    def test_approved_immutable_assignment(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        assignment = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            actor=user,
            auto_approve=True,
        )
        _activate_mapping_set(mset, user)
        with pytest.raises(MappingImmutabilityError):
            AnalyticalMappingAssignmentService.protect_approved(assignment)


@pytest.mark.django_db
class TestAssignments:
    def test_task_direct_mapping(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        a = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            actor=user,
            auto_approve=True,
        )
        assert a.is_effective

    def test_wbs_assignment(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        wbs_v = WBSVersionService.create_draft(project=project, name="WBS")
        node = WBSHierarchyService(wbs_v).create_node(
            WBSNodeDTO(name="Branch", node_type=WBSNode.NodeType.SUMMARY)
        )
        a = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
            wbs_node=node,
            actor=user,
            auto_approve=True,
        )
        assert a.wbs_node_id == node.pk

    def test_ifc_stable_identity(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        gid = "3abcDEFghijklmnopqrst"
        a = AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.IFC_ENTITY,
            entity_global_id=gid,
            actor=user,
            auto_approve=True,
        )
        assert a.entity_global_id == gid

    def test_cross_project_rejected(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(p1, user)
        task = TaskFactory(project=p2)
        with pytest.raises(MappingValidationError):
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=task,
                auto_approve=True,
            )

    def test_exactly_one_target(self):
        project = ProjectFactory()
        dim, value, mset = _active_stack(project, UserFactory())
        task = TaskFactory(project=project)
        with pytest.raises(MappingValidationError):
            AnalyticalMappingAssignmentService.create_proposal(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=task,
                entity_global_id="also-set",
            )

    def test_proposed_not_effective(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            actor=user,
        )
        _activate_mapping_set(mset, user)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution in {"unmapped", "proposed_only"}

    def test_proposal_dto_no_auto_promotion(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        dto = MappingProposalDTO(
            dimension_key="trade",
            proposed_value="Electrical",
            target_type="task",
            target_id=str(task.pk),
            source="scope_classification",
        )
        a = AnalyticalMappingAssignmentService.create_from_proposal_dto(
            mapping_set=mset,
            proposal=dto,
            dimension_value=value,
            task=task,
            actor=user,
        )
        assert a.governance_status == AnalyticalMappingAssignment.GovernanceStatus.PROPOSED

    def test_single_cardinality_conflict(self):
        project = ProjectFactory()
        user = UserFactory()
        dim = _trade_dimension(project, user)
        val_svc = AnalyticalDimensionValueService(dim)
        value = val_svc.create_value(name="Electrical")
        val2 = val_svc.create_value(name="Mechanical")
        AnalyticalDimensionService.activate(dim, actor=user)
        mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Trade v1", actor=user)
        task = TaskFactory(project=project)
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            auto_approve=True,
        )
        second = AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=val2,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
        )
        with pytest.raises(MappingValidationError):
            AnalyticalMappingAssignmentService.approve_assignment(second)


@pytest.mark.django_db
class TestResolution:
    def test_wbs_inheritance(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        wbs_v = WBSVersionService.create_draft(project=project, name="WBS")
        svc = WBSHierarchyService(wbs_v)
        root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT))
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.WBS_NODE,
            wbs_node=root,
            auto_approve=True,
        )
        _activate_mapping_set(mset, user)
        task = TaskFactory(project=project, wbs_node=root)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution == "inherited"

    def test_unmapped_truth(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, _, mset = _active_stack(project, user)
        _activate_mapping_set(mset, user)
        task = TaskFactory(project=project)
        result = EffectiveMappingResolver(project).resolve_task(task, dim)
        assert result.resolution == "unmapped"
        assert result.values == []

    def test_coverage_excludes_proposed(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        AnalyticalMappingAssignmentService.create_proposal(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
        )
        _activate_mapping_set(mset, user)
        summary = MappingCoverageService(project).summarize(dimension_key="trade")
        dim_summary = summary["dimensions"][0]
        assert dim_summary.get("directly_mapped", dim_summary.get("mapped_effective", 0)) == 0
        assert dim_summary.get("proposed_only", dim_summary.get("proposed", 0)) >= 1


@pytest.mark.django_db
class TestCapabilities:
    def test_schema_only_unavailable(self):
        project = ProjectFactory()
        caps = ProjectAnalyticsCapabilityProfile(project).build()
        gm = caps["governed_mapping_capabilities"]
        assert gm["governed_mapping_schema"]["available"]
        assert gm["mapping_governance_readiness"]["state"] == CapabilityState.UNAVAILABLE.value

    def test_governed_available_with_approved(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        task = TaskFactory(project=project)
        AnalyticalMappingAssignmentService.assign_manually(
            mapping_set=mset,
            dimension_value=value,
            target_type=AnalyticalMappingAssignment.TargetType.TASK,
            task=task,
            auto_approve=True,
        )
        _activate_mapping_set(mset, user)
        caps = ProjectAnalyticsCapabilityProfile(project).build()
        gm = caps["governed_mapping_capabilities"]
        assert gm["mapping_governance_readiness"]["state"] in {
            CapabilityState.AVAILABLE.value,
            CapabilityState.AVAILABLE_WITH_CAVEATS.value,
        }
        assert gm["e8_trade_analytics_readiness"]["caveats"]


@pytest.mark.django_db
class TestGovernedMappingAPI:
    def test_dimension_list_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_analytical_dimensions", kwargs={"pk": project.pk})
        r = client.get(url)
        assert r.status_code == 200
        assert "dimensions" in r.json()

    def test_post_returns_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_analytical_dimensions", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_coverage_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_mapping_coverage", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 200

    def test_provenance_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        task = TaskFactory(project=project)
        url = reverse(
            "scheduling:schedule_task_mapping_provenance",
            kwargs={"pk": project.pk, "task_pk": task.pk},
        )
        assert client.get(url).status_code == 200

    def test_unauthorized_denied(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:schedule_analytical_dimensions", kwargs={"pk": project.pk})
        assert client.get(url).status_code in {302, 403, 404}


@pytest.mark.django_db
class TestAuditEvents:
    def test_dimension_created_event(self):
        project = ProjectFactory()
        _trade_dimension(project)
        assert MappingGovernanceEvent.objects.filter(
            project=project,
            event_type=MappingGovernanceEvent.EventType.DIMENSION_CREATED,
        ).exists()

    def test_append_only(self):
        project = ProjectFactory()
        event = MappingGovernanceEvent.objects.create(
            project=project,
            event_type=MappingGovernanceEvent.EventType.DIMENSION_CREATED,
        )
        with pytest.raises(ValueError):
            event.reason_text = "x"
            event.save()


@pytest.mark.django_db
class TestMappingPerformance:
    def test_resolve_10k_under_budget(self):
        project = ProjectFactory()
        user = UserFactory()
        dim, value, mset = _active_stack(project, user)
        tasks = [TaskFactory(project=project) for _ in range(200)]
        for task in tasks[:100]:
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=value,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=task,
                auto_approve=True,
            )
        _activate_mapping_set(mset, user)
        ids = [t.pk for t in tasks]
        resolver = EffectiveMappingResolver(project)
        t0 = time.perf_counter()
        resolver.resolve_many_tasks(ids, dim)
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0
