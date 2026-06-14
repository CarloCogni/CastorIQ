# scheduling/services/executive_controls/series_authority.py
"""Historical/trend safety guard — series provenance contracts."""

from __future__ import annotations

from scheduling.services.executive_controls.capability_contracts import SeriesAuthorityContract
from scheduling.services.executive_controls.enums import MetricAuthority, SeriesType

DERIVED_CURVE_CAVEAT = (
    "Derived as-of S-curve — mathematically reconstructed from the current schedule "
    "snapshot. Not imported historical time-phasing."
)

HISTORICAL_UNAVAILABLE_CAVEAT = (
    "Imported historical time-series unavailable — requires AnalyticalSnapshot or "
    "schedule version history."
)

FROZEN_SNAPSHOT_UNAVAILABLE = (
    "Frozen snapshot history unavailable — no AnalyticalSnapshot records exist."
)


def derived_as_of_curve_contract(*, schedulable_tasks: int) -> SeriesAuthorityContract:
    """Current-state PV/EV/AC reconstruction — not historical truth."""
    available = schedulable_tasks > 0
    return SeriesAuthorityContract(
        series_type=SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value,
        historical_authority=MetricAuthority.DERIVED.value,
        available=available,
        caveat=DERIVED_CURVE_CAVEAT
        if available
        else "No schedulable tasks for curve reconstruction.",
        source_versions=(),
        data_point_provenance="current_task_snapshot",
    )


def imported_historical_contract() -> SeriesAuthorityContract:
    """Imported historical series — blocked until schema exists."""
    return SeriesAuthorityContract(
        series_type=SeriesType.IMPORTED_HISTORICAL.value,
        historical_authority=MetricAuthority.UNAVAILABLE.value,
        available=False,
        caveat=HISTORICAL_UNAVAILABLE_CAVEAT,
        source_versions=(),
        data_point_provenance="none",
    )


def frozen_snapshot_history_contract() -> SeriesAuthorityContract:
    """Point-in-time frozen snapshots — blocked until schema exists."""
    return SeriesAuthorityContract(
        series_type=SeriesType.FROZEN_SNAPSHOT_HISTORY.value,
        historical_authority=MetricAuthority.UNAVAILABLE.value,
        available=False,
        caveat=FROZEN_SNAPSHOT_UNAVAILABLE,
        source_versions=(),
        data_point_provenance="none",
    )


def current_point_contract(*, available: bool, caveat: str = "") -> SeriesAuthorityContract:
    """Single as-of metric — not a time series."""
    return SeriesAuthorityContract(
        series_type=SeriesType.CURRENT_POINT.value,
        historical_authority=MetricAuthority.DERIVED.value,
        available=available,
        caveat=caveat or "Current point at data date — not a historical trend.",
        source_versions=(),
        data_point_provenance="current_task_snapshot",
    )


def forecast_projection_contract(*, available: bool) -> SeriesAuthorityContract:
    """Derived forward projection — explicitly not imported history."""
    return SeriesAuthorityContract(
        series_type=SeriesType.FORECAST_PROJECTION.value,
        historical_authority=MetricAuthority.DERIVED.value,
        available=available,
        caveat="Derived projection from current SPI/CPI — not imported historical forecast.",
        source_versions=(),
        data_point_provenance="derived_forecast",
    )


def build_series_contracts(*, schedulable_tasks: int) -> dict[str, dict]:
    """Return all series authority contracts for E8 surfaces."""
    derived = derived_as_of_curve_contract(schedulable_tasks=schedulable_tasks)
    imported = imported_historical_contract()
    frozen = frozen_snapshot_history_contract()
    current_pt = current_point_contract(available=schedulable_tasks > 0)
    forecast = forecast_projection_contract(available=schedulable_tasks > 0)
    return {
        "current_point": current_pt.to_dict(),
        "derived_as_of_curve": derived.to_dict(),
        "forecast_projection": forecast.to_dict(),
        "imported_historical": imported.to_dict(),
        "frozen_snapshot_history": frozen.to_dict(),
    }


def trend_engine_disclaimer() -> str:
    """Label required when trend_engine output is surfaced outside legacy controls."""
    return (
        "Trend regression derived from reconstructed current-snapshot weekly series — "
        "not imported schedule history. Do not interpret as factual historical performance."
    )
