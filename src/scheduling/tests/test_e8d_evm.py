# scheduling/tests/test_e8d_evm.py
"""E8-D current-point EVM and derived as-of S-curve tests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from environments.tests.factories import ProjectMembershipFactory, UserFactory
from scheduling.services.executive_controls.current_evm_analytics import (
    DERIVED_BANNER,
    CurrentEVMAnalyticsService,
)
from scheduling.services.executive_controls.derived_asof_scurve import DerivedAsOfSCurveService
from scheduling.services.executive_controls.enums import SeriesType
from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession
from scheduling.services.executive_controls.evm_filters import EVMFilters
from scheduling.services.executive_controls.series_authority import build_series_contracts
from scheduling.tests.capability_fixtures import (
    build_empty_project,
    build_msp_like_project,
    build_p6xml_full_project,
    build_sparse_column_project,
    build_xer_like_project,
)

User = get_user_model()


def _member_client(client, project):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission="editor")
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestSeriesAuthority:
    def test_current_point_contract(self):
        c = build_series_contracts(schedulable_tasks=5)["current_point"]
        assert c["series_type"] == SeriesType.CURRENT_POINT.value

    def test_current_snapshot_reconstruction(self):
        c = build_series_contracts(schedulable_tasks=5)["derived_as_of_curve"]
        assert c["series_type"] == SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value

    def test_imported_historical_unavailable(self):
        c = build_series_contracts(schedulable_tasks=5)["imported_historical"]
        assert c["available"] is False

    def test_frozen_snapshot_unavailable(self):
        c = build_series_contracts(schedulable_tasks=5)["frozen_snapshot_history"]
        assert c["available"] is False

    def test_forecast_projection_labelled(self):
        c = build_series_contracts(schedulable_tasks=5)["forecast_projection"]
        assert c["series_type"] == SeriesType.FORECAST_PROJECTION.value

    def test_no_historical_wording_in_banner(self):
        assert "not imported" in DERIVED_BANNER.lower()


@pytest.mark.django_db
class TestCurrentMetrics:
    def test_cost_evm_mode(self):
        payload = CurrentEVMAnalyticsService(build_p6xml_full_project()).build()
        assert payload["mode"] == "cost_evm"
        assert payload["metrics"]["e8.spi"]["available"] is True

    def test_schedule_performance_mode(self):
        assert (
            CurrentEVMAnalyticsService(build_xer_like_project()).build()["mode"]
            == "schedule_performance"
        )

    def test_cpi_unavailable_without_ac(self):
        payload = CurrentEVMAnalyticsService(build_msp_like_project()).build()
        assert "e8.cpi" in payload["unavailable_metrics"]

    def test_missing_not_zero(self):
        payload = CurrentEVMAnalyticsService(build_empty_project()).build()
        assert payload["mode"] == "unavailable"

    def test_compute_evm_once_per_session(self):
        p = build_sparse_column_project()
        session = E8EVMComputeSession(str(p.pk))
        with patch("scheduling.services.evm.compute_evm") as mock:
            mock.return_value = {
                "has_data": True,
                "use_cost": False,
                "bac": 100,
                "pv": 50,
                "ev": 40,
                "spi": 0.8,
                "performance_mode": "schedule_performance",
                "performance_mode_label": "x",
                "cost_basis": "d",
                "series": {"pv": [], "ev": []},
            }
            CurrentEVMAnalyticsService(p, session=session).build()
            DerivedAsOfSCurveService(p, session=session).build_scurve(EVMFilters())
            assert mock.call_count == 1


@pytest.mark.django_db
class TestSCurve:
    def test_curves_available(self):
        payload = DerivedAsOfSCurveService(build_sparse_column_project()).build_scurve(EVMFilters())
        assert payload["available"] is True
        assert payload["historical"] is False

    def test_reconstruction_banner(self):
        payload = DerivedAsOfSCurveService(build_sparse_column_project()).build_scurve(EVMFilters())
        assert "not imported" in payload["banner"].lower()

    def test_period_table(self):
        p = build_p6xml_full_project()
        svc = DerivedAsOfSCurveService(p)
        assert svc.build_periods(EVMFilters())["rows"]


@pytest.mark.django_db
class TestPortability:
    def test_empty_unavailable(self):
        assert CurrentEVMAnalyticsService(build_empty_project()).build()["mode"] == "unavailable"

    def test_no_trend_engine(self):
        p = build_p6xml_full_project()
        with patch("scheduling.services.trend_engine.compute_trend_analysis") as mock:
            CurrentEVMAnalyticsService(p).build()
        mock.assert_not_called()


@pytest.mark.django_db
class TestEVMAPI:
    def test_evm_page(self, client):
        p = build_sparse_column_project()
        _member_client(client, p)
        assert (
            client.get(
                reverse("scheduling:executive_controls_evm", kwargs={"pk": p.pk})
            ).status_code
            == 200
        )

    def test_current_json(self, client):
        p = build_sparse_column_project()
        _member_client(client, p)
        r = client.get(reverse("scheduling:executive_controls_evm_current", kwargs={"pk": p.pk}))
        assert r.status_code == 200
        assert "mode" in r.json()

    def test_post_405(self, client):
        p = build_sparse_column_project()
        _member_client(client, p)
        assert (
            client.post(
                reverse("scheduling:executive_controls_evm_current", kwargs={"pk": p.pk})
            ).status_code
            == 405
        )

    def test_payload_bounded(self, client):
        p = build_p6xml_full_project()
        _member_client(client, p)
        body = client.get(
            reverse("scheduling:executive_controls_evm_scurve", kwargs={"pk": p.pk})
        ).json()
        assert len(json.dumps(body)) < 500_000
