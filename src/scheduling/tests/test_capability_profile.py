# scheduling/tests/test_capability_profile.py
"""Cross-planner capability profile and E8 feature gating regression."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import Task, TaskEntityBinding
from scheduling.services.executive_controls.capability_profile import (
    PROFILE_VERSION,
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.enums import FeatureId, MissingReason, SeriesType
from scheduling.services.executive_controls.overview_filters import OverviewFilters
from scheduling.services.executive_controls.overview_service import ExecutiveControlsOverviewService
from scheduling.services.executive_controls.series_authority import build_series_contracts
from scheduling.tests.capability_fixtures import (
    build_empty_project,
    build_ifc_zero_trusted_project,
    build_msp_like_project,
    build_no_float_project,
    build_no_progress_project,
    build_p6xml_full_project,
    build_partial_trusted_project,
    build_sparse_column_project,
    build_xer_like_project,
)
from scheduling.tests.factories import TaskFactory

User = get_user_model()


def _profile(project) -> dict:
    return ProjectAnalyticsCapabilityProfile(project).build()


def _cap(profile: dict, feature: str) -> dict:
    return profile["capabilities"][feature]


def _member_client(client, project, permission="editor"):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission=permission)
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestCapabilityContract:
    def test_profile_version(self):
        project = build_sparse_column_project()
        assert _profile(project)["profile_version"] == PROFILE_VERSION

    def test_actual_fields_drive_capability_not_source_name(self):
        project = ProjectFactory()
        from scheduling.models import ScheduleSource

        ScheduleSource.objects.create(
            project=project,
            filename="fake.p6xml",
            source_format=Task.Source.P6XML,
            task_count=1,
        )
        TaskFactory(project=project, physical_percent_complete=0.5)
        cap = _cap(_profile(project), FeatureId.COST_EVM.value)
        assert cap["available"] is False
        assert MissingReason.NO_COST_BASELINE.value in cap["missing_reasons"]

    def test_source_name_does_not_force_cost_evm(self):
        project = build_sparse_column_project(with_cost=True)
        cap = _cap(_profile(project), FeatureId.COST_EVM.value)
        assert cap["planner_source_type"] == Task.Source.CSV

    def test_missing_reasons_explicit(self):
        cap = _cap(_profile(build_empty_project()), FeatureId.SCHEDULE_OVERVIEW.value)
        assert MissingReason.NO_TASKS.value in cap["missing_reasons"]

    def test_dependency_graph_exposed(self):
        assert (
            FeatureId.CURRENT_CPI.value in _profile(build_sparse_column_project())["dependencies"]
        )

    def test_coverage_on_spi(self):
        cap = _cap(_profile(build_msp_like_project()), FeatureId.CURRENT_SPI.value)
        assert cap["available"] is True
        assert cap["coverage_pct"] is not None


@pytest.mark.django_db
class TestSourceProfiles:
    def test_p6xml_full(self):
        p = _profile(build_p6xml_full_project())
        assert _cap(p, FeatureId.COST_EVM.value)["available"] is True
        assert _cap(p, FeatureId.CURRENT_CPI.value)["available"] is True
        assert p["banner"]["data_date_authoritative"] is True

    def test_xer_like(self):
        p = _profile(build_xer_like_project())
        assert _cap(p, FeatureId.SCHEDULE_OVERVIEW.value)["available"] is True
        assert _cap(p, FeatureId.COST_EVM.value)["available"] is False

    def test_msp_like(self):
        p = _profile(build_msp_like_project())
        assert _cap(p, FeatureId.SCHEDULE_PERFORMANCE.value)["available"] is True
        assert _cap(p, FeatureId.COST_EVM.value)["available"] is False

    def test_sparse_column(self):
        p = _profile(build_sparse_column_project())
        assert _cap(p, FeatureId.CURRENT_SPI.value)["available"] is True

    def test_no_progress(self):
        assert (
            _cap(_profile(build_no_progress_project()), FeatureId.CURRENT_SPI.value)["available"]
            is False
        )

    def test_no_float_no_deps(self):
        cap = _cap(_profile(build_no_float_project()), FeatureId.CRITICAL_PATH.value)
        assert MissingReason.NO_DEPENDENCIES.value in cap["missing_reasons"]


@pytest.mark.django_db
class TestFeatureGating:
    def test_historical_unavailable(self):
        p = _profile(build_p6xml_full_project())
        assert _cap(p, FeatureId.HISTORICAL_SPI_TREND.value)["available"] is False

    def test_derived_series_contract(self):
        c = build_series_contracts(schedulable_tasks=5)
        assert (
            c["derived_as_of_curve"]["series_type"]
            == SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value
        )

    def test_wbs_blocked(self):
        cap = _cap(_profile(build_p6xml_full_project()), FeatureId.WBS_MATRIX.value)
        assert MissingReason.NO_HIERARCHY_LINK.value in cap["missing_reasons"]

    def test_model_no_ifc(self):
        assert (
            _cap(_profile(build_sparse_column_project()), FeatureId.MODEL_IMPACT.value)["available"]
            is False
        )

    def test_model_ifc_zero_trusted(self):
        cap = _cap(_profile(build_ifc_zero_trusted_project()), FeatureId.MODEL_IMPACT.value)
        assert cap["available"] is True
        assert cap["numerator"] == 0

    def test_partial_trusted(self):
        assert _cap(
            _profile(build_partial_trusted_project()), FeatureId.TRUSTED_MODEL_DRILLDOWN.value
        )["available"]

    def test_review_excluded(self):
        project = build_partial_trusted_project()
        task = Task.objects.filter(project=project).first()
        TaskEntityBinding.objects.create(
            task=task,
            entity_global_id="GID-R",
            confidence=0.5,
            link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
            needs_review=True,
        )
        assert _cap(_profile(project), FeatureId.TRUSTED_MODEL_DRILLDOWN.value)["numerator"] == 1


@pytest.mark.django_db
class TestCapabilityAPI:
    def test_get(self, client):
        project = build_sparse_column_project()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_capabilities", kwargs={"pk": project.pk})
        data = client.get(url).json()
        assert data["profile_version"] == PROFILE_VERSION

    def test_post_405(self, client):
        project = ProjectFactory()
        _member_client(client, project)
        url = reverse("scheduling:executive_controls_capabilities", kwargs={"pk": project.pk})
        assert client.post(url).status_code == 405

    def test_unauthorized(self, client):
        project = ProjectFactory()
        url = reverse("scheduling:executive_controls_capabilities", kwargs={"pk": project.pk})
        assert client.get(url).status_code in (302, 403, 404)


@pytest.mark.django_db
class TestOverviewDegradation:
    def test_shell_has_capability(self):
        shell = ExecutiveControlsOverviewService(build_ifc_zero_trusted_project()).build_shell(
            OverviewFilters()
        )
        assert "capability_profile" in shell

    def test_model_unavailable_without_ifc(self):
        project = ProjectFactory()
        TaskFactory(project=project)
        payload = ExecutiveControlsOverviewService(project).build_model_impact_section(
            OverviewFilters()
        )
        assert payload["section_available"] is False

    def test_cost_skips_evm_when_unavailable(self):
        with patch("scheduling.services.evm.compute_evm") as mock_evm:
            payload = ExecutiveControlsOverviewService(build_empty_project()).build_cost_section(
                OverviewFilters()
            )
        mock_evm.assert_not_called()
        assert payload.get("section_available") is False


@pytest.mark.django_db
class TestPerformance:
    def test_query_bounded(self):
        project = build_p6xml_full_project()
        with CaptureQueriesContext(connection) as ctx:
            ProjectAnalyticsCapabilityProfile(project).build()
        assert len(ctx) <= 20

    def test_payload_bounded(self):
        payload = ProjectAnalyticsCapabilityProfile(build_p6xml_full_project()).build()
        assert len(json.dumps(payload)) < 200_000

    def test_no_writes(self):
        project = build_sparse_column_project()
        n = Task.objects.filter(project=project).count()
        for _ in range(3):
            ProjectAnalyticsCapabilityProfile(project).build()
        assert Task.objects.filter(project=project).count() == n
