# scheduling/services/executive_controls/enums.py
"""E8-A analytical vocabulary enums."""

from __future__ import annotations

from enum import StrEnum


class MetricAuthority(StrEnum):
    """How much trust an analytical metric carries."""

    AUTHORITATIVE = "authoritative"
    GOVERNED = "governed"
    DERIVED = "derived"
    PROXY = "proxy"
    SUGGESTION = "suggestion"
    UNAVAILABLE = "unavailable"


class SourceAuthority(StrEnum):
    """Provenance label for a data field or metric input."""

    BASELINE_SCHEDULE = "baseline_schedule"
    CURRENT_SCHEDULE = "current_schedule"
    ACTUAL_PROGRESS = "actual_progress"
    ACTUAL_COST = "actual_cost"
    QUANTITY_PROGRESS = "quantity_progress"
    DURATION_PROGRESS = "duration_progress"
    RESOURCE_ASSIGNMENT = "resource_assignment"
    TRUSTED_BINDING = "trusted_binding"
    REVIEW_SUGGESTION = "review_suggestion"
    PROPERTY_METADATA = "property_metadata"
    LEGACY_M2M = "legacy_m2m"
    CALCULATED = "calculated"
    UNKNOWN = "unknown"


class ScopeClassification(StrEnum):
    """Physical / non-physical work scope bucket."""

    PHYSICAL_CONSTRUCTION = "physical_construction"
    ENGINEERING_DESIGN = "engineering_design"
    PROCUREMENT = "procurement"
    APPROVALS_AUTHORITY = "approvals_authority"
    TESTING_COMMISSIONING = "testing_commissioning"
    HANDOVER_SNAGGING = "handover_snagging"
    MANAGEMENT_ADMINISTRATIVE = "management_administrative"
    MILESTONE = "milestone"
    UNKNOWN = "unknown"


class DelayType(StrEnum):
    """Canonical delay taxonomy — never merge into a generic slip field."""

    COMPLETED_LATE = "completed_late"
    CURRENTLY_LATE = "currently_late"
    FORECAST_LATE = "forecast_late"
    BASELINE_FINISH_VARIANCE = "baseline_finish_variance"
    ACTUAL_FINISH_VARIANCE = "actual_finish_variance"
    CRITICAL = "critical"
    NEGATIVE_FLOAT = "negative_float"
    ZERO_FLOAT = "zero_float"
    NEAR_CRITICAL = "near_critical"
    MISSING_BASELINE = "missing_baseline"
    MISSING_FORECAST = "missing_forecast"
    NOT_LATE = "not_late"


class AnalyticalState(StrEnum):
    """Whether metrics are live or frozen."""

    LIVE_CURRENT = "live_current"
    FROZEN_SNAPSHOT = "frozen_snapshot"
    HISTORICAL_VERSION = "historical_version"
    UNAVAILABLE = "unavailable"


class DayType(StrEnum):
    """Calendar semantics for delay day counts."""

    WORKING = "working"
    CALENDAR = "calendar"


class CapabilityState(StrEnum):
    """Whether an analytical feature may be shown."""

    AVAILABLE = "available"
    AVAILABLE_WITH_CAVEATS = "available_with_caveats"
    PROXY_ONLY = "proxy_only"
    UNAVAILABLE = "unavailable"


class MissingReason(StrEnum):
    """Explicit reason a capability input is absent."""

    NO_TASKS = "no_tasks"
    NO_DEPENDENCIES = "no_dependencies"
    NO_BASELINE_REFERENCE = "no_baseline_reference"
    NO_ACTUAL_DATES = "no_actual_dates"
    NO_PROGRESS = "no_progress"
    NO_DATA_DATE = "no_data_date"
    NO_CALENDAR = "no_calendar"
    NO_FLOAT = "no_float"
    NO_COST_BASELINE = "no_cost_baseline"
    NO_ACTUAL_COST = "no_actual_cost"
    NO_RESOURCE_ASSIGNMENTS = "no_resource_assignments"
    NO_HIERARCHY_LINK = "no_hierarchy_link"
    NO_SCOPE_CLASSIFICATION = "no_scope_classification"
    NO_IFC = "no_ifc"
    NO_TRUSTED_BINDINGS = "no_trusted_bindings"
    NO_SNAPSHOT = "no_snapshot"
    NO_HISTORICAL_VERSIONS = "no_historical_versions"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    UNSUPPORTED_SOURCE_MAPPING = "unsupported_source_mapping"


class FeatureId(StrEnum):
    """Planner-neutral analytical feature identifiers."""

    SCHEDULE_OVERVIEW = "schedule_overview"
    DELAY_CURRENT = "delay_current"
    DELAY_WORKING_DAYS = "delay_working_days"
    CRITICAL_PATH = "critical_path"
    NEGATIVE_FLOAT = "negative_float"
    NEAR_CRITICAL = "near_critical"
    SCHEDULE_PERFORMANCE = "schedule_performance"
    CURRENT_SPI = "current_spi"
    COST_EVM = "cost_evm"
    CURRENT_CPI = "current_cpi"
    EAC = "eac"
    ETC = "etc"
    VAC = "vac"
    TCPI = "tcpi"
    DERIVED_COST_CURVE = "derived_cost_curve"
    HISTORICAL_SPI_TREND = "historical_spi_trend"
    HISTORICAL_CPI_TREND = "historical_cpi_trend"
    BASELINE_VS_CURRENT_CURVE = "baseline_vs_current_curve"
    REPEATABLE_HISTORICAL_REPORT = "repeatable_historical_report"
    EQUIVALENT_WORKFORCE = "equivalent_workforce"
    ACTUAL_HEADCOUNT = "actual_headcount"
    STAGE_MATRIX = "stage_matrix"
    WBS_MATRIX = "wbs_matrix"
    TRADE_ANALYSIS = "trade_analysis"
    DISCIPLINE_ANALYSIS = "discipline_analysis"
    LOCATION_ANALYSIS = "location_analysis"
    MODEL_IMPACT = "model_impact"
    TRUSTED_MODEL_DRILLDOWN = "trusted_model_drilldown"
    MODEL_COVERAGE = "model_coverage"


class SeriesType(StrEnum):
    """Provenance classification for time-series analytics."""

    CURRENT_SNAPSHOT_RECONSTRUCTION = "current_snapshot_reconstruction"
    IMPORTED_HISTORICAL = "imported_historical"
    FROZEN_SNAPSHOT_HISTORY = "frozen_snapshot_history"
