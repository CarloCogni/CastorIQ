# scheduling/services/governed_mapping/contracts.py
"""DTO contracts for governed mapping boundary (DF-D1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MappingProposalDTO:
    """Suggestion boundary — proposals require review before becoming effective."""

    dimension_key: str
    proposed_value: str
    target_type: str
    target_id: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    confidence: float | None = None
    rule_version: str = ""
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingTargetRef:
    """Explicit analytical target identity."""

    target_type: str
    task_id: str | None = None
    wbs_node_id: str | None = None
    entity_global_id: str | None = None
    ifc_file_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EffectiveMappingResult:
    """Resolved effective mapping for one target within a dimension."""

    dimension_key: str
    dimension_id: str
    resolution: str
    authority: str
    governance_status: str
    mapping_method: str
    values: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    inherited_from: str | None = None
    assignment_ids: list[str] = field(default_factory=list)
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
