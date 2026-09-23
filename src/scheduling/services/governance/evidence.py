# scheduling/services/governance/evidence.py
"""Map persisted binding fields to governance evidence labels."""

from __future__ import annotations

from scheduling.models import TaskEntityBinding
from scheduling.services.governance.vocabulary import EvidenceLabel


def evidence_label_for_binding(
    link_method: str,
    *,
    needs_review: bool,
) -> EvidenceLabel:
    """Return the governance evidence label for a binding row.

    Normalized, heuristic, and embedding matches remain review-only for trust even
    when confidence is high. Exact literal equality is distinct from normalized.
    """
    method = (link_method or "").lower()
    if method == TaskEntityBinding.LinkMethod.EXACT:
        return EvidenceLabel.EXACT_IDENTIFIER
    if method == TaskEntityBinding.LinkMethod.NORMALIZED:
        return EvidenceLabel.NORMALIZED_IDENTIFIER
    if method == TaskEntityBinding.LinkMethod.MANUAL:
        return EvidenceLabel.MANUAL_SELECTION
    if method == TaskEntityBinding.LinkMethod.HEURISTIC:
        return EvidenceLabel.HEURISTIC
    if method == TaskEntityBinding.LinkMethod.EMBEDDING:
        return EvidenceLabel.SEMANTIC
    return EvidenceLabel.UNKNOWN


def is_trusted_evidence_label(label: EvidenceLabel) -> bool:
    """True when the evidence type may correspond to an accepted binding."""
    return label in {
        EvidenceLabel.EXACT_IDENTIFIER,
        EvidenceLabel.MANUAL_SELECTION,
    }


def trust_requires_accepted_row(label: EvidenceLabel) -> bool:
    """True when needs_review=False is required regardless of evidence label."""
    if label in {
        EvidenceLabel.NORMALIZED_IDENTIFIER,
        EvidenceLabel.HEURISTIC,
        EvidenceLabel.SEMANTIC,
    }:
        return True
    return True
