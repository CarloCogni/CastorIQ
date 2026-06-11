# castor/scheduling/tests/test_evm_forecast.py
"""Tests for EVM performance-mode flags and guarded SPI forecast."""

from __future__ import annotations

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from scheduling.services.evm import compute_evm
from scheduling.services.evm_forecast import compute_spi_forecast

from .fixtures import make_project, make_task, make_user


class PerformanceModeTests(TestCase):
    """compute_evm() exposes performance_mode derived from use_cost."""

    def setUp(self):
        self.project = make_project()

    def test_duration_proxy_returns_schedule_performance_mode(self):
        """No task costs → schedule_performance and is_monetary_evm false."""
        make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
        result = compute_evm(str(self.project.pk), as_of_date=date(2025, 1, 20))

        assert result["has_data"] is True
        assert result["is_monetary_evm"] is False
        assert result["performance_mode"] == "schedule_performance"
        assert "duration" in result["performance_mode_label"].lower()

    def test_cost_project_returns_cost_evm_mode(self):
        """Tasks with schedule cost → cost_evm and is_monetary_evm true."""
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            cost=100,
        )
        result = compute_evm(str(self.project.pk), as_of_date=date(2025, 1, 20))

        assert result["is_monetary_evm"] is True
        assert result["performance_mode"] == "cost_evm"
        assert "Monetary EVM" in result["performance_mode_label"]


class SpiForecastGuardTests(TestCase):
    """spi_forecast payload honours EV, SPI, and sane-horizon guards."""

    def setUp(self):
        self.project = make_project()

    def test_ev_zero_suppresses_spi_forecast(self):
        """Future-only work → EV=0 → forecast suppressed."""
        make_task(
            self.project,
            start_date=date(2030, 1, 1),
            end_date=date(2030, 6, 30),
            cost=1000,
            status="planned",
        )
        result = compute_evm(str(self.project.pk), as_of_date=date(2025, 1, 1))

        fc = result["spi_forecast"]
        assert fc["suppressed"] is True
        assert fc["date"] is None
        assert fc["variance_days"] is None
        assert "earned value" in (fc["reason"] or "").lower()

    def test_spi_zero_suppresses_forecast_helper(self):
        """Direct helper: SPI <= 0 with positive EV suppresses date."""
        fc = compute_spi_forecast(
            spi=0.0,
            ev=50.0,
            project_start="2025-01-01",
            project_end="2025-06-30",
            as_of="2025-03-01",
        )
        assert fc["suppressed"] is True
        assert fc["date"] is None

    def test_sane_horizon_suppresses_unrealistic_forecast(self):
        """Very low SPI with some EV → date beyond sane horizon is suppressed."""
        fc = compute_spi_forecast(
            spi=0.01,
            ev=1.0,
            project_start="2025-01-01",
            project_end="2025-06-30",
            as_of="2025-03-01",
        )
        assert fc["suppressed"] is True
        assert fc["date"] is None
        assert "SPI 0.01" in (fc["reason"] or "")

    def test_normal_spi_returns_forecast_date(self):
        """Healthy SPI with earned value returns a forecast date."""
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 30),
            cost=1000,
            status="complete",
        )
        result = compute_evm(str(self.project.pk), as_of_date=date(2025, 3, 1))

        fc = result["spi_forecast"]
        assert fc["suppressed"] is False
        assert fc["date"] is not None
        assert fc["reason"] is None
        assert isinstance(fc["variance_days"], int)


class EVMDataViewForecastTests(TestCase):
    """EVMDataView JSON includes performance mode and spi_forecast."""

    def setUp(self):
        from django.test import Client

        self.client = Client()
        self.user = make_user()
        self.project = make_project(owner=self.user)
        self.client.force_login(self.user)
        self.url = reverse("scheduling:evm_data", kwargs={"pk": self.project.pk})

    def test_evm_endpoint_includes_mode_and_forecast_fields(self):
        """GET /evm/ returns performance_mode, label, and spi_forecast."""
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 30),
            cost=500,
        )
        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)

        for key in (
            "performance_mode",
            "performance_mode_label",
            "is_monetary_evm",
            "spi_forecast",
        ):
            assert key in data, f"missing {key}"

        assert "suppressed" in data["spi_forecast"]
        assert "date" in data["spi_forecast"]
        assert "reason" in data["spi_forecast"]
        assert "variance_days" in data["spi_forecast"]
