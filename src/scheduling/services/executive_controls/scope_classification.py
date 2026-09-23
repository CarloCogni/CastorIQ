# scheduling/services/executive_controls/scope_classification.py
"""Read-only scope classification — no persistence, no keyword-as-truth."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from scheduling.services.executive_controls.contracts import ScopeClassificationResult
from scheduling.services.executive_controls.enums import MetricAuthority, ScopeClassification

if TYPE_CHECKING:
    from scheduling.models import Task

logger = logging.getLogger(__name__)

# Authoritative P6 activity_type → scope (narrow deterministic meaning only).
AUTHORITATIVE_ACTIVITY_TYPE_MAP: dict[str, ScopeClassification] = {
    "start milestone": ScopeClassification.MILESTONE,
    "finish milestone": ScopeClassification.MILESTONE,
    "milestone": ScopeClassification.MILESTONE,
    "wbs summary": ScopeClassification.MANAGEMENT_ADMINISTRATIVE,
    "level of effort": ScopeClassification.MANAGEMENT_ADMINISTRATIVE,
}

# Explicit activity_type tokens treated as authoritative procurement/engineering when present.
AUTHORITATIVE_TYPE_TOKENS: dict[str, ScopeClassification] = {
    "procurement": ScopeClassification.PROCUREMENT,
    "purchase": ScopeClassification.PROCUREMENT,
    "submittal": ScopeClassification.APPROVALS_AUTHORITY,
    "approval": ScopeClassification.APPROVALS_AUTHORITY,
    "engineering": ScopeClassification.ENGINEERING_DESIGN,
    "design": ScopeClassification.ENGINEERING_DESIGN,
    "commissioning": ScopeClassification.TESTING_COMMISSIONING,
    "testing": ScopeClassification.TESTING_COMMISSIONING,
    "handover": ScopeClassification.HANDOVER_SNAGGING,
    "snag": ScopeClassification.HANDOVER_SNAGGING,
    "construction": ScopeClassification.PHYSICAL_CONSTRUCTION,
}

# Suggestion-only name keywords — never authoritative.
SUGGESTION_NAME_PATTERNS: list[tuple[re.Pattern[str], ScopeClassification]] = [
    (
        re.compile(r"\b(submittal|shop drawing|sample)\b", re.I),
        ScopeClassification.APPROVALS_AUTHORITY,
    ),
    (re.compile(r"\b(procure|purchase order|po\b)", re.I), ScopeClassification.PROCUREMENT),
    (re.compile(r"\b(design|engineering|drawing)\b", re.I), ScopeClassification.ENGINEERING_DESIGN),
    (re.compile(r"\b(commission|testing|test)\b", re.I), ScopeClassification.TESTING_COMMISSIONING),
    (re.compile(r"\b(handover|snag|punch)\b", re.I), ScopeClassification.HANDOVER_SNAGGING),
    (
        re.compile(r"\b(pour|install|erect|fix|frame|clad)\b", re.I),
        ScopeClassification.PHYSICAL_CONSTRUCTION,
    ),
]


class ScopeClassificationResolver:
    """Resolve scope classification without persisting inferred values."""

    def resolve(
        self,
        task: Task,
        *,
        trusted_model_linked: bool = False,
    ) -> ScopeClassificationResult:
        """Return classification with explicit authority level."""
        activity_type = (task.activity_type or "").strip()
        activity_code = (task.activity_code or "").strip()
        name = task.name or ""

        # Priority 1: explicit P6 activity_type (authoritative narrow meaning).
        if activity_type:
            normalized = activity_type.lower()
            if normalized in AUTHORITATIVE_ACTIVITY_TYPE_MAP:
                classification = AUTHORITATIVE_ACTIVITY_TYPE_MAP[normalized]
                return self._authoritative(
                    classification,
                    field="activity_type",
                    value=activity_type,
                    explanation=f"P6 activity type '{activity_type}' maps to {classification.value}.",
                    trusted_model_linked=trusted_model_linked,
                    task=task,
                )
            for token, scope in AUTHORITATIVE_TYPE_TOKENS.items():
                if token in normalized:
                    return self._authoritative(
                        scope,
                        field="activity_type",
                        value=activity_type,
                        explanation=f"P6 activity type contains authoritative token '{token}'.",
                        trusted_model_linked=trusted_model_linked,
                        task=task,
                    )

        # Priority 2: governed mapping — no persistent table in E8-A; activity_code reserved for future.
        # If activity_code matches explicit PREFIX:PHYSICAL etc. pattern (project convention).
        if activity_code and ":" in activity_code:
            prefix, _, rest = activity_code.partition(":")
            scope_from_code = _explicit_code_scope(prefix)
            if scope_from_code is not None:
                return self._authoritative(
                    scope_from_code,
                    field="activity_code",
                    value=activity_code,
                    explanation=f"Explicit activity code prefix '{prefix}' governs scope.",
                    trusted_model_linked=trusted_model_linked,
                    task=task,
                )

        # Priority 3: deterministic milestone by zero-duration heuristic not used — activity_type only.

        # Priority 4: suggestion-only keyword inference from name.
        suggestions: list[str] = []
        for pattern, scope in SUGGESTION_NAME_PATTERNS:
            if pattern.search(name):
                suggestions.append(scope.value)
                return ScopeClassificationResult(
                    classification=scope.value,
                    authoritative=False,
                    authority_level=MetricAuthority.SUGGESTION.value,
                    source_field="name",
                    source_value=name[:120],
                    confidence=0.4,
                    explanation=(
                        "Keyword match on task name — suggestion only, not authoritative scope."
                    ),
                    evidence=[f"name~{scope.value}"],
                    alternatives=suggestions,
                    requires_mapping=True,
                    caveats=[
                        "Suggestion excluded from authoritative scope denominators.",
                        "Approve governed mapping in a future package for authoritative use.",
                    ],
                    trusted_model_linked=trusted_model_linked,
                    is_non_physical=bool(task.is_non_physical),
                )

        # Binding presence is separate — does not imply physical construction.
        caveats = [
            "Insufficient evidence for authoritative scope classification.",
            "Trusted model linkage indicates 4D eligibility, not physical scope class.",
        ]
        if task.is_non_physical:
            caveats.append(
                "is_non_physical=True means model-link excluded — not a scope classification."
            )

        return ScopeClassificationResult(
            classification=ScopeClassification.UNKNOWN.value,
            authoritative=False,
            authority_level=MetricAuthority.UNAVAILABLE.value,
            source_field=None,
            source_value=None,
            confidence=None,
            explanation="No authoritative P6 code or governed mapping — classified as unknown.",
            evidence=[],
            alternatives=suggestions,
            requires_mapping=True,
            caveats=caveats,
            trusted_model_linked=trusted_model_linked,
            is_non_physical=bool(task.is_non_physical),
        )

    def _authoritative(
        self,
        classification: ScopeClassification,
        *,
        field: str,
        value: str,
        explanation: str,
        trusted_model_linked: bool,
        task: Task,
    ) -> ScopeClassificationResult:
        return ScopeClassificationResult(
            classification=classification.value,
            authoritative=True,
            authority_level=MetricAuthority.AUTHORITATIVE.value,
            source_field=field,
            source_value=value,
            confidence=None,
            explanation=explanation,
            evidence=[f"{field}={value}"],
            alternatives=[],
            requires_mapping=False,
            caveats=[
                "Authoritative only for narrow P6 activity_type or explicit code meaning.",
                "Binding presence does not override this classification.",
            ],
            trusted_model_linked=trusted_model_linked,
            is_non_physical=bool(task.is_non_physical),
        )


def _explicit_code_scope(prefix: str) -> ScopeClassification | None:
    """Map explicit governed code prefixes when projects use SCOPE:CODE convention."""
    mapping = {
        "PHYS": ScopeClassification.PHYSICAL_CONSTRUCTION,
        "PROC": ScopeClassification.PROCUREMENT,
        "ENG": ScopeClassification.ENGINEERING_DESIGN,
        "APP": ScopeClassification.APPROVALS_AUTHORITY,
        "TC": ScopeClassification.TESTING_COMMISSIONING,
        "HO": ScopeClassification.HANDOVER_SNAGGING,
        "ADM": ScopeClassification.MANAGEMENT_ADMINISTRATIVE,
        "MS": ScopeClassification.MILESTONE,
    }
    return mapping.get(prefix.upper())
