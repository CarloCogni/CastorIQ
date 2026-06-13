# scheduling/services/governance/metric_methodology.py
"""Governance metric definition contract and registry (E2-F)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID

METHODOLOGY_VERSION = "e2-f-v1"


@dataclass(frozen=True)
class GovernanceMetricDefinition:
    """Immutable metric methodology — every KPI must declare its semantics."""

    metric_id: str
    label: str
    description: str
    numerator_definition: str
    denominator_definition: str
    source: str
    trust_policy: str
    inclusion_rules: str
    exclusion_rules: str
    coverage_caveat: str
    unit: str
    drilldown_route: str
    drilldown_filter: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GovernanceMetricResult:
    """Computed metric value with explicit numerator/denominator."""

    metric_id: str
    label: str
    value: int | float | None
    numerator: int | None
    denominator: int | None
    percentage: float | None
    available: bool
    caveat: str
    source: str
    methodology_version: str
    trust_policy: str
    drilldown_route: str
    drilldown_filter: dict[str, str]
    calculated_at: str
    data_authority: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _metric_result(
    definition: GovernanceMetricDefinition,
    *,
    numerator: int | None,
    denominator: int | None,
    value: int | float | None = None,
    data_authority: str,
) -> GovernanceMetricResult:
    """Build result; percentage only when denominator is positive and meaningful."""
    pct: float | None = None
    available = numerator is not None
    if denominator is not None and denominator > 0 and numerator is not None:
        pct = round(100.0 * numerator / denominator, 2)
    elif denominator == 0:
        available = False
    return GovernanceMetricResult(
        metric_id=definition.metric_id,
        label=definition.label,
        value=value if value is not None else numerator,
        numerator=numerator,
        denominator=denominator,
        percentage=pct,
        available=available,
        caveat=definition.coverage_caveat,
        source=definition.source,
        methodology_version=METHODOLOGY_VERSION,
        trust_policy=definition.trust_policy,
        drilldown_route=definition.drilldown_route,
        drilldown_filter=dict(definition.drilldown_filter),
        calculated_at=datetime.now(UTC).isoformat(),
        data_authority=data_authority,
    )


METRIC_REGISTRY: dict[str, GovernanceMetricDefinition] = {
    "trusted_bindings": GovernanceMetricDefinition(
        metric_id="trusted_bindings",
        label="Active trusted bindings",
        description="Accepted schedule-to-model bindings counted as governance truth.",
        numerator_definition="TaskEntityBinding rows with is_active=True, governance_status=trusted, needs_review=False",
        denominator_definition="N/A — count metric",
        source="TaskEntityBinding via BindingGovernanceReader.trusted_bindings_qs",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Active trusted bindings in project scope",
        exclusion_rules="Review, rejected, reversed, superseded, inactive",
        coverage_caveat="Pre-audit legacy trusted rows count as trusted; no historical approval event implied.",
        unit="bindings",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "trusted"},
    ),
    "active_review_bindings": GovernanceMetricDefinition(
        metric_id="active_review_bindings",
        label="Active review suggestions",
        description="Bindings awaiting explicit human approval.",
        numerator_definition="TaskEntityBinding active_review + needs_review=True + is_active=True",
        denominator_definition="N/A — count metric",
        source="TaskEntityBinding via BindingGovernanceReader.review_bindings_qs",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Active review queue only",
        exclusion_rules="Rejected/inactive suggestions",
        coverage_caveat="Confidence is informational only — not authority.",
        unit="bindings",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "review"},
    ),
    "trusted_task_coverage": GovernanceMetricDefinition(
        metric_id="trusted_task_coverage",
        label="Trusted-linked schedule activities",
        description="Distinct tasks with at least one active trusted binding.",
        numerator_definition="Distinct task_id on active trusted bindings",
        denominator_definition="All Task rows for project (includes non-physical, milestones, procurement)",
        source="Task + TaskEntityBinding",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="All schedule tasks in project",
        exclusion_rules="None at task level",
        coverage_caveat=(
            "Denominator is all schedule activities — not limited to physical/model-scope work. "
            "Physical-scope classification unavailable for a separate 4D completeness percentage."
        ),
        unit="tasks",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "trusted"},
    ),
    "trusted_entity_coverage": GovernanceMetricDefinition(
        metric_id="trusted_entity_coverage",
        label="Trusted-linked IFC entities",
        description="Indexed IFC entities with at least one active trusted binding.",
        numerator_definition="Distinct entity_global_id on active trusted bindings intersect project IFC scope",
        denominator_definition="IFCEntity rows on completed IFC files for project",
        source="IFCEntity + TaskEntityBinding",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Completed IFC file entity index",
        exclusion_rules="Entities excluded by indexer policy not separately counted in MVP",
        coverage_caveat="Denominator is indexed entities — not raw IFC file entity count before indexing.",
        unit="entities",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "trusted"},
    ),
    "property_hint_entities": GovernanceMetricDefinition(
        metric_id="property_hint_entities",
        label="Property hint entities",
        description="IFC entities with Activity ID property but no trusted binding.",
        numerator_definition="IFCEntity with Activity ID property not in trusted entity set",
        denominator_definition="N/A — separate evidence channel",
        source="IFCEntity.properties + BindingGovernanceReader",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Property metadata evidence only",
        exclusion_rules="Never combined into trusted counts",
        coverage_caveat="Property hints are not trusted links — evidence only.",
        unit="entities",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "property_hints"},
    ),
    "legacy_m2m_only": GovernanceMetricDefinition(
        metric_id="legacy_m2m_only",
        label="Legacy M2M-only relations",
        description="Task↔entity M2M pairs without matching active trusted binding.",
        numerator_definition="Task.ifc_entities M2M pairs lacking trusted binding for same global_id",
        denominator_definition="N/A — compatibility diagnostic",
        source="Task.ifc_entities + TaskEntityBinding",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Legacy compatibility channel",
        exclusion_rules="Not trusted truth",
        coverage_caveat="Legacy M2M may lag binding truth — use reconciliation for parity repair.",
        unit="relations",
        drilldown_route="link_governance_review_queue",
        drilldown_filter={"mode": "legacy_only"},
    ),
    "governance_events_total": GovernanceMetricDefinition(
        metric_id="governance_events_total",
        label="Governance audit events",
        description="Append-only decision events recorded prospectively after migration 0024.",
        numerator_definition="BindingGovernanceEvent rows for project",
        denominator_definition="N/A",
        source="BindingGovernanceEvent",
        trust_policy=TRUSTED_BINDING_POLICY_ID,
        inclusion_rules="Events after E2-E migration",
        exclusion_rules="No backfilled historical approvals",
        coverage_caveat=(
            "Zero events does not mean zero historical human decisions — "
            "pre-audit trusted bindings are legacy baseline."
        ),
        unit="events",
        drilldown_route="link_governance_audit",
        drilldown_filter={},
    ),
}


def build_count_metric(
    metric_id: str, count: int, *, data_authority: str
) -> GovernanceMetricResult:
    """Build a simple count metric from registry definition."""
    definition = METRIC_REGISTRY[metric_id]
    return _metric_result(
        definition,
        numerator=count,
        denominator=None,
        data_authority=data_authority,
    )


def build_ratio_metric(
    metric_id: str,
    *,
    numerator: int,
    denominator: int | None,
    data_authority: str,
) -> GovernanceMetricResult:
    """Build coverage ratio metric; percentage unavailable when denominator unknown."""
    definition = METRIC_REGISTRY[metric_id]
    if denominator is None:
        return _metric_result(
            definition,
            numerator=numerator,
            denominator=None,
            data_authority=data_authority,
        )
    return _metric_result(
        definition,
        numerator=numerator,
        denominator=denominator,
        data_authority=data_authority,
    )


def methodology_registry_payload() -> list[dict[str, Any]]:
    """Export all metric definitions for API/UI methodology drawer."""
    return [d.to_dict() for d in METRIC_REGISTRY.values()]
