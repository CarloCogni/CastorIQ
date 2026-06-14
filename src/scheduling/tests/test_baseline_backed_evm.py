# scheduling/tests/test_baseline_backed_evm.py
"""DF-A2.1 baseline-backed EVM integration tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import (
    BaselineTaskState,
    BaselineVersion,
    ScheduleActivity,
)
from scheduling.services.baseline.evm_scope import EVMMethodologyMode
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.baseline.population import (
    BaselinePopulationService,
    PopulationSourceMode,
    TaskStateDTO,
)
from scheduling.services.evm import compute_evm
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.current_evm_analytics import CurrentEVMAnalyticsService
from scheduling.services.executive_controls.derived_asof_scurve import DerivedAsOfSCurveService
from scheduling.services.executive_controls.enums import FeatureId
from scheduling.services.executive_controls.evm_filters import EVMFilters
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _activity(project, key="p6xml:ext:100"):
    return ScheduleActivity.objects.create(
        project=project,
        canonical_activity_key=key,
        origin=ScheduleActivity.Origin.IMPORTED,
    )


def _task(project, activity, *, cost=None, **kwargs):
    defaults = {
        "project": project,
        "schedule_activity": activity,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 1, 10),
        "status": "complete",
    }
    if cost is not None:
        defaults["cost"] = cost
    defaults.update(kwargs)
    return TaskFactory(**defaults)


def _baseline_with_states(
    project,
    user,
    *,
    btype=BaselineVersion.BaselineType.APPROVED,
    tasks_spec: list[tuple[ScheduleActivity, Decimal | None, date, date]],
    select=True,
):
    baseline = BaselineVersionService.create_draft(
        project=project,
        name="EVM Baseline",
        baseline_type=btype,
    )
    dtos = [
        TaskStateDTO(
            schedule_activity_id=str(a.pk),
            name_snapshot="Task",
            planned_start=ps,
            planned_finish=pf,
            baseline_cost=cost,
        )
        for a, cost, ps, pf in tasks_spec
    ]
    BaselinePopulationService.populate(
        baseline,
        mode=PopulationSourceMode.EXPLICIT_DTO,
        dtos=dtos,
    )
    BaselineVersionService.publish(baseline, actor=user)
    if btype == BaselineVersion.BaselineType.APPROVED:
        BaselineVersionService.approve(baseline, actor=user)
    if select:
        BaselineVersionService.select_for_analysis(baseline, actor=user)
    return baseline


@pytest.mark.django_db
class TestModeSelection:
    def test_approved_baseline_mode(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, Decimal("500"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"] == EVMMethodologyMode.APPROVED_BASELINE_COST_EVM
        )
        assert evm["bac"] == 500.0

    def test_imported_reference_mode(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        _baseline_with_states(
            project,
            user,
            btype=BaselineVersion.BaselineType.IMPORTED_REFERENCE,
            tasks_spec=[(a, Decimal("200"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"]
            == EVMMethodologyMode.REFERENCE_BASELINE_COST_EVM
        )

    def test_working_baseline_mode(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a)
        _baseline_with_states(
            project,
            user,
            btype=BaselineVersion.BaselineType.WORKING,
            tasks_spec=[(a, Decimal("150"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"] == EVMMethodologyMode.WORKING_BASELINE_COST_EVM
        )

    def test_no_baseline_derived_fallback(self):
        project = ProjectFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"]
            == EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM
        )
        assert evm["bac"] == 100.0

    def test_rejected_baseline_not_used(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        baseline = BaselineVersionService.create_draft(
            project=project,
            name="Rej",
            baseline_type=BaselineVersion.BaselineType.APPROVED,
        )
        BaselineTaskState.objects.create(
            baseline_version=baseline,
            schedule_activity=a,
            name_snapshot="T",
            planned_start=date(2025, 1, 1),
            planned_finish=date(2025, 1, 10),
            baseline_cost=Decimal("999"),
        )
        BaselineVersionService.reject(baseline, actor=user)
        baseline.is_selected_for_analysis = True
        baseline.save(update_fields=["is_selected_for_analysis"])
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"]
            == EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM
        )


@pytest.mark.django_db
class TestPVBACEV:
    def test_baseline_cost_drives_bac_not_task_cost(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, Decimal("800"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert evm["bac"] == 800.0
        assert evm["ev"] == 800.0

    def test_missing_baseline_cost_excluded(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, None, date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert (
            evm["baseline_evm"]["methodology_mode"] == EVMMethodologyMode.SCHEDULE_PERFORMANCE_MODE
        )

    def test_current_only_no_fabricated_baseline_cost(self):
        project = ProjectFactory()
        user = UserFactory()
        a1, a2 = _activity(project, "k1"), _activity(project, "k2")
        _task(project, a1, cost=50)
        _task(project, a2, cost=50)
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a1, Decimal("300"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert evm["bac"] == 300.0
        assert evm["baseline_evm"]["coverage"]["current_only_count"] >= 1

    def test_legacy_parity_without_baseline(self):
        project = ProjectFactory()
        _task(
            project,
            _activity(project),
            cost=100,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            status="complete",
        )
        evm = compute_evm(str(project.pk), as_of_date=date(2025, 1, 15))
        assert evm["use_cost"] is True
        assert evm["bac"] == 100.0
        assert evm["ev"] == 100.0


@pytest.mark.django_db
class TestCurveAndE8:
    def test_curve_historical_false(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100)
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, Decimal("400"), date(2025, 1, 1), date(2025, 3, 31))],
        )
        curve = DerivedAsOfSCurveService(project).build_scurve(EVMFilters())
        assert curve["historical"] is False
        assert (
            curve["baseline_evm"]["methodology_mode"]
            == EVMMethodologyMode.APPROVED_BASELINE_COST_EVM
        )

    def test_pv_provenance_baseline(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100, start_date=date(2025, 1, 1), end_date=date(2025, 3, 31))
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, Decimal("400"), date(2025, 1, 1), date(2025, 2, 28))],
        )
        curve = DerivedAsOfSCurveService(project).build_scurve(EVMFilters())
        pv_pts = curve["curves"]["pv"]["points"]
        assert pv_pts[0]["provenance"] == "baseline_task_state"

    def test_e8_current_includes_baseline_coverage(self):
        project = ProjectFactory()
        user = UserFactory()
        a = _activity(project)
        _task(project, a, cost=100, status="complete")
        _baseline_with_states(
            project,
            user,
            tasks_spec=[(a, Decimal("250"), date(2025, 1, 1), date(2025, 1, 10))],
        )
        payload = CurrentEVMAnalyticsService(project).build()
        assert "baseline_evm" in payload["coverage"]
        assert "Approved baseline" in payload["mode_label"]

    def test_historical_remains_unavailable(self):
        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        assert profile["baseline_capabilities"]["historical_evm"]["available"] is False
        assert profile["capabilities"][FeatureId.HISTORICAL_SPI_TREND.value]["available"] is False


@pytest.mark.django_db
class TestBaselineBackedAPISecurity:
    def test_evm_get_no_writes(self, client):
        project = ProjectFactory()
        user = UserFactory()
        ProjectMembershipFactory(project=project, user=user, permission="editor")
        client.force_login(user)
        from scheduling.models import BaselineAuditEvent

        before = BaselineAuditEvent.objects.count()
        url = reverse("scheduling:executive_controls_evm_current", kwargs={"pk": project.pk})
        client.get(url)
        client.get(url)
        assert BaselineAuditEvent.objects.count() == before

    def test_unauthorized_denied(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:executive_controls_evm_current", kwargs={"pk": project.pk})
        assert client.get(url).status_code in (302, 403)
