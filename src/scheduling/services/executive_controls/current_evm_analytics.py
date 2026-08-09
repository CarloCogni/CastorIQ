# scheduling/services/executive_controls/current_evm_analytics.py
"""E8-D current-point schedule performance analytics — capability-gated.

Product surface never presents assignment actual cost as company cost EVM.
CPI / AC / EAC / ETC / VAC / TCPI stay Unavailable without a company cost source.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scheduling.services.executive_controls.capability_profile import PROFILE_VERSION
from scheduling.services.executive_controls.enums import FeatureId, MetricAuthority
from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession
from scheduling.services.executive_controls.evm_contracts import (
    CurrentEVMPayload,
    CurrentMetricResult,
)
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    COMPANY_COST_SOURCE_ABSENT_NOTE,
    PRODUCT_MODE_LABEL,
    company_actual_cost_source_available,
    gate_company_cost_metrics_unavailable,
)

logger = logging.getLogger(__name__)

CURRENCY_ASSUMPTION = "Schedule progress indicators — not commercial cost ledgers."
DERIVED_BANNER = (
    "Derived As-of S-Curve — reconstructed from the current schedule state. "
    "This is not imported historical performance and may change after re-import."
)


def _format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "Unavailable"
    if unit == "index":
        return f"{value:.3f}"
    if unit == "currency":
        return f"{value:,.2f}"
    if unit == "percent":
        return f"{value:.1f}%"
    return str(value)


def _progress_pct(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return round(part / whole * 100.0, 1)


class CurrentEVMAnalyticsService:
    """Current-point schedule performance payload for Controls product UI."""

    def __init__(
        self,
        project,
        *,
        capability_profile: dict[str, Any] | None = None,
        session: E8EVMComputeSession | None = None,
    ) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._capability_payload = capability_profile
        self._session = session or E8EVMComputeSession(self.project_id)

    def _capability(self) -> dict[str, Any]:
        if self._capability_payload is None:
            from scheduling.services.executive_controls.capability_profile import (
                ProjectAnalyticsCapabilityProfile,
            )

            self._capability_payload = ProjectAnalyticsCapabilityProfile(self.project).build()
        return self._capability_payload

    def _resolve_product_mode(self, evm: dict[str, Any], caps: dict[str, dict]) -> tuple[str, str]:
        """Product surface never selects Cost EVM / Monetary EVM as the mode."""
        sched_ok = (
            caps[FeatureId.CURRENT_SPI.value]["available"]
            or caps[FeatureId.SCHEDULE_PERFORMANCE.value]["available"]
            or bool(evm.get("has_data"))
        )
        if sched_ok and evm.get("has_data"):
            return "schedule_performance", PRODUCT_MODE_LABEL
        return "unavailable", "Unavailable"

    def _metric(
        self,
        *,
        metric_id: str,
        label: str,
        value: float | None,
        unit: str,
        available: bool,
        authority: str,
        formula: str,
        caveat: str,
        data_date: str,
        coverage: dict[str, Any],
        missing_reason: str = "",
    ) -> CurrentMetricResult:
        return CurrentMetricResult(
            metric_id=metric_id,
            label=label,
            available=available,
            value=value if available else None,
            unit=unit,
            source="scheduling.services.evm.compute_evm",
            authority=authority,
            coverage=coverage,
            formula=formula,
            caveat=caveat,
            data_date=data_date,
            drilldown="executive_controls_evm",
            missing_reason=missing_reason,
            display_value=_format_value(value if available else None, unit),
        )

    def build(self, *, mode: str = "auto") -> dict[str, Any]:
        """Return current-point product-surface schedule performance payload.

        ``mode`` is accepted for API compatibility but Cost EVM is never
        returned as the product mode — assignment cost is not company cost.
        """
        _ = mode  # Product surface ignores cost_evm requests.
        capability = self._capability()
        caps = capability["capabilities"]
        evm = self._session.evm()
        calculated_at = datetime.now(UTC).isoformat()
        data_date = self._session.data_date.isoformat()

        if not evm.get("has_data"):
            payload = CurrentEVMPayload(
                project_id=self.project_id,
                methodology_version=E8_METHODOLOGY_VERSION,
                capability_profile_version=PROFILE_VERSION,
                mode="unavailable",
                mode_label="Unavailable",
                data_date=data_date,
                data_date_authoritative=capability.get("data_date_authoritative", False),
                calculated_at=calculated_at,
                currency_assumption=CURRENCY_ASSUMPTION,
                cost_basis="",
                metrics={},
                unavailable_metrics={"all": "No schedulable physical tasks."},
                series_contracts=capability.get("series_contracts", {}),
                coverage={},
                caveats=["No schedule performance data for this project."],
            )
            return payload.to_dict()

        resolved_mode, mode_label = self._resolve_product_mode(evm, caps)
        baseline_evm = evm.get("baseline_evm") or {}

        coverage = {
            "cost_coverage_pct": evm.get("cost_coverage_pct"),
            "ac_coverage_pct": evm.get("ac_coverage_pct"),
            "schedulable_tasks": evm.get("cost_coverage_pct"),
            "baseline_evm": baseline_evm,
            "company_actual_cost_source": company_actual_cost_source_available(),
        }
        if baseline_evm.get("coverage"):
            coverage.update(baseline_evm["coverage"])

        bac = evm.get("bac")
        pv = evm.get("pv")
        ev = evm.get("ev")
        spi = evm.get("spi")
        planned_pct = _progress_pct(pv, bac)
        earned_pct = _progress_pct(ev, bac)

        auth_proxy = MetricAuthority.PROXY.value
        auth_derived = MetricAuthority.DERIVED.value
        auth_unavail = MetricAuthority.UNAVAILABLE.value

        spi_avail = caps[FeatureId.CURRENT_SPI.value]["available"] and spi is not None
        progress_avail = spi_avail and planned_pct is not None and earned_pct is not None

        metrics: dict[str, dict[str, Any]] = {}
        unavailable: dict[str, str] = gate_company_cost_metrics_unavailable()

        def add(m: CurrentMetricResult) -> None:
            metrics[m.metric_id] = m.to_dict()
            if not m.available and m.missing_reason:
                unavailable[m.metric_id] = m.missing_reason

        add(
            self._metric(
                metric_id="e8.spi",
                label="Schedule Performance Indicator",
                value=spi,
                unit="index",
                available=spi_avail,
                authority=auth_derived if spi_avail else auth_unavail,
                formula="EV / PV (schedule/progress weighted)",
                caveat="Based on schedule/progress inputs. Not financial cost EVM.",
                data_date=data_date,
                coverage=coverage,
                missing_reason=caps[FeatureId.CURRENT_SPI.value].get(
                    "missing_reasons", ["no_progress"]
                )[0]
                if not spi_avail
                else "",
            )
        )
        add(
            self._metric(
                metric_id="e8.pv",
                label="Planned schedule progress",
                value=planned_pct,
                unit="percent",
                available=progress_avail,
                authority=auth_proxy,
                formula="PV / BAC × 100 at data date",
                caveat="Schedule/progress indicator — not commercial Planned Value.",
                data_date=data_date,
                coverage=coverage,
                missing_reason="Planned schedule progress unavailable."
                if not progress_avail
                else "",
            )
        )
        add(
            self._metric(
                metric_id="e8.ev",
                label="Earned schedule progress",
                value=earned_pct,
                unit="percent",
                available=progress_avail,
                authority=auth_proxy,
                formula="EV / BAC × 100 at data date",
                caveat="Schedule/progress indicator — not commercial Earned Value.",
                data_date=data_date,
                coverage=coverage,
                missing_reason="Earned schedule progress unavailable."
                if not progress_avail
                else "",
            )
        )

        # Explicit unavailable company-cost metrics (not shown as available KPIs).
        for mid, reason in gate_company_cost_metrics_unavailable().items():
            label_map = {
                "e8.ac": "Actual Cost",
                "e8.cpi": "CPI",
                "e8.eac": "EAC",
                "e8.etc": "ETC",
                "e8.vac": "VAC",
                "e8.tcpi": "TCPI",
            }
            add(
                self._metric(
                    metric_id=mid,
                    label=label_map[mid],
                    value=None,
                    unit="index",
                    available=False,
                    authority=auth_unavail,
                    formula="",
                    caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
                    data_date=data_date,
                    coverage=coverage,
                    missing_reason=reason,
                )
            )

        caveats = [
            DERIVED_BANNER,
            COMPANY_COST_SOURCE_ABSENT_NOTE,
            COMPANY_ACTUAL_COST_UNAVAILABLE,
        ]
        caveats.extend(baseline_evm.get("caveats") or [])
        if evm.get("overdue_linear_capped", 0) > 0:
            caveats.append(
                f"{evm['overdue_linear_capped']} in-progress tasks use linear EV fallback — "
                "Schedule Performance Indicator may be overstated."
            )
        series = capability.get("series_contracts", {})
        if series.get("imported_historical", {}).get("caveat"):
            caveats.append(str(series["imported_historical"]["caveat"]))

        payload = CurrentEVMPayload(
            project_id=self.project_id,
            methodology_version=E8_METHODOLOGY_VERSION,
            capability_profile_version=PROFILE_VERSION,
            mode=resolved_mode,
            mode_label=mode_label,
            data_date=data_date,
            data_date_authoritative=capability.get("data_date_authoritative", False),
            calculated_at=calculated_at,
            currency_assumption=CURRENCY_ASSUMPTION,
            cost_basis="",
            metrics=metrics,
            unavailable_metrics=unavailable,
            series_contracts=series,
            coverage=coverage,
            caveats=caveats,
        )
        out = payload.to_dict()
        # Product surface never claims cost EVM availability.
        out["cost_evm_available"] = False
        out["company_actual_cost_source_available"] = company_actual_cost_source_available()
        return out
