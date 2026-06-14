# scheduling/services/source_version/contracts.py
"""DTO contracts for source version foundation services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActivityIdentityResult:
    """Outcome of resolving or creating a ScheduleActivity."""

    activity_id: str | None
    canonical_activity_key: str
    identity_status: str
    created: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "canonical_activity_key": self.canonical_activity_key,
            "identity_status": self.identity_status,
            "created": self.created,
            "error": self.error,
        }


@dataclass(frozen=True)
class SourceVersionResult:
    """Outcome of source version service operations."""

    version_id: str | None
    status: str
    version_number: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "status": self.status,
            "version_number": self.version_number,
            "error": self.error,
        }


@dataclass(frozen=True)
class ImportRunResult:
    """Outcome of import run lifecycle transitions."""

    run_id: str | None
    status: str
    source_version_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "source_version_id": self.source_version_id,
            "error": self.error,
        }


@dataclass
class ImportRunCounts:
    """Optional count payload when completing an import run."""

    task_count: int = 0
    dependency_count: int = 0
    skipped_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    validation_summary: dict[str, Any] = field(default_factory=dict)
