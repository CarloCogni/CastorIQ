# scheduling/services/executive_controls/contracts.py
"""E8-A dataclass contracts for analytical payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class E8MetricDefinition:
    """Immutable e8-v1 metric methodology definition."""

    metric_id: str
    label: str
    business_question: str
    formula: str
    numerator_definition: str
    denominator_definition: str
    weighting_method: str
    primary_source: str
    source_authority: str
    inclusion_rules: str
    exclusion_rules: str
    baseline_semantics: str
    data_date_semantics: str
    missing_data_behavior: str
    coverage_metric: str
    authority_level: str
    caveat: str
    drilldown_route: str
    compatible_filters: tuple[str, ...] = ()
    version: str = "e8-v1"
    drilldown_filter: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class E8MetricResult:
    """Computed metric with explicit availability and provenance."""

    metric_id: str
    label: str
    available: bool
    value: int | float | None
    unit: str
    numerator: int | float | None
    denominator: int | float | None
    percentage: float | None
    source: str
    authority: str
    methodology_version: str
    coverage: dict[str, Any] | None
    caveats: list[str]
    data_date: str | None
    calculated_at: str
    analytical_state: str
    drilldown_route: str
    drilldown_filter: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DelayClassificationResult:
    """Per-task delay semantics from DelayClassificationService."""

    task_id: str
    primary_delay_type: str
    secondary_indicators: list[str]
    is_completed: bool
    is_current_risk: bool
    baseline_finish: str | None
    actual_finish: str | None
    current_forecast_finish: str | None
    variance_days: int | None
    day_type: str
    calendar_fallback: bool
    total_float: int | None
    is_critical: bool
    evidence_fields: list[str]
    source_authority: str
    missing_fields: list[str]
    caveats: list[str]
    explanation: str
    trusted_entity_count: int = 0
    scope_classification: str = "unknown"
    scope_authoritative: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScopeClassificationResult:
    """Per-task scope classification from ScopeClassificationResolver."""

    classification: str
    authoritative: bool
    authority_level: str
    source_field: str | None
    source_value: str | None
    confidence: float | None
    explanation: str
    evidence: list[str]
    alternatives: list[str]
    requires_mapping: bool
    caveats: list[str]
    trusted_model_linked: bool
    is_non_physical: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoverageItem:
    """Single coverage ratio or count with explicit denominator."""

    metric_id: str
    label: str
    numerator: int
    denominator: int | None
    percentage: float | None
    available: bool
    authority: str
    source: str
    caveat: str
    drilldown_filter: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
