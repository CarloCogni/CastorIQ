# scheduling/services/executive_controls/current_evm_analytics.py
"""E8-D current-point EVM analytics — capability-gated, no historical claims."""

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

logger = logging.getLogger(__name__)

CURRENCY_ASSUMPTION = "Schedule currency units — no FX conversion applied."
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


def _compute_tcpi(*, bac: float, ev: float, ac: float) -> tuple[float | None, str]:
    budget_remaining = bac - ac
    if budget_remaining <= 0:
        return None, "TCPI undefined — BAC exhausted or exceeded by actual cost."
    return round((bac - ev) / budget_remaining, 3), ""


def _compute_etc(
    *, eac: float | None, ac: float | None, bac: float, ev: float, cpi: float | None
) -> float | None:
    if eac is not None and ac is not None:
        return round(eac - ac, 2)
    if cpi and cpi > 0:
        return round((bac - ev) / cpi, 2)
    return None


class CurrentEVMAnalyticsService:
    """Current-point PV/EV/AC/SPI/CPI and forecasts — one compute_evm() per session."""

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

    def _resolve_mode(
        self, evm: dict[str, Any], caps: dict[str, dict], requested: str
    ) -> tuple[str, str]:
        cost_ok = caps[FeatureId.COST_EVM.value]["available"] and evm.get("use_cost")
        sched_ok = (
            caps[FeatureId.CURRENT_SPI.value]["available"]
            or caps[FeatureId.SCHEDULE_PERFORMANCE.value]["available"]
        )

        if requested == "cost_evm" and cost_ok:
            return "cost_evm", evm.get("performance_mode_label", "Cost EVM")
        if requested == "schedule_performance" and sched_ok:
            return "schedule_performance", "Schedule Performance (duration-weighted proxy)"
        if cost_ok and evm.get("performance_mode") == "cost_evm":
            return "cost_evm", evm.get("performance_mode_label", "Cost EVM")
        if sched_ok and evm.get("has_data"):
            return "schedule_performance", evm.get("performance_mode_label", "Schedule Performance")
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
        """Return current-point EVM payload."""
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
                caveats=["No EVM data for this project."],
            )
            return payload.to_dict()

        resolved_mode, mode_label = self._resolve_mode(evm, caps, mode)
        cost_mode = resolved_mode == "cost_evm"
        ac_available = (
            bool(evm.get("ac_available")) and caps[FeatureId.CURRENT_CPI.value]["available"]
        )

        coverage = {
            "cost_coverage_pct": evm.get("cost_coverage_pct"),
            "ac_coverage_pct": evm.get("ac_coverage_pct"),
            "schedulable_tasks": evm.get("cost_coverage_pct"),  # reuse pct context
        }

        bac = evm.get("bac")
        pv = evm.get("pv")
        ev = evm.get("ev")
        ac = evm.get("ac")
        spi = evm.get("spi")
        cpi = evm.get("cpi") if ac_available else None
        eac = evm.get("eac") if ac_available else None
        vac = evm.get("vac") if ac_available else None
        etc = _compute_etc(eac=eac, ac=ac, bac=bac, ev=ev, cpi=cpi) if ac_available else None
        tcpi_val, tcpi_reason = (
            _compute_tcpi(bac=bac, ev=ev, ac=ac) if ac_available and ac is not None else (None, "")
        )

        auth_derived = MetricAuthority.DERIVED.value
        auth_auth = (
            MetricAuthority.AUTHORITATIVE.value
            if ac_available
            else MetricAuthority.UNAVAILABLE.value
        )
        auth_proxy = MetricAuthority.PROXY.value

        spi_avail = caps[FeatureId.CURRENT_SPI.value]["available"] and spi is not None
        pv_avail = spi_avail and pv is not None
        ev_avail = spi_avail and ev is not None
        bac_avail = bac is not None

        metrics: dict[str, dict[str, Any]] = {}
        unavailable: dict[str, str] = {}

        def add(m: CurrentMetricResult) -> None:
            metrics[m.metric_id] = m.to_dict()
            if not m.available and m.missing_reason:
                unavailable[m.metric_id] = m.missing_reason

        add(
            self._metric(
                metric_id="e8.pv",
                label="Planned Value (PV)" if cost_mode else "Planned progress (PV proxy)",
                value=pv,
                unit="currency" if cost_mode else "index",
                available=pv_avail,
                authority=auth_derived if cost_mode else auth_proxy,
                formula="Σ(weight × planned_pct) at data date",
                caveat=evm.get("performance_mode_label", ""),
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
                metric_id="e8.ev",
                label="Earned Value (EV)" if cost_mode else "Earned progress",
                value=ev,
                unit="currency" if cost_mode else "index",
                available=ev_avail,
                authority=auth_derived if cost_mode else auth_proxy,
                formula="Σ(weight × earned_pct) at data date",
                caveat="Not monetary Earned Value in schedule_performance mode."
                if not cost_mode
                else "",
                data_date=data_date,
                coverage=coverage,
            )
        )
        add(
            self._metric(
                metric_id="e8.spi",
                label="Schedule Performance Index (SPI)",
                value=spi,
                unit="index",
                available=spi_avail,
                authority=auth_derived if cost_mode else auth_proxy,
                formula="EV / PV",
                caveat="Current point only — not a historical trend.",
                data_date=data_date,
                coverage=coverage,
            )
        )
        add(
            self._metric(
                metric_id="e8.bac",
                label="Budget at Completion (BAC)" if cost_mode else "Total weight (BAC proxy)",
                value=bac,
                unit="currency" if cost_mode else "index",
                available=bac_avail,
                authority=auth_derived if cost_mode else auth_proxy,
                formula=evm.get("cost_basis", ""),
                caveat=evm.get("cost_basis", ""),
                data_date=data_date,
                coverage=coverage,
            )
        )

        if cost_mode and ac_available:
            add(
                self._metric(
                    metric_id="e8.ac",
                    label="Actual Cost (AC)",
                    value=ac,
                    unit="currency",
                    available=True,
                    authority=auth_auth,
                    formula="Σ P6ResourceAssignment.actual_cost",
                    caveat="Authoritative when imported from resource assignments.",
                    data_date=data_date,
                    coverage=coverage,
                )
            )
            add(
                self._metric(
                    metric_id="e8.cpi",
                    label="Cost Performance Index (CPI)",
                    value=cpi,
                    unit="index",
                    available=cpi is not None,
                    authority=auth_derived,
                    formula="EV / AC",
                    caveat="Current point — compare using tolerance bands, not red/green alone.",
                    data_date=data_date,
                    coverage=coverage,
                )
            )
            add(
                self._metric(
                    metric_id="e8.eac",
                    label="Estimate at Completion (EAC)",
                    value=eac,
                    unit="currency",
                    available=eac is not None and caps[FeatureId.EAC.value]["available"],
                    authority=auth_derived,
                    formula="BAC / CPI",
                    caveat="Derived forecast — not imported history.",
                    data_date=data_date,
                    coverage=coverage,
                    missing_reason="EAC unavailable — insufficient cost inputs.",
                )
            )
            add(
                self._metric(
                    metric_id="e8.etc",
                    label="Estimate to Complete (ETC)",
                    value=etc,
                    unit="currency",
                    available=etc is not None and caps[FeatureId.ETC.value]["available"],
                    authority=auth_derived,
                    formula="EAC − AC",
                    caveat="Derived projection from current CPI.",
                    data_date=data_date,
                    coverage=coverage,
                )
            )
            add(
                self._metric(
                    metric_id="e8.vac",
                    label="Variance at Completion (VAC)",
                    value=vac,
                    unit="currency",
                    available=vac is not None and caps[FeatureId.VAC.value]["available"],
                    authority=auth_derived,
                    formula="BAC − EAC",
                    caveat="Derived forecast variance at completion.",
                    data_date=data_date,
                    coverage=coverage,
                )
            )
            add(
                self._metric(
                    metric_id="e8.tcpi",
                    label="To-Complete Performance Index (TCPI)",
                    value=tcpi_val,
                    unit="index",
                    available=tcpi_val is not None and caps[FeatureId.TCPI.value]["available"],
                    authority=auth_derived,
                    formula="(BAC − EV) / (BAC − AC)",
                    caveat=tcpi_reason or "Efficiency required on remaining work to meet BAC.",
                    data_date=data_date,
                    coverage=coverage,
                    missing_reason=tcpi_reason,
                )
            )
        else:
            for mid, reason in (
                ("e8.ac", evm.get("ac_disabled_reason", "Actual cost unavailable.")),
                ("e8.cpi", "CPI requires authoritative actual cost."),
                ("e8.eac", "EAC requires actual cost and CPI."),
                ("e8.etc", "ETC requires actual cost."),
                ("e8.vac", "VAC requires actual cost."),
                ("e8.tcpi", "TCPI requires actual cost below BAC."),
            ):
                unavailable[mid] = reason

        caveats = [DERIVED_BANNER]
        if not cost_mode:
            caveats.append("Schedule Performance mode — not Cost EVM.")
        if evm.get("overdue_linear_capped", 0) > 0:
            caveats.append(
                f"{evm['overdue_linear_capped']} in-progress tasks use linear EV fallback — SPI may be overstated."
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
            cost_basis=evm.get("cost_basis", ""),
            metrics=metrics,
            unavailable_metrics=unavailable,
            series_contracts=series,
            coverage=coverage,
            caveats=caveats,
        )
        return payload.to_dict()
