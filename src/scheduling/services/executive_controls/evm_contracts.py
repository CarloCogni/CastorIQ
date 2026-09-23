# scheduling/services/executive_controls/evm_contracts.py
"""E8-D current-point and series result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CurrentMetricResult:
    """Single current-point EVM metric with provenance."""

    metric_id: str
    label: str
    available: bool
    value: float | None
    unit: str
    source: str
    authority: str
    coverage: dict[str, Any]
    formula: str
    caveat: str
    data_date: str
    drilldown: str
    missing_reason: str = ""
    display_value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CurveSeriesResult:
    """One derived as-of curve with authority metadata."""

    curve_id: str
    label: str
    series_type: str
    authority: str
    historical: bool
    repeatable: bool
    unit: str
    granularity: str
    data_date: str
    calculated_at: str
    coverage: dict[str, Any]
    caveat: str
    weighting_mode: str
    points: tuple[dict[str, Any], ...]
    included_tasks: int
    excluded_tasks: int
    missing_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PeriodTableRow:
    """Underlying period row reconciling to chart points."""

    period: str
    period_start: str
    period_end: str
    cumulative_pv: float | None
    cumulative_ev: float | None
    cumulative_ac: float | None
    cumulative_pv_pct: float | None
    cumulative_ev_pct: float | None
    cumulative_ac_pct: float | None
    included_population: int
    excluded_population: int
    source_snapshot_timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PeriodTablePayload:
    """Paginated period table for E8-D."""

    rows: list[dict[str, Any]]
    pagination: dict[str, Any]
    data_date_marker: str
    series_type: str
    caveat: str
    calculated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CurrentEVMPayload:
    """Full current-point analytics response."""

    project_id: str
    methodology_version: str
    capability_profile_version: str
    mode: str
    mode_label: str
    data_date: str
    data_date_authoritative: bool
    calculated_at: str
    currency_assumption: str
    cost_basis: str
    metrics: dict[str, dict[str, Any]]
    unavailable_metrics: dict[str, str]
    series_contracts: dict[str, dict[str, Any]]
    coverage: dict[str, Any]
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
