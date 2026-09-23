# scheduling/services/governance/reconciliation_vocabulary.py
"""Read-only reconciliation status vocabulary for E2-D (not persisted)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReconciliationSeverity(StrEnum):
    """Finding severity for sorting and UI badges."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReconciliationCategory(StrEnum):
    """High-level finding grouping."""

    HEALTHY = "healthy"
    REVIEW_REQUIRED = "review_required"
    BROKEN = "broken"
    UNKNOWN = "unknown"


class ReconciliationStatus(StrEnum):
    """Per-binding or per-relation reconciliation status (E2-D)."""

    # Healthy
    VALID = "valid"
    VALID_MULTIPLE_SEQUENTIAL = "valid_multiple_sequential"
    VALID_MANUAL_OVERRIDE = "valid_manual_override"

    # Review required
    EVIDENCE_CHANGED = "evidence_changed"
    IDENTIFIER_CHANGED = "identifier_changed"
    PROPERTY_CHANGED = "property_changed"
    POSSIBLE_CONFLICT = "possible_conflict"
    CROSS_FILE_AMBIGUITY = "cross_file_ambiguity"
    POLICY_MISMATCH = "policy_mismatch"
    METHOD_REQUIRES_REVIEW = "method_requires_review"
    ACCEPTED_PLUS_REVIEW_PENDING = "accepted_plus_review_pending"

    # Broken / orphaned
    MISSING_TASK = "missing_task"
    MISSING_ENTITY = "missing_entity"
    MISSING_IFC_FILE = "missing_ifc_file"
    INVALID_PROJECT_SCOPE = "invalid_project_scope"
    DUPLICATE_PAIR = "duplicate_pair"
    INVALID_CROSS_PROJECT_REFERENCE = "invalid_cross_project_reference"

    # M2M parity
    ACCEPTED_WITHOUT_M2M = "accepted_without_m2m"
    M2M_WITHOUT_ACCEPTED = "m2m_without_accepted"
    REVIEW_WITH_M2M = "review_with_m2m"

    # Unknown / limited
    VERSION_UNKNOWN = "version_unknown"
    SOURCE_EVIDENCE_UNAVAILABLE = "source_evidence_unavailable"
    CANNOT_DETERMINE = "cannot_determine"


class RecommendedAction(StrEnum):
    """Read-only remediation suggestion — no action executed in E2-D."""

    REAFFIRM = "reaffirm"
    RE_REVIEW = "re_review"
    SUPERSEDE = "supersede"
    REVERSE = "reverse"
    RECREATE_BINDING = "recreate_binding"
    INSPECT_DUPLICATE = "inspect_duplicate"
    FIX_PROJECT_SCOPE = "fix_project_scope"
    REPAIR_M2M_PARITY = "repair_m2m_parity"
    NO_ACTION = "no_action"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class StatusDefinition:
    """Metadata for one reconciliation status."""

    status: ReconciliationStatus
    category: ReconciliationCategory
    severity: ReconciliationSeverity
    deterministic: bool
    trust_impact: str
    explanation: str
    recommended_action: RecommendedAction
    e2e_eligible: bool
    auto_action_prohibited: bool


STATUS_DEFINITIONS: dict[ReconciliationStatus, StatusDefinition] = {
    ReconciliationStatus.VALID: StatusDefinition(
        status=ReconciliationStatus.VALID,
        category=ReconciliationCategory.HEALTHY,
        severity=ReconciliationSeverity.INFO,
        deterministic=True,
        trust_impact="Accepted binding remains trusted; evidence still matches.",
        explanation="Binding evidence is consistent with current task and entity data.",
        recommended_action=RecommendedAction.NO_ACTION,
        e2e_eligible=False,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.VALID_MULTIPLE_SEQUENTIAL: StatusDefinition(
        status=ReconciliationStatus.VALID_MULTIPLE_SEQUENTIAL,
        category=ReconciliationCategory.HEALTHY,
        severity=ReconciliationSeverity.INFO,
        deterministic=True,
        trust_impact="Multiple sequential accepted tasks on one entity remain valid.",
        explanation="Non-overlapping task periods on the same entity.",
        recommended_action=RecommendedAction.NO_ACTION,
        e2e_eligible=False,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.VALID_MANUAL_OVERRIDE: StatusDefinition(
        status=ReconciliationStatus.VALID_MANUAL_OVERRIDE,
        category=ReconciliationCategory.HEALTHY,
        severity=ReconciliationSeverity.INFO,
        deterministic=True,
        trust_impact="Manual accepted binding does not require identifier equality.",
        explanation="Manual explicit selection; property mismatch is informational only.",
        recommended_action=RecommendedAction.REAFFIRM,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.EVIDENCE_CHANGED: StatusDefinition(
        status=ReconciliationStatus.EVIDENCE_CHANGED,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.HIGH,
        deterministic=True,
        trust_impact="Trust retained until authorized decision; human re-review recommended.",
        explanation="Reproducible evidence no longer matches current task or IFC property values.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.IDENTIFIER_CHANGED: StatusDefinition(
        status=ReconciliationStatus.IDENTIFIER_CHANGED,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.HIGH,
        deterministic=True,
        trust_impact="Exact/normalized identifier drift; trust not auto-revoked.",
        explanation="Task activity code or IFC Activity ID changed since binding evidence.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.PROPERTY_CHANGED: StatusDefinition(
        status=ReconciliationStatus.PROPERTY_CHANGED,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.LOW,
        deterministic=True,
        trust_impact="Informational for manual bindings; review for exact/normalized.",
        explanation="IFC property value changed relative to binding evidence.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.POSSIBLE_CONFLICT: StatusDefinition(
        status=ReconciliationStatus.POSSIBLE_CONFLICT,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.MEDIUM,
        deterministic=True,
        trust_impact="Multiple accepted mappings may conflict; trust not auto-revoked.",
        explanation="Deterministic overlap or contradiction evidence on this entity.",
        recommended_action=RecommendedAction.INSPECT_DUPLICATE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.CROSS_FILE_AMBIGUITY: StatusDefinition(
        status=ReconciliationStatus.CROSS_FILE_AMBIGUITY,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.MEDIUM,
        deterministic=True,
        trust_impact="Entity scope ambiguous across IFC files.",
        explanation="Same GlobalId appears on multiple completed IFC files in this project.",
        recommended_action=RecommendedAction.FIX_PROJECT_SCOPE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.POLICY_MISMATCH: StatusDefinition(
        status=ReconciliationStatus.POLICY_MISMATCH,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.MEDIUM,
        deterministic=True,
        trust_impact="Binding state inconsistent with trusted-binding-v1 policy.",
        explanation="Review-only method marked accepted or policy rule violated.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.METHOD_REQUIRES_REVIEW: StatusDefinition(
        status=ReconciliationStatus.METHOD_REQUIRES_REVIEW,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.INFO,
        deterministic=True,
        trust_impact="Not trusted until explicit approval.",
        explanation="Binding remains in review state by policy.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.ACCEPTED_PLUS_REVIEW_PENDING: StatusDefinition(
        status=ReconciliationStatus.ACCEPTED_PLUS_REVIEW_PENDING,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.LOW,
        deterministic=True,
        trust_impact="Accepted binding preserved; pending review on same entity.",
        explanation="Entity has both accepted and review bindings.",
        recommended_action=RecommendedAction.INSPECT_DUPLICATE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.MISSING_TASK: StatusDefinition(
        status=ReconciliationStatus.MISSING_TASK,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.CRITICAL,
        deterministic=True,
        trust_impact="Orphaned binding reference; trust should not drive reads.",
        explanation="Task row no longer exists for this binding.",
        recommended_action=RecommendedAction.RECREATE_BINDING,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.MISSING_ENTITY: StatusDefinition(
        status=ReconciliationStatus.MISSING_ENTITY,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.CRITICAL,
        deterministic=True,
        trust_impact="Entity absent from project IFC scope.",
        explanation="GlobalId not found on any completed IFC file for this project.",
        recommended_action=RecommendedAction.REVERSE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.MISSING_IFC_FILE: StatusDefinition(
        status=ReconciliationStatus.MISSING_IFC_FILE,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.CRITICAL,
        deterministic=True,
        trust_impact="IFC file reference missing or not completed.",
        explanation="Entity's IFC file is missing or not in completed status.",
        recommended_action=RecommendedAction.FIX_PROJECT_SCOPE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.INVALID_PROJECT_SCOPE: StatusDefinition(
        status=ReconciliationStatus.INVALID_PROJECT_SCOPE,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.CRITICAL,
        deterministic=True,
        trust_impact="Hard invalid scope; cannot approve or trust for simulation.",
        explanation="Binding entity is outside project IFC scope.",
        recommended_action=RecommendedAction.FIX_PROJECT_SCOPE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.DUPLICATE_PAIR: StatusDefinition(
        status=ReconciliationStatus.DUPLICATE_PAIR,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.HIGH,
        deterministic=True,
        trust_impact="Data integrity violation if duplicate rows exist.",
        explanation="More than one binding row for the same task/entity pair.",
        recommended_action=RecommendedAction.INSPECT_DUPLICATE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.INVALID_CROSS_PROJECT_REFERENCE: StatusDefinition(
        status=ReconciliationStatus.INVALID_CROSS_PROJECT_REFERENCE,
        category=ReconciliationCategory.BROKEN,
        severity=ReconciliationSeverity.CRITICAL,
        deterministic=True,
        trust_impact="Cross-project reference invalid.",
        explanation="Task project does not match reconciliation project scope.",
        recommended_action=RecommendedAction.FIX_PROJECT_SCOPE,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.ACCEPTED_WITHOUT_M2M: StatusDefinition(
        status=ReconciliationStatus.ACCEPTED_WITHOUT_M2M,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.MEDIUM,
        deterministic=True,
        trust_impact="Accepted binding valid; M2M compatibility layer out of sync.",
        explanation="Accepted binding exists without Task.ifc_entities M2M row.",
        recommended_action=RecommendedAction.REPAIR_M2M_PARITY,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.M2M_WITHOUT_ACCEPTED: StatusDefinition(
        status=ReconciliationStatus.M2M_WITHOUT_ACCEPTED,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.MEDIUM,
        deterministic=True,
        trust_impact="M2M alone is not trusted truth.",
        explanation="Legacy M2M relation without accepted binding for this pair.",
        recommended_action=RecommendedAction.RECREATE_BINDING,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.REVIEW_WITH_M2M: StatusDefinition(
        status=ReconciliationStatus.REVIEW_WITH_M2M,
        category=ReconciliationCategory.REVIEW_REQUIRED,
        severity=ReconciliationSeverity.LOW,
        deterministic=True,
        trust_impact="Review binding should not imply M2M trust.",
        explanation="Review suggestion has M2M compatibility row.",
        recommended_action=RecommendedAction.RE_REVIEW,
        e2e_eligible=True,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.VERSION_UNKNOWN: StatusDefinition(
        status=ReconciliationStatus.VERSION_UNKNOWN,
        category=ReconciliationCategory.UNKNOWN,
        severity=ReconciliationSeverity.LOW,
        deterministic=False,
        trust_impact="Cannot determine import lineage; trust unchanged.",
        explanation="Import/version lineage unavailable for staleness determination.",
        recommended_action=RecommendedAction.INSUFFICIENT_EVIDENCE,
        e2e_eligible=False,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE: StatusDefinition(
        status=ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE,
        category=ReconciliationCategory.UNKNOWN,
        severity=ReconciliationSeverity.LOW,
        deterministic=False,
        trust_impact="Heuristic/semantic evidence cannot be reproduced.",
        explanation="Original matching evidence is not deterministically available.",
        recommended_action=RecommendedAction.INSUFFICIENT_EVIDENCE,
        e2e_eligible=False,
        auto_action_prohibited=True,
    ),
    ReconciliationStatus.CANNOT_DETERMINE: StatusDefinition(
        status=ReconciliationStatus.CANNOT_DETERMINE,
        category=ReconciliationCategory.UNKNOWN,
        severity=ReconciliationSeverity.LOW,
        deterministic=False,
        trust_impact="Insufficient data for classification.",
        explanation="Missing dates or context prevents deterministic multi-task classification.",
        recommended_action=RecommendedAction.INSUFFICIENT_EVIDENCE,
        e2e_eligible=False,
        auto_action_prohibited=True,
    ),
}


SEVERITY_RANK = {
    ReconciliationSeverity.CRITICAL: 0,
    ReconciliationSeverity.HIGH: 1,
    ReconciliationSeverity.MEDIUM: 2,
    ReconciliationSeverity.LOW: 3,
    ReconciliationSeverity.INFO: 4,
}


def status_definition(status: ReconciliationStatus) -> StatusDefinition:
    """Return metadata for a reconciliation status."""
    return STATUS_DEFINITIONS[status]


def primary_status(statuses: list[ReconciliationStatus]) -> ReconciliationStatus:
    """Pick the highest-severity status from a list."""
    if not statuses:
        return ReconciliationStatus.VALID
    return min(
        statuses,
        key=lambda s: (
            SEVERITY_RANK[STATUS_DEFINITIONS[s].severity],
            s.value,
        ),
    )
