# scheduling/tests/test_executive_controls.py
"""E8-A executive controls analytical foundation tests."""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.services.evm import compute_evm
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.coverage import AnalyticalCoverageService
from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.enums import DayType, DelayType, MetricAuthority
from scheduling.services.executive_controls.evm_availability import E8EVMAvailabilityService
from scheduling.services.executive_controls.methodology import (
    E8_METHODOLOGY_VERSION,
    E8_METRIC_REGISTRY,
    methodology_registry_payload,
)
from scheduling.services.executive_controls.resource_availability import (
    EQUIVALENT_WORKFORCE_LABEL,
    EquivalentWorkforceAvailabilityService,
)
from scheduling.services.executive_controls.scope_classification import ScopeClassificationResolver
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestMethodologyRegistry:
    """e8-v1 registry contract tests."""

    def test_e8_v1_registered(self):
        """Registry version is e8-v1 with expected metric count."""
        assert E8_METHODOLOGY_VERSION == "e8-v1"
        assert len(E8_METRIC_REGISTRY) >= 30

    def test_required_metadata_fields_exist(self):
        """Every definition exposes mandatory methodology fields."""
        for definition in E8_METRIC_REGISTRY.values():
            assert definition.metric_id
            assert definition.label
            assert definition.business_question
            assert definition.formula
            assert definition.numerator_definition
            assert definition.drilldown_route
            assert definition.version == "e8-v1"

    def test_unsupported_evm_metrics_declare_unavailable_behavior(self):
        """CPI/AC definitions declare unavailable when AC missing."""
        cpi = E8_METRIC_REGISTRY["e8.cpi"]
        assert "unavailable" in cpi.missing_data_behavior.lower()

    def test_no_proxy_labelled_authoritative_evm(self):
        """Duration proxy is proxy authority, not authoritative."""
        proxy = E8_METRIC_REGISTRY["e8.schedule_performance_index"]
        assert proxy.authority_level == MetricAuthority.PROXY.value

    def test_drilldown_and_caveat_fields_exist(self):
        """Payload export includes drilldown routes."""
        payload = methodology_registry_payload()
        assert payload[0]["drilldown_route"]
        assert payload[0]["caveat"]


@pytest.mark.django_db
class TestDelayClassification:
    """Canonical delay semantics."""

    def _task(self, project, **kwargs):
        defaults = {
            "start_date": datetime.date(2025, 1, 1),
            "end_date": datetime.date(2025, 1, 31),
            "status": "planned",
        }
        defaults.update(kwargs)
        return TaskFactory(project=project, **defaults)

    def test_completed_late_uses_actual_finish(self):
        """Completed late = actual finish after baseline finish."""
        project = ProjectFactory()
        task = self._task(
            project,
            status="complete",
            end_date=datetime.date(2025, 1, 20),
            actual_end=datetime.date(2025, 1, 25),
        )
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 2, 1))
        result = svc.classify_task(task)
        assert result.primary_delay_type == DelayType.COMPLETED_LATE.value
        assert result.variance_days is not None and result.variance_days > 0

    def test_completed_on_time(self):
        """Completed on or before baseline is not_late."""
        project = ProjectFactory()
        task = self._task(
            project,
            status="complete",
            end_date=datetime.date(2025, 1, 31),
            actual_end=datetime.date(2025, 1, 28),
        )
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 2, 1))
        result = svc.classify_task(task)
        assert result.primary_delay_type == DelayType.NOT_LATE.value

    def test_forecast_late_uses_early_finish(self):
        """Forecast late compares early_finish to baseline."""
        project = ProjectFactory()
        task = self._task(
            project,
            early_finish=datetime.date(2025, 2, 15),
            end_date=datetime.date(2025, 1, 31),
        )
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert result.primary_delay_type == DelayType.FORECAST_LATE.value

    def test_currently_late_separate_from_forecast(self):
        """Currently late when required finish before data date."""
        project = ProjectFactory()
        task = self._task(
            project,
            end_date=datetime.date(2025, 1, 10),
            early_finish=datetime.date(2025, 1, 10),
        )
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 20))
        result = svc.classify_task(task)
        assert DelayType.CURRENTLY_LATE.value in (
            result.primary_delay_type,
            *result.secondary_indicators,
        )

    def test_negative_float_indicator(self):
        """Negative float appears in secondary indicators."""
        project = ProjectFactory()
        task = self._task(project, total_float=-3)
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert DelayType.NEGATIVE_FLOAT.value in result.secondary_indicators

    def test_zero_float_indicator(self):
        """Zero float flagged separately."""
        project = ProjectFactory()
        task = self._task(project, total_float=0, is_critical=True)
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert DelayType.ZERO_FLOAT.value in result.secondary_indicators

    def test_near_critical_default_threshold(self):
        """Near critical at default 5 working days."""
        project = ProjectFactory()
        task = self._task(project, total_float=3)
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert DelayType.NEAR_CRITICAL.value in result.secondary_indicators

    def test_near_critical_threshold_configurable(self):
        """Custom near-critical threshold."""
        project = ProjectFactory()
        task = self._task(project, total_float=8)
        svc = DelayClassificationService(
            str(project.pk), near_critical_threshold=10, data_date=datetime.date(2025, 1, 15)
        )
        result = svc.classify_task(task)
        assert DelayType.NEAR_CRITICAL.value in result.secondary_indicators

    def test_missing_baseline(self):
        """Missing end_date yields missing_baseline."""
        project = ProjectFactory()
        task = TaskFactory.build(project=project, end_date=datetime.date(2025, 1, 31))
        task.end_date = None
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert result.primary_delay_type == DelayType.MISSING_BASELINE.value

    def test_milestone_activity_type(self):
        """Milestone activity type still gets delay classification."""
        project = ProjectFactory()
        task = self._task(
            project,
            activity_type="Finish Milestone",
            end_date=datetime.date(2025, 1, 1),
            start_date=datetime.date(2025, 1, 1),
        )
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        result = svc.classify_task(task)
        assert result.baseline_finish is not None

    def test_working_day_calculation(self):
        """Working day mode produces integer variance."""
        project = ProjectFactory()
        task = self._task(
            project,
            status="complete",
            end_date=datetime.date(2025, 1, 6),
            actual_end=datetime.date(2025, 1, 13),
        )
        svc = DelayClassificationService(
            str(project.pk), day_type=DayType.WORKING, data_date=datetime.date(2025, 1, 20)
        )
        result = svc.classify_task(task)
        assert result.day_type == DayType.WORKING.value
        assert isinstance(result.variance_days, int)

    def test_calendar_day_alternate(self):
        """Calendar day mode uses calendar semantics."""
        project = ProjectFactory()
        task = self._task(
            project,
            status="complete",
            end_date=datetime.date(2025, 1, 1),
            actual_end=datetime.date(2025, 1, 8),
        )
        svc = DelayClassificationService(
            str(project.pk), day_type=DayType.CALENDAR, data_date=datetime.date(2025, 1, 20)
        )
        result = svc.classify_task(task)
        assert result.day_type == DayType.CALENDAR.value
        assert result.variance_days == 7

    def test_no_slip_field_in_contract(self):
        """Delay result dict has no generic slip key."""
        project = ProjectFactory()
        task = self._task(project)
        svc = DelayClassificationService(str(project.pk), data_date=datetime.date(2025, 1, 15))
        data = svc.classify_task(task).to_dict()
        assert "slip" not in data
        assert "primary_delay_type" in data


@pytest.mark.django_db
class TestScopeClassification:
    """Scope resolver authority rules."""

    def test_explicit_code_authoritative(self):
        """Explicit activity_type procurement token is authoritative."""
        project = ProjectFactory()
        task = TaskFactory(project=project, activity_type="Procurement Activity")
        result = ScopeClassificationResolver().resolve(task)
        assert result.authoritative is True
        assert result.classification == "procurement"

    def test_milestone_deterministic(self):
        """P6 Finish Milestone maps to milestone."""
        project = ProjectFactory()
        task = TaskFactory(project=project, activity_type="Finish Milestone")
        result = ScopeClassificationResolver().resolve(task)
        assert result.classification == "milestone"
        assert result.authoritative is True

    def test_keyword_suggestion_only(self):
        """Name keyword yields suggestion authority."""
        project = ProjectFactory()
        task = TaskFactory(project=project, name="Submit shop drawing package", activity_type="")
        result = ScopeClassificationResolver().resolve(task)
        assert result.authority_level == MetricAuthority.SUGGESTION.value
        assert result.authoritative is False

    def test_binding_does_not_force_physical(self):
        """Trusted link without type evidence stays unknown unless suggested."""
        project = ProjectFactory()
        task = TaskFactory(project=project, name="Task A", activity_type="Task Dependent")
        result = ScopeClassificationResolver().resolve(task, trusted_model_linked=True)
        assert result.classification == "unknown"
        assert result.trusted_model_linked is True

    def test_unknown_remains_unknown(self):
        """Insufficient evidence returns unknown."""
        project = ProjectFactory()
        task = TaskFactory(project=project, name="General work", activity_type="Task Dependent")
        result = ScopeClassificationResolver().resolve(task)
        assert result.classification == "unknown"

    def test_explicit_phys_code_prefix(self):
        """PHYS: governed activity code prefix is authoritative."""
        project = ProjectFactory()
        task = TaskFactory(project=project, activity_code="PHYS:001", activity_type="")
        result = ScopeClassificationResolver().resolve(task)
        assert result.classification == "physical_construction"
        assert result.authoritative is True


@pytest.mark.django_db
class TestCoverageService:
    """Coverage denominators and separation."""

    def test_all_task_denominator_explicit(self):
        """Coverage payload exposes all_tasks denominator."""
        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        payload = AnalyticalCoverageService(str(project.pk)).build()
        assert payload["denominators"]["all_tasks"] == 3

    def test_trusted_task_entity_separate(self):
        """Model link section includes separate task and entity items."""
        project = ProjectFactory()
        payload = AnalyticalCoverageService(str(project.pk)).build()
        ids = {item["metric_id"] for item in payload["model_links"]}
        assert "e8.trusted_task_link_coverage" in ids
        assert "e8.trusted_entity_link_coverage" in ids

    def test_unknown_withholds_authoritative_percentage(self):
        """Unknown scope count is explicit."""
        project = ProjectFactory()
        payload = AnalyticalCoverageService(str(project.pk)).build()
        unknown = next(i for i in payload["scope"] if i["metric_id"] == "e8.unknown_scope_count")
        assert unknown["numerator"] >= 0


@pytest.mark.django_db
class TestEVMAvailability:
    """EVM mode contract without changing compute_evm."""

    def test_cost_mode_when_cost_present(self):
        """Cost-bearing tasks enable metrics from compute_evm."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=1000,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 3, 31),
            is_non_physical=False,
        )
        before = compute_evm(str(project.pk))
        payload = E8EVMAvailabilityService(str(project.pk)).build()
        after = compute_evm(str(project.pk))
        assert before == after
        assert payload["available_metrics"]

    def test_cpi_unavailable_without_ac(self):
        """CPI listed unavailable when AC missing."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=500,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = E8EVMAvailabilityService(str(project.pk)).build()
        assert "e8.cpi" in payload.get("unavailable_metrics", {})

    def test_duration_mode_schedule_performance(self):
        """No cost falls back to schedule performance."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=None,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = E8EVMAvailabilityService(str(project.pk)).build()
        assert payload["schedule_performance_available"] is True


@pytest.mark.django_db
class TestResourceAvailability:
    """Equivalent workforce availability."""

    def test_equivalent_workforce_label(self):
        """Recommended label matches owner decision."""
        project = ProjectFactory()
        payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()
        assert payload["recommended_label"] == EQUIVALENT_WORKFORCE_LABEL

    def test_assumptions_exposed(self):
        """Assumptions block present."""
        project = ProjectFactory()
        payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()
        assert "hours_per_worker_day" in payload["assumptions"]

    def test_attendance_unavailable(self):
        """Site headcount not available without attendance."""
        project = ProjectFactory()
        payload = EquivalentWorkforceAvailabilityService(str(project.pk)).build()
        assert payload["actual_site_headcount_available"] is False


@pytest.mark.django_db
class TestAnalyticalContext:
    """Live current state and baseline caveats."""

    def test_live_current_state(self):
        """Context exposes live_current analytical state."""
        project = ProjectFactory()
        ctx = AnalyticalContextService(project).build()
        assert ctx["analytical_state"] == "live_current"

    def test_contractual_baseline_false(self):
        """No contractual baseline until BaselineVersion."""
        project = ProjectFactory()
        ctx = AnalyticalContextService(project).build()
        assert ctx["contractual_baseline_available"] is False

    def test_reimport_warning(self):
        """Re-import drift warning present."""
        project = ProjectFactory()
        ctx = AnalyticalContextService(project).build()
        assert "re-import" in ctx["reimport_drift_warning"].lower()


@pytest.mark.django_db
class TestExecutiveControlsAPI:
    """HTTP contract, auth, pagination."""

    def test_get_read_only_context(self, client):
        """GET context returns 200."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_context", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.json()["analytical_state"] == "live_current"

    def test_post_returns_405(self, client):
        """POST is rejected."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_coverage", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_project_isolation(self, client):
        """User cannot access another project's endpoints."""
        p1 = ProjectFactory()
        p2 = ProjectFactory()
        user = UserFactory()
        ProjectMembershipFactory(project=p1, user=user, permission="editor")
        client.force_login(user)
        url = reverse("scheduling:executive_controls_context", kwargs={"pk": p2.pk})
        assert client.get(url).status_code == 403

    def test_unauthorized_rejected(self, client):
        """Anonymous user redirected/denied."""
        project = ProjectFactory()
        url = reverse("scheduling:executive_controls_context", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code in (302, 403)

    def test_pagination_stable(self, client):
        """Delay detail paginates with stable ordering."""
        project = ProjectFactory()
        TaskFactory.create_batch(5, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_delays", kwargs={"pk": project.pk})
        r1 = client.get(url, {"page": 1, "page_size": 2}).json()
        r2 = client.get(url, {"page": 1, "page_size": 2}).json()
        assert r1["rows"] == r2["rows"]

    def test_query_counts_bounded(self, client):
        """Coverage endpoint query count stays bounded."""
        from django.db import connection

        project = ProjectFactory()
        TaskFactory.create_batch(10, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_coverage", kwargs={"pk": project.pk})
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        assert len(ctx.captured_queries) <= 25

    def test_no_writes_on_repeated_reads(self, client):
        """Repeated GET does not change task count."""
        from scheduling.models import Task

        project = ProjectFactory()
        TaskFactory.create_batch(2, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_delay_summary", kwargs={"pk": project.pk})
        before = Task.objects.filter(project=project).count()
        client.get(url)
        client.get(url)
        assert Task.objects.filter(project=project).count() == before


@pytest.mark.django_db
class TestNoMigrations:
    """E8-A must not add migrations."""

    def test_no_migrations_in_e8_a(self):
        """No new migration files for executive controls."""
        from pathlib import Path

        mig_dir = Path(__file__).resolve().parents[1] / "migrations"
        # E8-A adds zero migrations — latest should remain 0024 or prior on this branch
        names = [p.name for p in mig_dir.glob("*.py") if p.name != "__init__.py"]
        assert not any("executive" in n for n in names)
        assert not any("e8" in n for n in names)
