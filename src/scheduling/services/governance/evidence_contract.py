# scheduling/services/governance/evidence_contract.py
"""Reusable evidence contract for review queue and detail panels (E2-B)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.vocabulary import EvidenceLabel, GovernanceCategory


@dataclass(frozen=True)
class BindingEvidenceView:
    """Structured evidence payload for one binding or hint row."""

    evidence_label: str
    deterministic: bool
    confidence: float | None
    source_field: str
    source_value: str | None
    normalized_value: str | None
    comparison_target: str | None
    matched: bool | None
    explanation: str
    trust_implication: str
    review_required: bool
    warnings: tuple[str, ...] = ()
    policy_id: str = TRUSTED_BINDING_POLICY_ID

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_binding_evidence(
    *,
    link_method: str,
    needs_review: bool,
    confidence: float,
    activity_code: str | None = None,
    entity_global_id: str | None = None,
) -> BindingEvidenceView:
    """Build evidence contract from persisted binding fields."""
    label = evidence_label_for_binding(link_method, needs_review=needs_review)
    deterministic = label in {
        EvidenceLabel.EXACT_IDENTIFIER,
        EvidenceLabel.NORMALIZED_IDENTIFIER,
        EvidenceLabel.MANUAL_SELECTION,
    }
    review_required = needs_review or label in {
        EvidenceLabel.NORMALIZED_IDENTIFIER,
        EvidenceLabel.HEURISTIC,
        EvidenceLabel.SEMANTIC,
    }
    if label == EvidenceLabel.EXACT_IDENTIFIER:
        explanation = "Exact literal identifier equality between task and entity."
        trust = "Eligible for trusted state when needs_review=False."
    elif label == EvidenceLabel.NORMALIZED_IDENTIFIER:
        explanation = "Normalized identifier match; distinct from exact literal equality."
        trust = "Review-only; confidence does not override needs_review."
    elif label == EvidenceLabel.MANUAL_SELECTION:
        explanation = "User explicitly selected this task–entity pair."
        trust = "Trusted when needs_review=False."
    elif label == EvidenceLabel.HEURISTIC:
        explanation = "Type or parameter heuristic suggestion."
        trust = "Review-only until explicitly accepted."
    elif label == EvidenceLabel.SEMANTIC:
        explanation = "Embedding or semantic similarity suggestion."
        trust = "Review-only until explicitly accepted."
    else:
        explanation = "Evidence type could not be classified from stored method."
        trust = "Requires needs_review=False for trusted reads."

    warnings: list[str] = []
    if confidence >= 0.95 and review_required:
        warnings.append("High confidence does not override review state.")
    if label == EvidenceLabel.NORMALIZED_IDENTIFIER:
        warnings.append("Normalized equality is not exact literal equality.")

    return BindingEvidenceView(
        evidence_label=label.value,
        deterministic=deterministic,
        confidence=confidence,
        source_field="link_method",
        source_value=link_method,
        normalized_value=activity_code if label == EvidenceLabel.NORMALIZED_IDENTIFIER else None,
        comparison_target=entity_global_id,
        matched=True if not review_required else None,
        explanation=explanation,
        trust_implication=trust,
        review_required=review_required,
        warnings=tuple(warnings),
    )


def build_property_hint_evidence(
    *,
    activity_id_value: str,
    has_trusted_binding: bool,
    has_review_binding: bool,
) -> BindingEvidenceView:
    """Evidence contract for IFC Activity ID property hints."""
    warnings: list[str] = []
    if has_trusted_binding:
        warnings.append("Accepted binding exists; property is supplementary.")
    if has_review_binding:
        warnings.append("Review binding exists separately from property hint.")

    return BindingEvidenceView(
        evidence_label=EvidenceLabel.IFC_PROPERTY_HINT.value,
        deterministic=False,
        confidence=None,
        source_field="properties",
        source_value=activity_id_value,
        normalized_value=None,
        comparison_target=None,
        matched=None,
        explanation="IFC Activity ID property metadata; not a binding row.",
        trust_implication="Never trusted without accepted binding.",
        review_required=not has_trusted_binding,
        warnings=tuple(warnings),
    )


def build_legacy_m2m_evidence() -> BindingEvidenceView:
    """Evidence contract for legacy M2M-only compatibility rows."""
    return BindingEvidenceView(
        evidence_label=EvidenceLabel.LEGACY_COMPATIBILITY.value,
        deterministic=True,
        confidence=None,
        source_field="task.ifc_entities",
        source_value="m2m",
        normalized_value=None,
        comparison_target=None,
        matched=False,
        explanation="Legacy M2M relation without matching accepted binding.",
        trust_implication="Compatibility storage only; not primary truth.",
        review_required=True,
        warnings=("Absence of accepted binding cannot be replaced by M2M.",),
    )


def category_for_binding(*, needs_review: bool) -> GovernanceCategory:
    """Map binding row to governance category."""
    return GovernanceCategory.REVIEW if needs_review else GovernanceCategory.TRUSTED
