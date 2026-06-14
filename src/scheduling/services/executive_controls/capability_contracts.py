# scheduling/services/executive_controls/capability_contracts.py
"""Dataclass contracts for project analytics capability profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilityResult:
    """Single feature capability evaluation."""

    feature_id: str
    state: str
    available: bool
    authority: str
    source: str
    planner_source_type: str
    numerator: int | None
    denominator: int | None
    coverage_pct: float | None
    required_fields: tuple[str, ...]
    present_fields: tuple[str, ...]
    missing_reasons: tuple[str, ...]
    caveats: tuple[str, ...]
    supported_analytical_mode: str
    disabled_dependent_features: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SeriesAuthorityContract:
    """Reusable guard for time-series provenance — not imported history."""

    series_type: str
    historical_authority: str
    available: bool
    caveat: str
    source_versions: tuple[str, ...] = ()
    data_point_provenance: str = "current_task_snapshot"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityProfilePayload:
    """Full project capability profile response shape."""

    profile_version: str
    project_id: str
    analytical_state: str
    source_type: str
    source_identity: dict[str, Any] | None
    data_date: str
    data_date_authoritative: bool
    capabilities: dict[str, dict[str, Any]]
    dependencies: dict[str, list[str]]
    series_contracts: dict[str, dict[str, Any]]
    recommended_visible_pages: list[str]
    hidden_pages: list[str]
    disabled_pages: list[str]
    page_reasons: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    banner: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
