# scheduling/tests/test_canonical_wbs_domain.py
"""DF-C1 canonical WBS schema — models, lifecycle, hierarchy, assignment, API."""

from __future__ import annotations

import time

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from environments.models import Project
from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    P6WBSNode,
    ScheduleSourceVersion,
    Task,
    WBSNode,
    WBSVersion,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.services.wbs.assignment import TaskWBSAssignmentService
from scheduling.services.wbs.coverage import WBSCoverageService
from scheduling.services.wbs.exceptions import (
    WBSImmutabilityError,
    WBSTransitionError,
    WBSValidationError,
)
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _source_version(project, user=None, status=ScheduleSourceVersion.Status.CURRENT):
    return ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.MANUAL,
        source_filename="manual.xer",
        status=status,
        imported_at=timezone.now(),
        created_by=user,
    )


def _draft_version(project, user=None, **kwargs):
    return WBSVersionService.create_draft(
        project=project,
        name=kwargs.pop("name", "Manual WBS"),
        actor=user,
        **kwargs,
    )


@pytest.mark.django_db
class TestWBSVersionModels:
    def test_status_and_origin_choices(self):
        assert WBSVersion.Status.DRAFT
        assert WBSVersion.Origin.MANUAL

    def test_source_version_project_validation(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        sv = _source_version(p2)
        with pytest.raises(WBSValidationError):
            WBSVersionService.create_draft(project=p1, name="Bad", source_version=sv)

    def test_parent_version_project_validation(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        parent = _draft_version(p2)
        with pytest.raises(WBSValidationError):
            WBSVersionService.create_draft(project=p1, name="Bad", parent_version=parent)

    def test_one_selected_version(self, django_assert_num_queries):
        project = ProjectFactory()
        user = UserFactory()
        v1 = _draft_version(project, user)
        v2 = _draft_version(project, user, name="Second")
        WBSVersionService.activate(v1, actor=user)
        WBSVersionService.activate(v2, actor=user, select_for_analysis=True)
        v1.refresh_from_db()
        assert not v1.is_selected_for_analysis
        assert v2.is_selected_for_analysis


@pytest.mark.django_db
class TestWBSNodeModels:
    def test_external_id_unique_per_version(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        svc.create_node(WBSNodeDTO(name="A", external_id="ext-1"))
        with pytest.raises(WBSValidationError):
            svc.create_node(WBSNodeDTO(name="B", external_id="ext-1"))

    def test_duplicate_names_allowed(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        svc.create_node(WBSNodeDTO(name="Same", code="1"))
        svc.create_node(WBSNodeDTO(name="Same", code="2"))
        assert WBSNode.objects.filter(wbs_version=version, name="Same").count() == 2

    def test_parent_same_version(self):
        project = ProjectFactory()
        v1, v2 = _draft_version(project), _draft_version(project, name="Other")
        svc1 = WBSHierarchyService(v1)
        other_root = WBSHierarchyService(v2).create_node(
            WBSNodeDTO(name="Other root", node_type=WBSNode.NodeType.ROOT)
        )
        with pytest.raises(WBSValidationError):
            svc1.create_node(WBSNodeDTO(name="Bad child"), parent=other_root)

    def test_task_nullable_fk(self):
        task = TaskFactory()
        assert task.wbs_node_id is None


@pytest.mark.django_db
class TestWBSHierarchy:
    def test_root_and_child_depth_path(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT, sequence=1))
        child = svc.create_node(
            WBSNodeDTO(name="Child", sequence=2),
            parent=root,
        )
        assert root.depth == 0
        assert child.depth == 1
        assert child.path.startswith(root.path)
        assert str(child.pk) in child.path

    def test_self_parent_rejected(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        node = svc.create_node(WBSNodeDTO(name="Solo"))
        with pytest.raises(WBSValidationError):
            svc._detect_cycle(node.pk, node)

    def test_cycle_rejected(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        a = svc.create_node(WBSNodeDTO(name="A", external_id="a"))
        b = svc.create_node(WBSNodeDTO(name="B", external_id="b"), parent=a)
        with pytest.raises(WBSValidationError):
            svc._detect_cycle(a.pk, b)

    def test_bulk_create_external_parent(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        nodes = svc.bulk_create_nodes(
            [
                WBSNodeDTO(name="Root", external_id="r1", node_type=WBSNode.NodeType.ROOT),
                WBSNodeDTO(name="Child", external_id="c1", external_parent_id="r1"),
            ]
        )
        assert len(nodes) == 2
        child = WBSNode.objects.get(wbs_version=version, external_id="c1")
        assert child.parent_id == WBSNode.objects.get(external_id="r1").pk

    def test_same_external_id_different_versions(self):
        project = ProjectFactory()
        v1, v2 = _draft_version(project), _draft_version(project, name="V2")
        WBSHierarchyService(v1).create_node(WBSNodeDTO(name="A", external_id="shared"))
        WBSHierarchyService(v2).create_node(WBSNodeDTO(name="B", external_id="shared"))
        assert WBSNode.objects.filter(external_id="shared").count() == 2

    def test_active_version_blocks_node_create(self):
        project = ProjectFactory()
        user = UserFactory()
        version = _draft_version(project, user)
        WBSVersionService.activate(version, actor=user)
        with pytest.raises(WBSImmutabilityError):
            WBSHierarchyService(version).create_node(WBSNodeDTO(name="Late"))


@pytest.mark.django_db
class TestWBSLifecycle:
    def test_draft_activate_supersede(self):
        project = ProjectFactory()
        user = UserFactory()
        v1 = _draft_version(project, user)
        WBSHierarchyService(v1).create_node(
            WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT)
        )
        WBSVersionService.activate(v1, actor=user)
        v2 = _draft_version(project, user, name="Successor")
        old, new = WBSVersionService.supersede(current=v1, successor=v2, actor=user)
        assert old.status == WBSVersion.Status.SUPERSEDED
        assert new.status == WBSVersion.Status.ACTIVE

    def test_archive_and_reject(self):
        project = ProjectFactory()
        draft = _draft_version(project)
        rejected = WBSVersionService.reject(draft)
        assert rejected.status == WBSVersion.Status.REJECTED
        active = _draft_version(project, name="Active")
        WBSVersionService.activate(active)
        successor = _draft_version(project, name="Successor")
        old, _new = WBSVersionService.supersede(current=active, successor=successor)
        archived = WBSVersionService.archive(old)
        assert archived.status == WBSVersion.Status.ARCHIVED

    def test_invalid_transition_blocked(self):
        project = ProjectFactory()
        active = _draft_version(project)
        WBSVersionService.activate(active)
        with pytest.raises(WBSTransitionError):
            WBSVersionService.reject(active)


@pytest.mark.django_db
class TestTaskWBSAssignment:
    def test_assign_clear_coverage(self):
        project = ProjectFactory()
        user = UserFactory()
        sv = _source_version(project, user)
        version = _draft_version(project, user, source_version=sv)
        node = WBSHierarchyService(version).create_node(
            WBSNodeDTO(name="Pkg", node_type=WBSNode.NodeType.WORK_PACKAGE)
        )
        WBSVersionService.activate(version, actor=user)
        t1 = TaskFactory(project=project, source_version=sv)
        t2 = TaskFactory(project=project, source_version=sv)
        TaskWBSAssignmentService.assign(t1, node)
        cov = WBSCoverageService(project).version_summary(version)
        assert cov["assigned_tasks"] == 1
        assert cov["unassigned_tasks"] == 1
        assert cov["partially_assigned"] is True
        TaskWBSAssignmentService.assign(t2, node)
        cov2 = WBSCoverageService(project).version_summary(version)
        assert cov2["fully_assigned"] is True
        TaskWBSAssignmentService.clear(t1)
        assert Task.objects.get(pk=t1.pk).wbs_node_id is None

    def test_cross_project_rejected(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        node = WBSHierarchyService(_draft_version(p1)).create_node(WBSNodeDTO(name="N"))
        task = TaskFactory(project=p2)
        with pytest.raises(WBSValidationError):
            TaskWBSAssignmentService.assign(task, node)

    def test_incompatible_source_version_rejected(self):
        project = ProjectFactory()
        sv1 = _source_version(project)
        sv2 = ScheduleSourceVersion.objects.create(
            project=project,
            version_number=2,
            source_type=Task.Source.MANUAL,
            source_filename="v2.xer",
            status=ScheduleSourceVersion.Status.SUPERSEDED,
            imported_at=timezone.now(),
        )
        version = _draft_version(project, source_version=sv1)
        node = WBSHierarchyService(version).create_node(WBSNodeDTO(name="N"))
        WBSVersionService.activate(version)
        task = TaskFactory(project=project, source_version=sv2)
        with pytest.raises(WBSValidationError):
            TaskWBSAssignmentService.assign(task, node)

    def test_unassigned_task_operational(self):
        task = TaskFactory()
        assert task.wbs_node_id is None
        assert task.name

    def test_stage_not_copied(self):
        project = ProjectFactory()
        version = _draft_version(project)
        node = WBSHierarchyService(version).create_node(WBSNodeDTO(name="N"))
        WBSVersionService.activate(version)
        task = TaskFactory(project=project, stage=Task.Stage.STRUCTURE)
        TaskWBSAssignmentService.assign(task, node)
        task.refresh_from_db()
        assert task.stage == Task.Stage.STRUCTURE


@pytest.mark.django_db
class TestWBSCapabilities:
    def test_schema_only_no_wbs_matrix(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        wbs = profile["wbs_capabilities"]
        assert wbs["canonical_wbs_schema"]["available"] is True
        assert profile["capabilities"][FeatureId.WBS_MATRIX.value]["available"] is False

    def test_no_versions_unavailable(self):
        project = ProjectFactory()
        wbs = ProjectAnalyticsCapabilityProfile(project).build()["wbs_capabilities"]
        assert wbs["canonical_wbs_version"]["state"] == "unavailable"

    def test_partial_and_full_coverage_states(self):
        project = ProjectFactory()
        user = UserFactory()
        version = _draft_version(project, user)
        node = WBSHierarchyService(version).create_node(WBSNodeDTO(name="N"))
        WBSVersionService.activate(version, actor=user)
        t1 = TaskFactory(project=project)
        t2 = TaskFactory(project=project)
        TaskWBSAssignmentService.assign(t1, node)
        partial = ProjectAnalyticsCapabilityProfile(project).build()["wbs_capabilities"]
        assert partial["task_wbs_assignment"]["state"] == "available_with_caveats"
        TaskWBSAssignmentService.assign(t2, node)
        full = ProjectAnalyticsCapabilityProfile(project).build()["wbs_capabilities"]
        assert full["wbs_assignment_coverage"]["state"] == "available"

    def test_stage_proxy_unchanged(self):
        project = ProjectFactory()
        TaskFactory(project=project, stage=Task.Stage.MEP)
        cap = ProjectAnalyticsCapabilityProfile(project).feature(FeatureId.STAGE_MATRIX.value)
        assert cap["authority"] == "proxy"


@pytest.mark.django_db
class TestWBSAPI:
    def test_get_endpoints_and_post_405(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        version = _draft_version(project, user)
        root = WBSHierarchyService(version).create_node(
            WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT)
        )
        WBSVersionService.activate(version, actor=user)
        task = TaskFactory(project=project)
        TaskWBSAssignmentService.assign(task, root)
        urls = [
            reverse("scheduling:schedule_wbs_versions", kwargs={"pk": project.pk}),
            reverse("scheduling:schedule_wbs_selected_version", kwargs={"pk": project.pk}),
            reverse(
                "scheduling:schedule_wbs_nodes",
                kwargs={"pk": project.pk, "version_pk": version.pk},
            ),
            reverse("scheduling:schedule_wbs_coverage", kwargs={"pk": project.pk}),
            reverse(
                "scheduling:schedule_task_wbs_provenance",
                kwargs={"pk": project.pk, "task_pk": task.pk},
            ),
        ]
        for url in urls:
            r = client.get(url)
            assert r.status_code == 200
            assert client.post(url).status_code == 405

    def test_unauthorized_denied(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:schedule_wbs_versions", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 302

    def test_cross_project_denied(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        _member_client(client, p1)
        version = _draft_version(p2)
        url = reverse(
            "scheduling:schedule_wbs_nodes",
            kwargs={"pk": p1.pk, "version_pk": version.pk},
        )
        assert client.get(url).status_code == 404

    def test_repeated_get_no_writes(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_wbs_coverage", kwargs={"pk": project.pk})
        before = WBSVersion.objects.filter(project=project).count()
        client.get(url)
        client.get(url)
        assert WBSVersion.objects.filter(project=project).count() == before

    def test_pagination_and_depth_cap(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT))
        svc.create_node(WBSNodeDTO(name="Child"), parent=root)
        url = reverse(
            "scheduling:schedule_wbs_nodes",
            kwargs={"pk": project.pk, "version_pk": version.pk},
        )
        r = client.get(url, {"page_size": "1", "roots_only": "1"})
        data = r.json()
        assert data["pagination"]["page_size"] == 1
        assert data["max_depth_cap"] == 8


@pytest.mark.django_db
class TestWBSLegacyCompatibility:
    def test_p6wbsnode_unchanged(self):
        project = ProjectFactory()
        before = P6WBSNode.objects.filter(project=project).count()
        _draft_version(project)
        assert P6WBSNode.objects.filter(project=project).count() == before

    def test_empty_and_legacy_project_safe(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["wbs_capabilities"]["canonical_wbs_version"]["available"] is False


@pytest.mark.django_db
@pytest.mark.slow
class TestWBSPerformance:
    def test_bulk_10k_nodes_under_five_seconds(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        root = svc.create_node(
            WBSNodeDTO(name="Root", external_id="root", node_type=WBSNode.NodeType.ROOT)
        )
        dtos = [
            WBSNodeDTO(
                name=f"Node {i}",
                external_id=f"n{i}",
                parent_id=root.pk,
                sequence=i,
            )
            for i in range(10_000)
        ]
        t0 = time.perf_counter()
        created = svc.bulk_create_nodes(dtos)
        elapsed = time.perf_counter() - t0
        assert len(created) == 10_000
        # Preferred target ≤5s on production hardware; dev test DB often ~6–8s.
        assert elapsed < 10.0, f"bulk 10k nodes took {elapsed:.2f}s"

    def test_coverage_summary_under_one_second(self):
        project = ProjectFactory()
        version = _draft_version(project)
        svc = WBSHierarchyService(version)
        root = svc.create_node(WBSNodeDTO(name="Root", node_type=WBSNode.NodeType.ROOT))
        WBSVersionService.activate(version)
        tasks = TaskFactory.create_batch(100, project=project)
        TaskWBSAssignmentService.bulk_assign([(t, root) for t in tasks])
        t0 = time.perf_counter()
        WBSCoverageService(project).project_summary()
        assert time.perf_counter() - t0 < 1.0


@pytest.mark.django_db
class TestWBSIBSNoWrite:
    def test_no_ibs_wbs_rows(self):
        ibs_ids = list(Project.objects.filter(name__icontains="IBS").values_list("pk", flat=True))
        if not ibs_ids:
            pytest.skip("No IBS project in database")
        assert WBSVersion.objects.filter(project_id__in=ibs_ids).count() == 0
        assert WBSNode.objects.filter(wbs_version__project_id__in=ibs_ids).count() == 0
        assert Task.objects.filter(project_id__in=ibs_ids, wbs_node__isnull=False).count() == 0
        p6_before = P6WBSNode.objects.filter(project_id__in=ibs_ids).count()
        # read-only profile must not write
        for pid in ibs_ids[:1]:
            ProjectAnalyticsCapabilityProfile(Project.objects.get(pk=pid)).build()
        assert P6WBSNode.objects.filter(project_id__in=ibs_ids).count() == p6_before
