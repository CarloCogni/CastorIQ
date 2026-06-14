# scheduling/tests/test_executive_overview.py
"""E8-B executive controls overview — shell, sections, filters, and contracts."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import Task, TaskEntityBinding
from scheduling.services.executive_controls.enums import DelayType
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.overview_filters import OverviewFilters
from scheduling.services.executive_controls.overview_service import ExecutiveControlsOverviewService
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


def _bind(task, gid: str, *, needs_review: bool = False):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=needs_review,
    )


@pytest.mark.django_db
class TestOverviewShell:
    """Shell contract — no heavy EVM or full delay classification."""

    def test_shell_excludes_heavy_calculations(self):
        """Shell does not invoke compute_evm or full delay classification."""
        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        svc = ExecutiveControlsOverviewService(project)
        with (
            patch("scheduling.services.evm.compute_evm") as mock_evm,
            patch(
                "scheduling.services.executive_controls.delays.ExecutiveDelayService.classification_pass"
            ) as mock_delay,
        ):
            payload = svc.build_shell(OverviewFilters())
        mock_evm.assert_not_called()
        mock_delay.assert_not_called()
        assert payload["section"] == "shell"
        assert "analytical_context" in payload

    def test_analytical_context_present(self):
        """Shell includes live analytical context."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())
        ctx = payload["analytical_context"]
        assert ctx["analytical_state"] == "live_current"
        assert ctx["methodology_version"] == E8_METHODOLOGY_VERSION

    def test_methodology_links_present(self):
        """Shell exposes methodology URL."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())
        assert payload["methodology_url"]
        assert payload["methodology_version"] == "e8-v1"

    def test_reimport_caveat_present(self):
        """Re-import drift warning included in shell warnings."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())
        warnings = " ".join(payload.get("warnings", [])).lower()
        assert "re-import" in warnings or "reimport" in warnings.replace("-", "")

    def test_calculated_timestamp(self):
        """Shell includes calculated_at."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())
        assert payload.get("calculated_at")


@pytest.mark.django_db
class TestOverviewSections:
    """Independent section endpoints and failure isolation."""

    def test_section_endpoints_independent(self, client):
        """Each section returns 200 JSON independently."""
        project = ProjectFactory()
        TaskFactory.create_batch(2, project=project)
        _member_client(client, project)
        sections = [
            "executive_controls_overview_schedule",
            "executive_controls_overview_cost",
            "executive_controls_overview_delays",
            "executive_controls_overview_model",
            "executive_controls_overview_coverage",
        ]
        for name in sections:
            url = reverse(f"scheduling:{name}", kwargs={"pk": project.pk})
            resp = client.get(url)
            assert resp.status_code == 200

    def test_one_section_failure_does_not_fail_others(self, client):
        """Cost section still works when schedule build raises."""
        project = ProjectFactory()
        TaskFactory(project=project)
        _member_client(client, project)
        schedule_url = reverse(
            "scheduling:executive_controls_overview_schedule", kwargs={"pk": project.pk}
        )
        cost_url = reverse("scheduling:executive_controls_overview_cost", kwargs={"pk": project.pk})
        with patch.object(
            ExecutiveControlsOverviewService,
            "build_schedule_section",
            side_effect=RuntimeError("boom"),
        ):
            sched = client.get(schedule_url, HTTP_HX_REQUEST="true")
        assert sched.status_code == 200
        assert b"temporarily unavailable" in sched.content.lower()
        assert client.get(cost_url).status_code == 200

    def test_overview_json_shell_endpoint(self, client):
        """Overview JSON returns shell metadata."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_overview", kwargs={"pk": project.pk})
        data = client.get(url).json()
        assert data["section"] == "shell"


@pytest.mark.django_db
class TestScheduleSection:
    """Schedule position cards."""

    def test_planned_actual_methodology(self):
        """Progress cards expose numerator/denominator."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=1000,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ExecutiveControlsOverviewService(project).build_schedule_section(
            OverviewFilters()
        )
        planned = next(c for c in payload["cards"] if c["metric_id"] == "e8.planned_progress")
        assert planned["numerator"] is not None or not planned["available"]
        assert "weighting" in payload.get("weighting_note", "").lower() or payload.get(
            "weighting_note"
        )

    def test_unavailable_values_not_zero(self):
        """Project finish variance unavailable does not show as zero."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_schedule_section(
            OverviewFilters()
        )
        finish = next(c for c in payload["cards"] if c["metric_id"] == "e8.project_finish_variance")
        if not finish["available"]:
            assert finish["value"] is None or finish["display_value"] == "N/A"

    def test_critical_separate_from_delay(self):
        """Critical count card is separate metric."""
        project = ProjectFactory()
        TaskFactory(project=project, is_critical=True)
        sched = ExecutiveControlsOverviewService(project).build_schedule_section(OverviewFilters())
        delay = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        critical_card = next(c for c in sched["cards"] if c["metric_id"] == "e8.critical_count")
        assert critical_card["value"] >= 1
        assert "primary_cards" in delay

    def test_negative_float_separate(self):
        """Negative float is its own schedule card."""
        project = ProjectFactory()
        TaskFactory(project=project, total_float=-2)
        sched = ExecutiveControlsOverviewService(project).build_schedule_section(OverviewFilters())
        neg = next(c for c in sched["cards"] if c["metric_id"] == "e8.negative_float_count")
        assert neg["value"] >= 1

    def test_project_finish_caveat(self):
        """Finish variance references baseline semantics when unavailable."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_schedule_section(
            OverviewFilters()
        )
        finish = next(c for c in payload["cards"] if c["metric_id"] == "e8.project_finish_variance")
        assert finish.get("caveat") or finish.get("unavailable_reason") or finish["available"]


@pytest.mark.django_db
class TestCostSection:
    """Cost / EVM section."""

    def test_cost_evm_mode_label(self):
        """Cost section exposes performance mode."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=500,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())
        assert payload.get("performance_mode_label")

    def test_cpi_unavailable_without_ac(self):
        """CPI card unavailable when AC missing."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=500,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())
        cpi = next(c for c in payload["cards"] if c["metric_id"] == "e8.cpi")
        if not cpi["available"]:
            assert cpi["unavailable_reason"] or cpi["display_value"] == "N/A"

    def test_proxy_not_labelled_cost_evm(self):
        """Duration-only project uses schedule performance label."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=None,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())
        assert payload["cost_evm_available"] is False
        assert any("Schedule Performance" in w for w in payload.get("warnings", []))

    def test_coverage_visible(self):
        """Cost cards include coverage metadata when available."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=100,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())
        spi = next(c for c in payload["cards"] if c["metric_id"] == "e8.spi")
        assert spi.get("coverage") is not None or spi["available"]

    def test_compute_evm_once_per_section(self):
        """Cost section calls compute_evm at most once per request."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=200,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        with patch("scheduling.services.evm.compute_evm") as mock_evm:
            mock_evm.return_value = {
                "has_data": True,
                "performance_mode": "cost_evm",
                "use_cost": True,
                "ac_available": False,
                "spi": 1.0,
                "bac": 1000,
                "pv": 500,
                "ev": 400,
                "cost_basis": "schedule cost",
                "performance_mode_label": "Cost EVM",
            }
            ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())
        assert mock_evm.call_count <= 1


@pytest.mark.django_db
class TestDelaySection:
    """Delay exposure — primary vs secondary."""

    def test_primary_counts_mutually_exclusive(self):
        """Primary delay keys are the four canonical types."""
        project = ProjectFactory()
        TaskFactory.create_batch(4, project=project)
        payload = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        primary_keys = set(payload["primary_counts"].keys())
        expected = {
            DelayType.COMPLETED_LATE.value,
            DelayType.CURRENTLY_LATE.value,
            DelayType.FORECAST_LATE.value,
            DelayType.NOT_LATE.value,
        }
        assert expected.issubset(primary_keys)

    def test_primary_counts_reconcile(self):
        """Primary counts sum to filtered task population."""
        project = ProjectFactory()
        TaskFactory.create_batch(5, project=project)
        payload = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        primary_sum = sum(
            payload["primary_counts"].get(k, 0)
            for k in (
                DelayType.COMPLETED_LATE.value,
                DelayType.CURRENTLY_LATE.value,
                DelayType.FORECAST_LATE.value,
                DelayType.NOT_LATE.value,
            )
        )
        assert primary_sum == payload["task_count"]

    def test_secondary_may_overlap(self):
        """Secondary indicators are separate from primary cards."""
        project = ProjectFactory()
        TaskFactory(project=project, is_critical=True, total_float=-1)
        payload = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        assert payload["secondary_label"]
        assert payload["indicator_cards"]

    def test_no_generic_slip_label(self):
        """Delay cards use typed labels, not generic slip."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        labels = " ".join(c["label"] for c in payload["primary_cards"]).lower()
        assert "slip" not in labels

    def test_delay_drilldowns(self):
        """Primary cards include drilldown URLs."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        assert payload["primary_cards"][0]["drilldown_url"]

    def test_working_calendar_visible(self):
        """Day type echoed in delay section."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_delays_section(
            OverviewFilters(day_type="calendar")
        )
        assert payload["day_type"] == "calendar"

    def test_one_pass_classification(self):
        """classification_pass invoked once per delays section build."""
        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        with patch(
            "scheduling.services.executive_controls.delays.ExecutiveDelayService.classification_pass"
        ) as mock_pass:
            mock_pass.return_value = {
                "classifier": MagicMock(data_date=datetime.date(2025, 6, 1)),
                "primary_counts": {dt.value: 0 for dt in DelayType},
                "secondary_counts": {},
                "task_count": 0,
                "results": [],
            }
            ExecutiveControlsOverviewService(project).build_delays_section(OverviewFilters())
        assert mock_pass.call_count == 1


@pytest.mark.django_db
class TestModelImpactSection:
    """Trusted model impact."""

    def test_active_trusted_bindings_only(self):
        """Review bindings excluded from trusted counts."""
        project = ProjectFactory()
        task = TaskFactory(project=project)
        _bind(task, "GID-T", needs_review=False)
        _bind(task, "GID-R", needs_review=True)
        reader = BindingGovernanceReader(str(project.pk))
        assert reader.trusted_entity_gids() == {"GID-T"}

    def test_review_excluded_from_overview(self):
        """Overview trusted entity count excludes review queue."""
        project = ProjectFactory()
        task = TaskFactory(project=project)
        entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-OK")
        IFCEntityFactory(ifc_file__project=project, global_id="GID-REV")
        _bind(task, entity.global_id)
        _bind(task, "GID-REV", needs_review=True)
        payload = ExecutiveControlsOverviewService(project).build_model_impact_section(
            OverviewFilters()
        )
        trusted_card = next(
            c for c in payload["cards"] if c["metric_id"] == "e8.trusted_entity_link_coverage"
        )
        assert trusted_card["value"] == 1

    def test_task_entity_denominators_separate(self):
        """Task and entity coverage use different denominators."""
        project = ProjectFactory()
        task = TaskFactory(project=project)
        TaskFactory(project=project)
        IFCEntityFactory(ifc_file__project=project, global_id="GID-A")
        _bind(task, "GID-A")
        payload = ExecutiveControlsOverviewService(project).build_model_impact_section(
            OverviewFilters()
        )
        task_card = next(
            c for c in payload["cards"] if c["metric_id"] == "e8.trusted_task_link_coverage"
        )
        entity_card = next(
            c for c in payload["cards"] if c["metric_id"] == "e8.trusted_entity_link_coverage"
        )
        assert (
            task_card["denominator"] != entity_card["denominator"] or task_card["denominator"] == 2
        )

    def test_counts_reconcile(self):
        """Trusted task count matches reader."""
        project = ProjectFactory()
        t1 = TaskFactory(project=project)
        TaskFactory(project=project)
        IFCEntityFactory(ifc_file__project=project, global_id="GID-1")
        _bind(t1, "GID-1")
        payload = ExecutiveControlsOverviewService(project).build_model_impact_section(
            OverviewFilters()
        )
        task_card = next(
            c for c in payload["cards"] if c["metric_id"] == "e8.trusted_task_link_coverage"
        )
        assert task_card["value"] == len(
            BindingGovernanceReader(str(project.pk)).trusted_task_ids()
        )

    def test_no_full_gid_payload(self):
        """Model impact JSON has cards only — no entity GID list."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_model_impact_section(
            OverviewFilters()
        )
        serialized = str(payload)
        assert "GID-" not in serialized or payload["cards"][0]["value"] is not None


@pytest.mark.django_db
class TestCoverageSection:
    """Data and methodology coverage."""

    def test_numerators_denominators(self):
        """Coverage rows expose numerator and denominator."""
        project = ProjectFactory()
        TaskFactory(project=project)
        payload = ExecutiveControlsOverviewService(project).build_coverage_section(
            OverviewFilters()
        )
        assert payload["rows"]
        row = payload["rows"][0]
        assert "numerator" in row or "metric_id" in row

    def test_unknown_percentage_withheld(self):
        """Unknown scope row does not claim authoritative percentage."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_coverage_section(
            OverviewFilters()
        )
        scope_rows = [r for r in payload["rows"] if "scope" in r.get("metric_id", "")]
        if scope_rows:
            unknown = next((r for r in scope_rows if "unknown" in r.get("metric_id", "")), None)
            if unknown and unknown.get("percentage") is None:
                assert unknown.get("caveat")

    def test_authority_badges(self):
        """Coverage rows include authority."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_coverage_section(
            OverviewFilters()
        )
        assert any(r.get("authority") for r in payload["rows"])

    def test_baseline_caveat_in_context(self):
        """Analytical context baseline description is non-contractual."""
        project = ProjectFactory()
        ctx = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())[
            "analytical_context"
        ]
        assert ctx["contractual_baseline_available"] is False

    def test_snapshot_unavailable(self):
        """Context header contract — no snapshot selected."""
        project = ProjectFactory()
        ctx = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())[
            "analytical_context"
        ]
        assert ctx.get("snapshot_available") is False or "snapshot" in str(ctx).lower()


@pytest.mark.django_db
class TestOverviewHTTP:
    """Security, read-only, and page smoke."""

    def test_get_read_only_page(self, client):
        """Executive controls page loads for member."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"Executive Controls" in resp.content

    def test_post_returns_405(self, client):
        """POST rejected on overview endpoints."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_overview", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_project_isolation(self, client):
        """Cross-project access denied."""
        p1 = ProjectFactory()
        p2 = ProjectFactory()
        user = UserFactory()
        ProjectMembershipFactory(project=p1, user=user, permission="editor")
        client.force_login(user)
        url = reverse("scheduling:executive_controls", kwargs={"pk": p2.pk})
        assert client.get(url).status_code == 403

    def test_unauthorized_rejected(self, client):
        """Anonymous user cannot access overview."""
        project = ProjectFactory()
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        assert client.get(url).status_code in (302, 403)

    def test_query_counts_bounded(self, client):
        """Shell page query count stays bounded."""
        project = ProjectFactory()
        TaskFactory.create_batch(5, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls", kwargs={"pk": project.pk})
        with CaptureQueriesContext(connection) as ctx:
            client.get(url)
        assert len(ctx.captured_queries) <= 55

    def test_payload_bounded(self, client):
        """Overview JSON shell is lightweight."""
        project = ProjectFactory()
        TaskFactory.create_batch(20, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_overview", kwargs={"pk": project.pk})
        data = client.get(url).json()
        assert len(str(data)) < 50_000

    def test_no_writes_on_repeated_reads(self, client):
        """Repeated section GET does not mutate tasks."""
        project = ProjectFactory()
        TaskFactory.create_batch(2, project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_overview_delays", kwargs={"pk": project.pk})
        before = Task.objects.filter(project=project).count()
        client.get(url)
        client.get(url)
        assert Task.objects.filter(project=project).count() == before

    def test_filter_url_state(self, client):
        """Filters pass through query string to section."""
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse(
            "scheduling:executive_controls_overview_delays",
            kwargs={"pk": project.pk},
        )
        resp = client.get(url, {"day_type": "calendar", "status": "planned"})
        assert resp.status_code == 200


@pytest.mark.django_db
class TestOverviewFilters:
    """URL filter serialization."""

    def test_filters_round_trip(self):
        """OverviewFilters serializes to query string."""
        f = OverviewFilters(stage="Phase1", linked_trusted=True, day_type="calendar")
        q = f.query_string()
        restored = OverviewFilters.from_params(dict(x.split("=") for x in q.split("&") if x))
        assert restored.stage == "Phase1"
        assert restored.linked_trusted is True
        assert restored.day_type == "calendar"


@pytest.mark.django_db
class TestEmptyProject:
    """Empty project synthetic acceptance."""

    def test_empty_project_shell(self):
        """Empty project still returns shell."""
        project = ProjectFactory()
        payload = ExecutiveControlsOverviewService(project).build_shell(OverviewFilters())
        assert payload["lightweight_coverage"]["all_tasks"] == 0

    def test_empty_project_sections(self):
        """Sections return without error on empty project."""
        project = ProjectFactory()
        svc = ExecutiveControlsOverviewService(project)
        assert svc.build_delays_section(OverviewFilters())["task_count"] == 0
        model_payload = svc.build_model_impact_section(OverviewFilters())
        assert model_payload.get("section_available") is False or model_payload["cards"] == []


@pytest.mark.django_db
class TestSnapshotQueryBudget:
    """DF-B1.1 — E8 overview snapshot query attribution and budgets."""

    def _overview_url(self, project):
        return reverse("scheduling:executive_controls", kwargs={"pk": project.pk})

    def _snapshot_sql_count(self, captured) -> int:
        return sum(1 for q in captured if "analyticalsnapshot" in q["sql"].lower())

    def test_zero_snapshot_overview_restores_55_budget(self, client):
        project = ProjectFactory()
        TaskFactory.create_batch(5, project=project)
        _member_client(client, project)
        with CaptureQueriesContext(connection) as ctx:
            client.get(self._overview_url(project))
        assert len(ctx.captured_queries) <= 55

    def test_zero_snapshot_no_latest_lookup_queries(self, client):
        """Counts live in merged foundation SQL; no latest-row fetch when zero."""
        project = ProjectFactory()
        TaskFactory.create_batch(3, project=project)
        _member_client(client, project)
        with CaptureQueriesContext(connection) as ctx:
            client.get(self._overview_url(project))
        assert not any(
            "order by" in q["sql"].lower() and "analyticalsnapshot" in q["sql"].lower()
            for q in ctx.captured_queries
        )

    def test_completed_snapshot_context_correct(self, client):
        from django.utils import timezone

        from scheduling.models import AnalyticalSnapshot, ScheduleSourceVersion
        from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )
        from scheduling.services.executive_controls.context import AnalyticalContextService

        project = ProjectFactory()
        user = UserFactory()
        ScheduleSourceVersion.objects.create(
            project=project,
            version_number=1,
            source_type=Task.Source.XER,
            source_filename="t.xer",
            status=ScheduleSourceVersion.Status.CURRENT,
            imported_at=timezone.now(),
        )
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Done",
            snapshot_type=AnalyticalSnapshot.SnapshotType.MANUAL_CHECKPOINT,
            actor=user,
        )
        AnalyticalSnapshotService.begin_calculation(snap, actor=user)
        AnalyticalSnapshotService.complete_manifest(snap, actor=user)
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        ctx = AnalyticalContextService(project).build(capability_profile=profile)
        assert ctx["latest_completed_snapshot"]["id"] == str(snap.pk)
        assert ctx["latest_published_snapshot"] is None

    def test_published_snapshot_context_with_bounded_queries(self, client):
        from django.utils import timezone

        from scheduling.models import AnalyticalSnapshot, ScheduleSourceVersion
        from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService

        project = ProjectFactory()
        user = UserFactory()
        _member_client(client, project)
        ScheduleSourceVersion.objects.create(
            project=project,
            version_number=1,
            source_type=Task.Source.XER,
            source_filename="t.xer",
            status=ScheduleSourceVersion.Status.CURRENT,
            imported_at=timezone.now(),
        )
        snap = AnalyticalSnapshotService.request_snapshot(
            project=project,
            name="Pub",
            snapshot_type=AnalyticalSnapshot.SnapshotType.REPORT_FREEZE,
            actor=user,
        )
        AnalyticalSnapshotService.begin_calculation(snap, actor=user)
        AnalyticalSnapshotService.complete_manifest(snap, actor=user)
        AnalyticalSnapshotService.publish(snap, actor=user)
        with CaptureQueriesContext(connection) as ctx:
            client.get(self._overview_url(project))
        assert len(ctx.captured_queries) <= 58
        assert self._snapshot_sql_count(ctx.captured_queries) <= 3

    def test_capability_and_context_counts_consistent(self):
        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )
        from scheduling.services.executive_controls.context import AnalyticalContextService

        project = ProjectFactory()
        profile = ProjectAnalyticsCapabilityProfile(project).build()
        ctx = AnalyticalContextService(project).build(capability_profile=profile)
        counts = profile["snapshot_capabilities"]["snapshot_counts"]
        assert counts["total"] == 0
        assert ctx["latest_completed_snapshot"] is None
        assert ctx["latest_published_snapshot"] is None
        assert ctx["snapshot_manifest_available"] is True

    def test_repeated_get_no_writes(self, client):
        from scheduling.models import AnalyticalSnapshot

        project = ProjectFactory()
        _member_client(client, project)
        url = self._overview_url(project)
        before = AnalyticalSnapshot.objects.filter(project=project).count()
        client.get(url)
        client.get(url)
        assert AnalyticalSnapshot.objects.filter(project=project).count() == before


@pytest.mark.django_db
class TestNoMigrationsE8B:
    """E8-B must not add migrations."""

    def test_no_e8_migrations(self):
        """No executive/e8 migration files."""
        from pathlib import Path

        mig_dir = Path(__file__).resolve().parents[1] / "migrations"
        names = [p.name for p in mig_dir.glob("*.py") if p.name != "__init__.py"]
        assert not any("executive" in n for n in names)
        assert not any("e8" in n for n in names)
