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
