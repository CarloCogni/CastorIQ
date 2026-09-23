# scheduling/tests/test_baseline_domain.py
"""DF-A2 baseline domain — models, lifecycle, population, comparison, API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    BaselineAuditEvent,
    BaselineTaskState,
    BaselineVersion,
    ScheduleActivity,
    ScheduleSourceVersion,
    Task,
)
from scheduling.services.baseline.comparison import BaselineComparisonService
from scheduling.services.baseline.exceptions import (
    BaselineTransitionError,
    BaselineValidationError,
)
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.baseline.population import (
    BaselinePopulationService,
    PopulationSourceMode,
    TaskStateDTO,
)
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _activity(project, key="p6xml:ext:100"):
    return ScheduleActivity.objects.create(
        project=project,
        canonical_activity_key=key,
        origin=ScheduleActivity.Origin.IMPORTED,
    )


def _task_with_activity(project, activity, **kwargs):
    defaults = {
        "project": project,
        "schedule_activity": activity,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 1, 10),
    }
    defaults.update(kwargs)
    return TaskFactory(**defaults)


def _source_version(project, user=None, status=ScheduleSourceVersion.Status.CURRENT):
    from django.utils import timezone

    return ScheduleSourceVersion.objects.create(
        project=project,
        version_number=1,
        source_type=Task.Source.XER,
        source_filename="test.xer",
        status=status,
        imported_at=timezone.now(),
        created_by=user,
    )


@pytest.mark.django_db
class TestBaselineModels:
    def test_baseline_type_choices(self):
        assert BaselineVersion.BaselineType.IMPORTED_REFERENCE
        assert BaselineVersion.Status.DRAFT

    def test_project_scoped_revision_uniqueness(self):
        project = ProjectFactory()
        BaselineVersion.objects.create(
            project=project,
            name="B1",
            baseline_type=BaselineVersion.BaselineType.WORKING,
            revision_number=1,
        )
        with pytest.raises(Exception):
            BaselineVersion.objects.create(
                project=project,
                name="B2",
                baseline_type=BaselineVersion.BaselineType.WORKING,
                revision_number=1,
            )

    def test_same_source_version_project_validation(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        sv = _source_version(p1)
        with pytest.raises(BaselineValidationError):
            BaselineVersionService.create_draft(
                project=p2,
                name="Bad",
                baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
                source_version=sv,
            )

    def test_parent_baseline_project_validation(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        parent = BaselineVersionService.create_draft(
            project=p1,
            name="Parent",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        with pytest.raises(BaselineValidationError):
            BaselineVersionService.create_draft(
                project=p2,
                name="Child",
                baseline_type=BaselineVersion.BaselineType.WORKING,
                parent_baseline=parent,
            )

    def test_one_selected_baseline_rule(self):
        project = ProjectFactory()
        user = UserFactory()
        b1 = BaselineVersionService.create_draft(
            project=project,
            name="A",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        b2 = BaselineVersionService.create_draft(
            project=project,
            name="B",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselineVersionService.publish(b1, actor=user)
        BaselineVersionService.publish(b2, actor=user)
        BaselineVersionService.select_for_analysis(b1, actor=user)
        BaselineVersionService.select_for_analysis(b2, actor=user)
        assert (
            BaselineVersion.objects.filter(project=project, is_selected_for_analysis=True).count()
            == 1
        )
        assert b2.is_selected_for_analysis

    def test_unique_task_state(self):
        project = ProjectFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="B",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineTaskState.objects.create(
            baseline_version=baseline,
            schedule_activity=activity,
            name_snapshot="T1",
        )
        with pytest.raises(Exception):
            BaselineTaskState.objects.create(
                baseline_version=baseline,
                schedule_activity=activity,
                name_snapshot="T2",
            )

    def test_nullable_cost_resource_values(self):
        project = ProjectFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="B",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        state = BaselineTaskState.objects.create(
            baseline_version=baseline,
            schedule_activity=activity,
            name_snapshot="T1",
            baseline_cost=None,
            planned_resource_units=None,
        )
        assert state.baseline_cost is None

    def test_published_immutability(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Orig",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        baseline.name = "Changed"
        with pytest.raises(ValueError):
            baseline.save()


@pytest.mark.django_db
class TestBaselineLifecycle:
    def test_create_draft(self):
        project = ProjectFactory()
        b = BaselineVersionService.create_draft(
            project=project,
            name="Draft",
            baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
        )
        assert b.status == BaselineVersion.Status.DRAFT
        assert BaselineAuditEvent.objects.filter(baseline_version=b).exists()

    def test_populate(self):
        project = ProjectFactory()
        activity = _activity(project)
        _task_with_activity(project, activity)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Pop",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        n = BaselinePopulationService.populate(
            baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL
        )
        assert n == 1

    def test_publish(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Pub",
            baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
        )
        BaselineVersionService.publish(baseline, actor=user)
        baseline.refresh_from_db()
        assert baseline.status == BaselineVersion.Status.PUBLISHED

    def test_approve(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="App",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.approve(baseline, actor=user, reason="PM sign-off")
        baseline.refresh_from_db()
        assert baseline.approved_at is not None

    def test_select(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Sel",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        assert baseline.is_selected_for_analysis

    def test_supersede(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Old",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        BaselineVersionService.supersede(baseline, actor=user)
        baseline.refresh_from_db()
        assert baseline.status == BaselineVersion.Status.SUPERSEDED
        assert not baseline.is_selected_for_analysis

    def test_archive(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Arc",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.archive(baseline, actor=user)
        assert baseline.status == BaselineVersion.Status.ARCHIVED

    def test_reject(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Rej",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.reject(baseline, actor=user)
        assert baseline.status == BaselineVersion.Status.REJECTED

    def test_invalid_transition_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="X",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        with pytest.raises(BaselineTransitionError):
            BaselineVersionService.publish(baseline, actor=user)

    def test_published_mutation_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="M",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineTaskState.objects.create(
            baseline_version=baseline,
            schedule_activity=activity,
            name_snapshot="T",
        )
        BaselineVersionService.publish(baseline, actor=user)
        state = baseline.task_states.first()
        state.name_snapshot = "Changed"
        with pytest.raises(ValueError):
            state.save()

    def test_revision_preserves_old_state(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = _activity(project)
        parent = BaselineVersionService.create_draft(
            project=project,
            name="Rev1",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineTaskState.objects.create(
            baseline_version=parent,
            schedule_activity=activity,
            name_snapshot="Original",
        )
        BaselineVersionService.publish(parent, actor=user)
        child = BaselineVersionService.create_draft(
            project=project,
            name="Rev2",
            baseline_type=BaselineVersion.BaselineType.WORKING,
            parent_baseline=parent,
        )
        BaselineTaskState.objects.create(
            baseline_version=child,
            schedule_activity=activity,
            name_snapshot="New",
        )
        parent_state = parent.task_states.first()
        assert parent_state.name_snapshot == "Original"

    def test_selection_transactional(self):
        project = ProjectFactory()
        user = UserFactory()
        b1 = BaselineVersionService.create_draft(
            project=project,
            name="S1",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        b2 = BaselineVersionService.create_draft(
            project=project,
            name="S2",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        for b in (b1, b2):
            BaselineVersionService.publish(b, actor=user)
        BaselineVersionService.select_for_analysis(b1, actor=user)
        BaselineVersionService.select_for_analysis(b2, actor=user)
        b1.refresh_from_db()
        assert not b1.is_selected_for_analysis


@pytest.mark.django_db
class TestBaselinePopulation:
    def test_bulk_task_state_creation(self):
        project = ProjectFactory()
        a1, a2 = _activity(project, "k1"), _activity(project, "k2")
        _task_with_activity(project, a1)
        _task_with_activity(project, a2, end_date=date(2025, 2, 1))
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Bulk",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        n = BaselinePopulationService.populate(
            baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL
        )
        assert n == 2

    def test_schedule_activity_identity(self):
        project = ProjectFactory()
        activity = _activity(project)
        _task_with_activity(project, activity)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Id",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL)
        assert baseline.task_states.first().schedule_activity_id == activity.pk

    def test_missing_field_remains_null(self):
        project = ProjectFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Null",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        dto = TaskStateDTO(
            schedule_activity_id=str(activity.pk),
            name_snapshot="No cost",
            planned_start=date(2025, 1, 1),
            planned_finish=date(2025, 1, 5),
            baseline_cost=None,
        )
        BaselinePopulationService.populate(
            baseline,
            mode=PopulationSourceMode.EXPLICIT_DTO,
            dtos=[dto],
        )
        assert baseline.task_states.first().baseline_cost is None

    def test_provenance_stored(self):
        project = ProjectFactory()
        activity = _activity(project)
        _task_with_activity(project, activity)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Prov",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL)
        prov = baseline.task_states.first().field_provenance
        assert prov.get("mode") == PopulationSourceMode.CURRENT_OPERATIONAL.value

    def test_duplicate_activities_blocked(self):
        project = ProjectFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Dup",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        dtos = [
            TaskStateDTO(schedule_activity_id=str(activity.pk), name_snapshot="A"),
            TaskStateDTO(schedule_activity_id=str(activity.pk), name_snapshot="B"),
        ]
        BaselinePopulationService.populate(
            baseline,
            mode=PopulationSourceMode.EXPLICIT_DTO,
            dtos=dtos[:1],
        )
        with pytest.raises(Exception):
            BaselineTaskState.objects.create(
                baseline_version=baseline,
                schedule_activity=activity,
                name_snapshot="Dup",
            )

    def test_population_modes_explicit(self):
        project = ProjectFactory()
        user = UserFactory()
        sv = _source_version(project, user)
        activity = _activity(project)
        _task_with_activity(project, activity, source_version=sv)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="SV",
            baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
            source_version=sv,
        )
        n = BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.SOURCE_VERSION)
        assert n == 1


@pytest.mark.django_db
class TestBaselineComparison:
    def test_matched_activity(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = _activity(project)
        _task_with_activity(project, activity)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Cmp",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL)
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        summary = BaselineComparisonService(project).summary()
        assert summary["matched_count"] == 1

    def test_new_current_activity(self):
        project = ProjectFactory()
        user = UserFactory()
        a1, a2 = _activity(project, "k1"), _activity(project, "k2")
        _task_with_activity(project, a1)
        _task_with_activity(project, a2)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="New",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        dto = TaskStateDTO(
            schedule_activity_id=str(a1.pk),
            name_snapshot="Only A",
            planned_start=date(2025, 1, 1),
            planned_finish=date(2025, 1, 5),
        )
        BaselinePopulationService.populate(
            baseline,
            mode=PopulationSourceMode.EXPLICIT_DTO,
            dtos=[dto],
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        summary = BaselineComparisonService(project).summary()
        assert summary["new_current_count"] == 1

    def test_baseline_only_activity(self):
        project = ProjectFactory()
        user = UserFactory()
        a1 = _activity(project, "only-baseline")
        _task_with_activity(project, _activity(project, "current-only"))
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Missing",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        dto = TaskStateDTO(
            schedule_activity_id=str(a1.pk),
            name_snapshot="Baseline only",
            planned_start=date(2025, 1, 1),
            planned_finish=date(2025, 1, 5),
        )
        BaselinePopulationService.populate(
            baseline,
            mode=PopulationSourceMode.EXPLICIT_DTO,
            dtos=[dto],
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        summary = BaselineComparisonService(project).summary()
        assert summary["baseline_only_count"] == 1

    def test_unresolved_identity(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = _activity(project)
        activity.identity_status = ScheduleActivity.IdentityStatus.UNRESOLVED
        activity.save()
        _task_with_activity(project, activity)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Unres",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL)
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        summary = BaselineComparisonService(project).summary()
        assert summary["unresolved_count"] == 1

    def test_finish_variance(self):
        current = date(2025, 1, 15)
        baseline_finish = date(2025, 1, 10)
        assert BaselineComparisonService.finish_variance_days(current, baseline_finish) == 5

    def test_cost_variance_unavailable_when_missing(self):
        assert BaselineComparisonService.cost_variance(Decimal("100"), None) is None
        assert BaselineComparisonService.cost_variance(None, Decimal("50")) is None

    def test_coverage_calculations(self):
        project = ProjectFactory()
        activity = _activity(project)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Cov",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        dto = TaskStateDTO(
            schedule_activity_id=str(activity.pk),
            name_snapshot="Dated",
            planned_start=date(2025, 1, 1),
            planned_finish=date(2025, 1, 5),
        )
        BaselinePopulationService.populate(
            baseline,
            mode=PopulationSourceMode.EXPLICIT_DTO,
            dtos=[dto],
        )
        cov = BaselinePopulationService.coverage_summary(baseline)
        assert cov["task_state_count"] == 1
        assert cov["dated_task_count"] == 1
        assert cov["cost_task_count"] == 0


@pytest.mark.django_db
class TestBaselineCapabilitiesE8:
    def test_legacy_baseline_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        bl = profile["baseline_capabilities"]
        assert bl["baseline_version_identity"]["state"] == "unavailable"

    def test_imported_reference_caveated(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Imp",
            baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        bl = ProjectAnalyticsCapabilityProfile(project).build()["baseline_capabilities"]
        assert bl["imported_reference_baseline"]["state"] == "available_with_caveats"

    def test_working_baseline_proxy(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Work",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        bl = ProjectAnalyticsCapabilityProfile(project).build()["baseline_capabilities"]
        assert bl["approved_baseline"]["available"] is False

    def test_approved_baseline_available(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="App",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.approve(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        bl = ProjectAnalyticsCapabilityProfile(project).build()["baseline_capabilities"]
        assert bl["approved_baseline"]["available"] is True

    def test_e8_labels_type_status(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="E8",
            baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.select_for_analysis(baseline, actor=user)
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        ctx = AnalyticalContextService(project).build(profile)
        assert ctx["selected_baseline"]["baseline_type"] == "imported_reference"
        assert "imported reference" in ctx["baseline_description"].lower()

    def test_historical_remains_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["capabilities"][FeatureId.HISTORICAL_SPI_TREND.value]["available"] is False

    def test_wbs_remains_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["capabilities"][FeatureId.WBS_MATRIX.value]["available"] is False


@pytest.mark.django_db
class TestBaselineAPI:
    def test_list_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        BaselineVersionService.create_draft(
            project=project,
            name="API",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        url = reverse("scheduling:schedule_baselines", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_detail_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        b = BaselineVersionService.create_draft(
            project=project,
            name="Det",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        url = reverse(
            "scheduling:schedule_baseline_detail",
            kwargs={"pk": project.pk, "baseline_pk": b.pk},
        )
        assert client.get(url).status_code == 200

    def test_selected_get(self, client):
        project = ProjectFactory()
        user = _member_client(client, project)
        b = BaselineVersionService.create_draft(
            project=project,
            name="Sel",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(b, actor=user)
        BaselineVersionService.select_for_analysis(b, actor=user)
        url = reverse("scheduling:schedule_baseline_selected", kwargs={"pk": project.pk})
        assert client.get(url).json()["selected"] is not None

    def test_comparison_get(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_baseline_comparison", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 200

    def test_pagination_cap(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_baselines", kwargs={"pk": project.pk})
        resp = client.get(url, {"page_size": "999"})
        assert resp.json()["pagination"]["page_size"] <= 50

    def test_post_policy(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_baselines", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_unauthorized_denied(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:schedule_baselines", kwargs={"pk": project.pk})
        assert client.get(url).status_code in (302, 403)

    def test_cross_project_denied(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        _member_client(client, p1)
        b = BaselineVersionService.create_draft(
            project=p2,
            name="Other",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        url = reverse(
            "scheduling:schedule_baseline_detail",
            kwargs={"pk": p1.pk, "baseline_pk": b.pk},
        )
        assert client.get(url).status_code == 404

    def test_repeated_get_no_writes(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:schedule_baseline_comparison", kwargs={"pk": project.pk})
        before = BaselineAuditEvent.objects.count()
        client.get(url)
        client.get(url)
        assert BaselineAuditEvent.objects.count() == before


@pytest.mark.django_db
class TestBaselineSecurity:
    def test_rejected_cannot_select(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Rej",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.reject(baseline, actor=user)
        with pytest.raises(BaselineTransitionError):
            BaselineVersionService.select_for_analysis(baseline, actor=user)

    def test_archived_cannot_select(self):
        project = ProjectFactory()
        user = UserFactory()
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Arc",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselineVersionService.publish(baseline, actor=user)
        BaselineVersionService.archive(baseline, actor=user)
        with pytest.raises(BaselineTransitionError):
            BaselineVersionService.select_for_analysis(baseline, actor=user)

    def test_cross_project_activity_rejected(self):
        p1, p2 = ProjectFactory(), ProjectFactory()
        activity = _activity(p2)
        baseline = BaselineVersionService.create_draft(
            project=p1,
            name="X",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        with pytest.raises(BaselineValidationError):
            BaselinePopulationService.populate(
                baseline,
                mode=PopulationSourceMode.EXPLICIT_DTO,
                dtos=[
                    TaskStateDTO(
                        schedule_activity_id=str(activity.pk),
                        name_snapshot="Wrong project",
                    )
                ],
            )

    def test_rejected_source_version_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        sv = _source_version(project, user, status=ScheduleSourceVersion.Status.REJECTED)
        with pytest.raises(BaselineValidationError):
            BaselineVersionService.create_draft(
                project=project,
                name="Bad SV",
                baseline_type=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
                source_version=sv,
            )

    def test_task_replacement_leaves_baseline_intact(self):
        project = ProjectFactory()
        user = UserFactory()
        activity = _activity(project)
        task = _task_with_activity(project, activity, name="Original")
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Persist",
            baseline_type=BaselineVersion.BaselineType.WORKING,
        )
        BaselinePopulationService.populate(baseline, mode=PopulationSourceMode.CURRENT_OPERATIONAL)
        BaselineVersionService.publish(baseline, actor=user)
        task.delete()
        new_task = _task_with_activity(project, activity, name="Replacement")
        state = baseline.task_states.first()
        assert state.name_snapshot == "Original"
        assert new_task.name == "Replacement"

    def test_legacy_project_operational(self):
        project = ProjectFactory()
        TaskFactory(project=project)
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["capabilities"][FeatureId.SCHEDULE_OVERVIEW.value]["available"] is True
