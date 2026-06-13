# scheduling/services/governance/vocabulary.py
"""Logical governance categories for schedule-to-model link state (not persisted)."""

from __future__ import annotations

from enum import StrEnum


class GovernanceCategory(StrEnum):
    """Primary governance classification for a task or entity link context."""

    TRUSTED = "trusted"
    REVIEW = "review"
    PROPERTY_HINT = "property_hint"
    LEGACY_COMPATIBILITY = "legacy_compatibility"
    UNLINKED = "unlinked"
    MULTIPLE_TRUSTED = "multiple_trusted"
    POSSIBLE_CONFLICT = "possible_conflict"
    STALE_CANDIDATE = "stale_candidate"
    INVALID_SCOPE = "invalid_scope"


class EvidenceLabel(StrEnum):
    """Trust evidence labels — distinct from persisted link_method values."""

    EXACT_IDENTIFIER = "exact_identifier_equality"
    NORMALIZED_IDENTIFIER = "normalized_identifier_equality"
    MANUAL_SELECTION = "manual_explicit_selection"
    LEGACY_COMPATIBILITY = "legacy_compatibility"
    HEURISTIC = "heuristic"
    SEMANTIC = "semantic"
    UNKNOWN = "unknown"
