# scheduling/services/governed_mapping/contracts.py
"""DTO contracts for governed mapping boundary (DF-D1/DF-D2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DimensionValuePopulationDTO:
    """One dimension value for population."""

    code: str = ""
    name: str = ""
    external_id: str = ""
    parent_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DimensionPopulationDTO:
    """Normalized dimension definition for population."""

    dimension_key: str
    dimension_type: str
    name: str
    structure_type: str = "flat"
    cardinality: str = "single"
    authority_policy: str = "manual_approval"
    values: list[DimensionValuePopulationDTO] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MappingTargetIdentityDTO:
    """Stable target identity — no name-only matching."""

    target_type: str
    task_id: str | None = None
    wbs_node_id: str | None = None
    entity_global_id: str | None = None
    ifc_file_id: str | None = None
    schedule_activity_id: str | None = None
    canonical_activity_key: str | None = None


@dataclass
class MappingAssignmentPopulationDTO:
    """Normalized assignment input for population."""

    dimension_key: str
    value_code: str
    target: MappingTargetIdentityDTO
    value_name: str = ""
    mapping_method: str = "imported"
    authority: str = "authoritative"
    governance_status: str = "proposed"
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    source_assignment_id: str | None = None


@dataclass
class MappingProposalDTO:
    """Suggestion boundary — proposals require review before becoming effective."""

    dimension_key: str
    proposed_value: str
    target_type: str
    target_id: str
    proposed_value_code: str = ""
    target_identity: MappingTargetIdentityDTO | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    confidence: float | None = None
    rule_version: str = ""
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProposalAdoptionResult:
    """Outcome of adopting suggestions into proposed assignments."""

    source: str
    dimension_key: str
    suggestions_inspected: int = 0
    valid_proposals: int = 0
    proposals_created: int = 0
    duplicates_skipped: int = 0
    unresolved_targets: int = 0
    unresolved_values: int = 0
    conflicts: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = True

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
    schedule_activity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EffectiveMappingProvenance:
    """Stable provenance for effective mapping resolution."""

    mapping_set_id: str | None = None
    mapping_set_revision: int | None = None
    dimension_revision: int | None = None
    source_assignment_id: str | None = None
    resolution_method: str = ""
    cross_version_outcome: str = ""
    inherited_from_target: str | None = None
    evidence_summary: dict[str, Any] = field(default_factory=dict)


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
    provenance: EffectiveMappingProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.provenance:
            payload["provenance"] = asdict(self.provenance)
        return payload


@dataclass
class CoverageBreakdown:
    """Coverage and conflict summary contract."""

    eligible_targets: int = 0
    directly_mapped: int = 0
    logical_identity_mapped: int = 0
    inherited_mapped: int = 0
    proposed_only: int = 0
    rejected: int = 0
    conflict_count: int = 0
    unmapped: int = 0
    effective_coverage_pct: float | None = None
    direct_coverage_pct: float | None = None
    inherited_coverage_pct: float | None = None
    target_type_breakdown: dict[str, int] = field(default_factory=dict)
    source_method_breakdown: dict[str, int] = field(default_factory=dict)
    proxy_coverage_pct: float | None = None
    suggestion_coverage_pct: float | None = None
    governed_approved_coverage_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WBSBranchMappingPolicyDTO:
    """Explicit WBS branch → governed value policy (no inference)."""

    dimension_key: str
    mapping_set_id: str
    wbs_version_id: str
    wbs_node_id: str
    dimension_value_id: str
    include_descendants: bool = True
    target_behavior: str = "inherit_to_tasks"
    authority: str = "approved"
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DimensionCutoverReadiness:
    """Per-dimension DF-D3 cutover readiness contract."""

    dimension_key: str
    dimension_type: str
    state: str
    effective_coverage_pct: float | None = None
    blocking_conflicts: int = 0
    source_authority: str = ""
    eligible_targets: int = 0
    unmapped: int = 0
    proposed_only: int = 0
    cross_version_blocked: int = 0
    cutover_caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CutoverReadinessSummary:
    """Trade and Package readiness evaluated independently."""

    trade_cutover_readiness: DimensionCutoverReadiness
    package_cutover_readiness: DimensionCutoverReadiness
    cutover_caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_cutover_readiness": self.trade_cutover_readiness.to_dict(),
            "package_cutover_readiness": self.package_cutover_readiness.to_dict(),
            "cutover_caveats": list(self.cutover_caveats),
        }
