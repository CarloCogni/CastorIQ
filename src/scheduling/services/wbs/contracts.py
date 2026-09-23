# scheduling/services/wbs/contracts.py
"""Normalized canonical WBS population DTOs (DF-C2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WBSVersionPopulationDTO:
    """Candidate WBSVersion metadata from an import adapter."""

    origin: str
    name: str
    code: str = ""
    data_date: Any = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WBSNodePopulationDTO:
    """One canonical WBS node from normalized adapter output."""

    external_id: str
    name: str
    code: str = ""
    external_parent_id: str = ""
    sequence: int = 0
    node_type: str = "unknown"
    identity_status: str = "resolved"
    authority: str = "source"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskWBSReferenceDTO:
    """Explicit Task→WBS reference from import evidence."""

    task_import_key: str
    external_wbs_id: str
    evidence_source: str
    authority: str = "source"
    unresolved_reason: str = ""


@dataclass
class CanonicalWBSPopulationDTO:
    """Full normalized payload for canonical WBS persistence."""

    version: WBSVersionPopulationDTO | None
    nodes: list[WBSNodePopulationDTO] = field(default_factory=list)
    task_references: list[TaskWBSReferenceDTO] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    adapter_id: str = ""
    has_wbs_evidence: bool = False

    def blocking_errors(self) -> list[str]:
        return list(self.errors)


@dataclass
class WBSPopulationResult:
    """Outcome of canonical WBS population or dry-run analysis."""

    adapter_id: str
    wbs_version_id: str | None = None
    activated: bool = False
    node_count: int = 0
    valid_nodes: int = 0
    unresolved_nodes: int = 0
    total_tasks: int = 0
    assigned_tasks: int = 0
    unassigned_tasks: int = 0
    unknown_references: int = 0
    assignment_coverage_pct: float | None = None
    hierarchy_valid: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False

    def to_summary(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "wbs_version_id": self.wbs_version_id,
            "activated": self.activated,
            "node_count": self.node_count,
            "valid_nodes": self.valid_nodes,
            "unresolved_nodes": self.unresolved_nodes,
            "total_tasks": self.total_tasks,
            "assigned_tasks": self.assigned_tasks,
            "unassigned_tasks": self.unassigned_tasks,
            "unknown_references": self.unknown_references,
            "assignment_coverage_pct": self.assignment_coverage_pct,
            "hierarchy_valid": self.hierarchy_valid,
            "warnings": self.warnings,
            "errors": self.errors,
            "dry_run": self.dry_run,
        }
