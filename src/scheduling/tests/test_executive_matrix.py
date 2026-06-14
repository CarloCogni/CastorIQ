# scheduling/tests/test_executive_matrix.py
"""E8-C hierarchy matrix, trade analysis, and lightweight progress tests."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import Task, TaskEntityBinding
from scheduling.services.executive_controls.dimension_registry import (
    STAGE_LABEL,
    UNKNOWN_KEY,
    ExecutiveDimensionRegistry,
)
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.methodology import E8_METRIC_REGISTRY
from scheduling.services.executive_controls.performance_cube import ProjectPerformanceCubeService
from scheduling.services.executive_controls.progress_aggregation import (
    WEIGHTING_DURATION,
    ScheduleProgressAggregationService,
)
from scheduling.services.executive_controls.trade_package_analysis import (
    TradePackageAnalysisService,
)
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
class TestDimensionRegistry:
    """Dimension discovery and authority labelling."""

    def test_only_available_dimensions_exposed(self):
        """Empty project still returns scope dimensions."""
        project = ProjectFactory()
        dims = ExecutiveDimensionRegistry(str(project.pk)).discover()
        assert all(d.availability or d.dimension_id.startswith("scope_") for d in dims)

    def test_stage_not_labelled_wbs(self):
        """Stage dimension uses proxy label not WBS."""
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure")
        dim = ExecutiveDimensionRegistry(str(project.pk)).get("stage")
        assert dim is not None
        assert "WBS" not in dim.label or "not" in dim.caveat.lower() or "proxy" in dim.label.lower()
        assert dim.label == STAGE_LABEL

    def test_sub_stage_suggestion_authority(self):
        """Sub-stage is suggestion authority."""
        project = ProjectFactory()
        TaskFactory(project=project, sub_stage="concrete")
        dim = ExecutiveDimensionRegistry(str(project.pk)).get("sub_stage")
        assert dim.authority == MetricAuthority.SUGGESTION.value

    def test_unknown_explicit_bucket(self):
        """Blank stage maps to unknown key."""
        project = ProjectFactory()
        task = TaskFactory(project=project, stage="")
        key_fn = ExecutiveDimensionRegistry(str(project.pk)).key_fn("stage")
        assert key_fn(task)[0] == UNKNOWN_KEY

    def test_unsupported_dimension_rejected(self):
        """Invalid dimension raises."""
        project = ProjectFactory()
        with pytest.raises(ValueError):
            ExecutiveDimensionRegistry(str(project.pk)).key_fn("wbs")


@pytest.mark.django_db
class TestProgressAggregation:
    """Lightweight progress without compute_evm."""

    def test_no_compute_evm(self):
        """Progress service never calls compute_evm."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 1, 31),
            is_non_physical=False,
        )
        with patch("scheduling.services.evm.compute_evm") as mock_evm:
            ScheduleProgressAggregationService(str(project.pk)).aggregate_queryset(
                Task.objects.filter(project=project)
            )
        mock_evm.assert_not_called()

    def test_duration_mode_proxy_labelled(self):
        """No cost falls back to duration proxy label."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=None,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ScheduleProgressAggregationService(str(project.pk)).aggregate_queryset(
            Task.objects.filter(project=project)
        )
        assert payload["weighting_mode"] == WEIGHTING_DURATION
        assert (
            "proxy" in payload["weighting_label"].lower()
            or "proxy" in payload.get("caveat", "").lower()
        )

    def test_cost_weighted_with_coverage(self):
        """Cost mode when sufficient cost coverage."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            cost=1000,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        payload = ScheduleProgressAggregationService(str(project.pk)).aggregate_queryset(
            Task.objects.filter(project=project),
        )
        assert payload["available"] is True
        assert payload["planned_progress_pct"] is not None

    def test_missing_dates_unavailable(self):
        """Empty schedulable population yields unavailable progress."""
        project = ProjectFactory()
        payload = ScheduleProgressAggregationService(str(project.pk)).aggregate([])
        assert payload["available"] is False

    def test_included_excluded_counts(self):
        """Aggregation exposes included/excluded counts."""
        project = ProjectFactory()
        t1 = TaskFactory(project=project)
        t2 = TaskFactory(project=project)
        payload = ScheduleProgressAggregationService(str(project.pk)).aggregate([t1, t2])
        assert payload["included_task_count"] == 2
        assert payload["total_task_count"] == 2


@pytest.mark.django_db
class TestPerformanceCube:
    """Matrix aggregation and pagination."""

    def test_server_side_pagination(self):
        """Matrix paginates groups."""
        project = ProjectFactory()
        for st in ("structure", "envelope", "mep", "finishes"):
            TaskFactory.create_batch(2, project=project, stage=st)
        filters = ExecutiveMatrixFilters(dimension="stage", page_size=2, page=1)
        payload = ProjectPerformanceCubeService(project).build_rows(filters)
        assert len(payload["rows"]) <= 2
        assert payload["pagination"]["total"] >= 4

    def test_unknown_row_present(self):
        """Unknown stage bucket appears when unassigned tasks exist."""
        project = ProjectFactory()
        TaskFactory(project=project, stage="")
        payload = ProjectPerformanceCubeService(project).build_rows(
            ExecutiveMatrixFilters(dimension="stage")
        )
        keys = [r["key"] for r in payload["rows"]]
        assert UNKNOWN_KEY in keys

    def test_cost_unavailable_not_zero(self):
        """Groups without cost show unavailable not zero budget."""
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure", cost=None)
        payload = ProjectPerformanceCubeService(project).build_rows(
            ExecutiveMatrixFilters(dimension="stage")
        )
        row = next(r for r in payload["rows"] if r["key"] == "structure")
        assert row["cost"]["available"] is False
        assert row["cost"]["budget_total"] is None

    def test_trusted_tasks_only(self):
        """Review bindings excluded from trusted counts."""
        project = ProjectFactory()
        task = TaskFactory(project=project, stage="structure")
        entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-T")
        _bind(task, entity.global_id)
        _bind(task, "GID-R", needs_review=True)
        payload = ProjectPerformanceCubeService(project).build_rows(
            ExecutiveMatrixFilters(dimension="stage")
        )
        row = next(r for r in payload["rows"] if r["key"] == "structure")
        assert row["model_impact"]["trusted_task_count"] == 1

    def test_no_gid_payload_in_rows(self):
        """Matrix rows contain counts not GID lists."""
        project = ProjectFactory()
        TaskFactory(project=project, stage="mep")
        payload = ProjectPerformanceCubeService(project).build_rows(
            ExecutiveMatrixFilters(dimension="stage")
        )
        assert "GID-" not in str(payload["rows"])

    def test_query_count_bounded(self):
        """Matrix row build stays within query budget."""
        project = ProjectFactory()
        TaskFactory.create_batch(20, project=project, stage="structure")
        with CaptureQueriesContext(connection) as ctx:
            ProjectPerformanceCubeService(project).build_rows(ExecutiveMatrixFilters())
        assert len(ctx.captured_queries) <= 25

    def test_entity_overlap_caveat(self):
        """Model impact includes non-additive entity caveat."""
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure")
        payload = ProjectPerformanceCubeService(project).build_rows(
            ExecutiveMatrixFilters(dimension="stage")
        )
        caveat = payload["rows"][0]["model_impact"]["entity_count_caveat"]
        assert "overlap" in caveat.lower() or "dedupe" in caveat.lower()


@pytest.mark.django_db
class TestTradePackageAnalysis:
    """Trade/package governed analysis."""

    def test_authoritative_default_dimension(self):
        """Authoritative-only uses activity_type or scope."""
        project = ProjectFactory()
        TaskFactory(project=project, activity_type="Task Dependent", stage="structure")
        payload = TradePackageAnalysisService(project).build(ExecutiveMatrixFilters())
        assert payload["dimension"] in ("activity_type", "scope_authoritative", "sub_stage")

    def test_prioritization_components_visible(self):
        """Prioritization index exposes components."""
        project = ProjectFactory()
        TaskFactory(
            project=project,
            sub_stage="concrete",
            stage="structure",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 3, 1),
        )
        payload = TradePackageAnalysisService(project).build(
            ExecutiveMatrixFilters(authoritative_only=False)
        )
        groups = payload.get("authoritative_groups") or payload.get("suggestion_groups") or []
        if groups:
            assert "prioritization" in groups[0]
            assert "components" in groups[0]["prioritization"]

    def test_no_opaque_composite_only(self):
        """Formula string is public on prioritization."""
        project = ProjectFactory()
        TaskFactory(project=project, sub_stage="electrical", stage="mep")
        payload = TradePackageAnalysisService(project).build(ExecutiveMatrixFilters())
        for row in payload.get("high_materiality_underperforming", []):
            assert row["prioritization"].get("formula")


@pytest.mark.django_db
class TestActivityDrilldownService:
    """Activity drilldown service unit tests."""

    def test_build_returns_rows(self):
        from scheduling.services.executive_controls.activity_drilldown import (
            ActivityDrilldownService,
        )

        project = ProjectFactory()
        TaskFactory(project=project, stage="structure", activity_code="A1")
        filters = ExecutiveMatrixFilters.from_params(
            {"dimension": "stage", "group_key": "structure"}
        )
        payload = ActivityDrilldownService(project).build(filters)
        assert payload["rows"]
        assert payload["rows"][0]["activity_code"] == "A1"


@pytest.mark.django_db
class TestMatrixAPI:
    """HTTP security and read-only."""

    def test_matrix_page_loads(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 200

    def test_matrix_rows_json(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure")
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix_rows", kwargs={"pk": project.pk})
        data = client.get(url).json()
        assert data["section"] == "matrix_rows"

    def test_trades_page_loads(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_trades", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 200

    def test_post_returns_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix_rows", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_project_isolation(self, client):
        p1, p2 = ProjectFactory(), ProjectFactory()
        user = UserFactory()
        ProjectMembershipFactory(project=p1, user=user, permission="editor")
        client.force_login(user)
        url = reverse("scheduling:executive_controls_matrix", kwargs={"pk": p2.pk})
        assert client.get(url).status_code == 403

    def test_page_size_cap(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix_rows", kwargs={"pk": project.pk})
        data = client.get(url, {"page_size": "500"}).json()
        assert data["pagination"]["page_size"] <= 100

    def test_no_writes_on_repeated_reads(self, client):
        project = ProjectFactory()
        TaskFactory(project=project)
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix_rows", kwargs={"pk": project.pk})
        before = Task.objects.filter(project=project).count()
        client.get(url)
        client.get(url)
        assert Task.objects.filter(project=project).count() == before

    def test_activity_drilldown(self, client):
        project = ProjectFactory()
        TaskFactory(project=project, stage="structure", activity_code="A1")
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_matrix_activities", kwargs={"pk": project.pk})
        resp = client.get(url, {"dimension": "stage", "group_key": "structure"})
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data, data


@pytest.mark.django_db
class TestMethodologyE8C:
    """E8-C methodology extensions."""

    def test_group_metrics_registered(self):
        assert "e8.group_planned_progress" in E8_METRIC_REGISTRY
        assert "e8.prioritization_index" in E8_METRIC_REGISTRY


@pytest.mark.django_db
class TestScheduleOverviewLightweight:
    """E8-B schedule section uses lightweight progress."""

    def test_schedule_section_no_compute_evm(self):
        from scheduling.services.executive_controls.overview_filters import OverviewFilters
        from scheduling.services.executive_controls.overview_service import (
            ExecutiveControlsOverviewService,
        )

        project = ProjectFactory()
        TaskFactory(
            project=project,
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 2, 28),
            is_non_physical=False,
        )
        with patch("scheduling.services.evm.compute_evm") as mock_evm:
            ExecutiveControlsOverviewService(project).build_schedule_section(OverviewFilters())
        mock_evm.assert_not_called()


@pytest.mark.django_db
class TestNoMigrationsE8C:
    def test_no_e8_migrations(self):
        from pathlib import Path

        mig_dir = Path(__file__).resolve().parents[1] / "migrations"
        names = [p.name for p in mig_dir.glob("*.py") if p.name != "__init__.py"]
        assert not any("e8" in n for n in names)
