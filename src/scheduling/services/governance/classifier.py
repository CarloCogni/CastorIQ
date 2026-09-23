# scheduling/services/governance/classifier.py
"""Pure governance state classification over existing binding data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.vocabulary import EvidenceLabel, GovernanceCategory


@dataclass(frozen=True)
class GovernanceStateResult:
    """Classification output for a task or entity governance context."""

    primary: GovernanceCategory
    trusted: bool
    explanation: str
    secondary: tuple[GovernanceCategory, ...] = ()
    source_facts: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class GovernanceStateClassifier:
    """Classify link governance state without persisting anything."""

    @staticmethod
    def classify_task(
        *,
        trusted_count: int,
        review_count: int,
        has_property_hint: bool = False,
        legacy_m2m_count: int = 0,
    ) -> GovernanceStateResult:
        """Classify a task's overall link governance state."""
        facts = {
            "trusted_count": trusted_count,
            "review_count": review_count,
            "legacy_m2m_count": legacy_m2m_count,
            "has_property_hint": has_property_hint,
        }
        secondary: list[GovernanceCategory] = []
        if trusted_count > 0:
            if review_count > 0:
                secondary.append(GovernanceCategory.REVIEW)
            if trusted_count > 1:
                return GovernanceStateResult(
                    primary=GovernanceCategory.MULTIPLE_TRUSTED,
                    trusted=True,
                    explanation=(
                        f"{trusted_count} accepted bindings; review pending: {review_count > 0}."
                    ),
                    secondary=tuple(secondary),
                    source_facts=facts,
                )
            return GovernanceStateResult(
                primary=GovernanceCategory.TRUSTED,
                trusted=True,
                explanation="At least one accepted binding.",
                secondary=tuple(secondary),
                source_facts=facts,
            )
        if review_count > 0:
            return GovernanceStateResult(
                primary=GovernanceCategory.REVIEW,
                trusted=False,
                explanation="Review-only bindings; not trusted.",
                source_facts=facts,
            )
        if legacy_m2m_count > 0:
            return GovernanceStateResult(
                primary=GovernanceCategory.LEGACY_COMPATIBILITY,
                trusted=False,
                explanation="Legacy M2M relation without accepted binding.",
                source_facts=facts,
                warnings=("M2M compatibility storage is not trusted link truth.",),
            )
        if has_property_hint:
            return GovernanceStateResult(
                primary=GovernanceCategory.PROPERTY_HINT,
                trusted=False,
                explanation="Property metadata present without accepted binding.",
                source_facts=facts,
            )
        return GovernanceStateResult(
            primary=GovernanceCategory.UNLINKED,
            trusted=False,
            explanation="No accepted binding, review binding, or legacy M2M.",
            source_facts=facts,
        )

    @staticmethod
    def classify_entity(
        *,
        trusted_task_ids: list[str],
        review_task_ids: list[str],
        has_property_hint: bool = False,
        task_date_ranges: dict[str, tuple[date | None, date | None]] | None = None,
    ) -> GovernanceStateResult:
        """Classify an entity's governance state from related tasks."""
        facts = {
            "trusted_task_count": len(trusted_task_ids),
            "review_task_count": len(review_task_ids),
            "has_property_hint": has_property_hint,
        }
        secondary: list[GovernanceCategory] = []
        if review_task_ids:
            secondary.append(GovernanceCategory.REVIEW)

        if len(trusted_task_ids) > 1:
            if task_date_ranges and _has_overlapping_accepted_tasks(
                trusted_task_ids, task_date_ranges
            ):
                return GovernanceStateResult(
                    primary=GovernanceCategory.POSSIBLE_CONFLICT,
                    trusted=True,
                    explanation=(
                        "Multiple accepted tasks on one entity with overlapping date ranges."
                    ),
                    secondary=tuple(secondary),
                    source_facts=facts,
                    warnings=("Explicit overlap evidence; not classified without overlap.",),
                )
            return GovernanceStateResult(
                primary=GovernanceCategory.MULTIPLE_TRUSTED,
                trusted=True,
                explanation="Multiple accepted tasks mapped to one entity.",
                secondary=tuple(secondary),
                source_facts=facts,
            )

        if len(trusted_task_ids) == 1:
            return GovernanceStateResult(
                primary=GovernanceCategory.TRUSTED,
                trusted=True,
                explanation="One accepted task mapping.",
                secondary=tuple(secondary),
                source_facts=facts,
            )

        if review_task_ids:
            return GovernanceStateResult(
                primary=GovernanceCategory.REVIEW,
                trusted=False,
                explanation="Review suggestions only.",
                source_facts=facts,
            )

        if has_property_hint:
            return GovernanceStateResult(
                primary=GovernanceCategory.PROPERTY_HINT,
                trusted=False,
                explanation="Activity ID property hint without accepted binding.",
                source_facts=facts,
            )

        return GovernanceStateResult(
            primary=GovernanceCategory.UNLINKED,
            trusted=False,
            explanation="No accepted or review bindings.",
            source_facts=facts,
        )

    @staticmethod
    def evidence_trust_label(link_method: str, *, needs_review: bool) -> EvidenceLabel:
        """Expose evidence label helper for queue contracts (E2-B)."""
        return evidence_label_for_binding(link_method, needs_review=needs_review)


def _has_overlapping_accepted_tasks(
    task_ids: list[str],
    ranges: dict[str, tuple[date | None, date | None]],
) -> bool:
    """True when any pair of tasks has overlapping planned dates."""
    dated = []
    for tid in task_ids:
        start, end = ranges.get(tid, (None, None))
        if start and end:
            dated.append((start, end))
    for i, (s1, e1) in enumerate(dated):
        for s2, e2 in dated[i + 1 :]:
            if s1 <= e2 and s2 <= e1:
                return True
    return False
