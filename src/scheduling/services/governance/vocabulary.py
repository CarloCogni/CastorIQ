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
    IFC_PROPERTY_HINT = "ifc_property_hint"
    TYPE_COMPATIBILITY = "type_compatibility"
    LOCATION_COMPATIBILITY = "location_compatibility"
    WBS_COMPATIBILITY = "wbs_compatibility"
    DISCIPLINE_COMPATIBILITY = "discipline_compatibility"
    SEQUENCE_COMPATIBILITY = "sequence_compatibility"
    QUANTITY_PLAUSIBILITY = "quantity_plausibility"
    HEURISTIC = "heuristic"
    SEMANTIC = "semantic"
    UNKNOWN = "unknown"


class QueueMode(StrEnum):
    """Review queue filter modes (E2-B)."""

    REVIEW = "review"
    TRUSTED = "trusted"
    PROPERTY_HINTS = "property_hints"
    LEGACY_ONLY = "legacy_only"
    MULTIPLE_TRUSTED = "multiple_trusted"
    POSSIBLE_CONFLICTS = "possible_conflicts"
    ALL_GOVERNANCE = "all_governance"
