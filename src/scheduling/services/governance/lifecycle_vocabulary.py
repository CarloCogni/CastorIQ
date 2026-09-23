# scheduling/services/governance/lifecycle_vocabulary.py
"""E2-E governance lifecycle event types, reason codes, and confirmation phrases."""

from __future__ import annotations

from enum import StrEnum

TRUSTED_BINDING_POLICY_ID = "trusted-binding-v1"

REVERSE_CONFIRM_PHRASE = "REVERSE TRUSTED LINK"
SUPERSEDE_CONFIRM_PHRASE = "SUPERSEDE LINK"
PARITY_REPAIR_CONFIRM_PHRASE = "PARITY REPAIR"
BULK_PARITY_MAX = 500


class GovernanceEventType(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REAFFIRMED = "reaffirmed"
    REVERSED = "reversed"
    SUPERSEDED = "superseded"
    SUPERSEDING_ACCEPTANCE = "superseding_acceptance"
    M2M_ADDED = "m2m_added"
    M2M_REMOVED = "m2m_removed"
    PARITY_REPAIRED = "parity_repaired"
    CONFLICT_ACKNOWLEDGED = "conflict_acknowledged"
    MIGRATION_INITIALIZED = "migration_initialized"


class RejectReasonCode(StrEnum):
    WRONG_TASK = "wrong_task"
    WRONG_ENTITY = "wrong_entity"
    WRONG_LOCATION = "wrong_location"
    WRONG_DISCIPLINE = "wrong_discipline"
    WRONG_TYPE = "wrong_type"
    DUPLICATE = "duplicate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OBSOLETE_SUGGESTION = "obsolete_suggestion"
    OTHER = "other"


class ReverseReasonCode(StrEnum):
    MISTAKEN_APPROVAL = "mistaken_approval"
    SOURCE_CHANGED = "source_changed"
    TASK_REMOVED = "task_removed"
    ENTITY_REMOVED = "entity_removed"
    SUPERSEDED = "superseded"
    SCOPE_CHANGED = "scope_changed"
    GOVERNANCE_CORRECTION = "governance_correction"
    OTHER = "other"


class ReaffirmReasonCode(StrEnum):
    EVIDENCE_VERIFIED = "evidence_verified"
    MANUAL_OVERRIDE_CONFIRMED = "manual_override_confirmed"
    SOURCE_CHANGE_REVIEWED = "source_change_reviewed"
    RECONCILIATION_FALSE_POSITIVE = "reconciliation_false_positive"
    OTHER = "other"


class ParityReasonCode(StrEnum):
    ACCEPTED_MISSING_M2M = "accepted_missing_m2m"
    M2M_WITHOUT_ACCEPTED = "m2m_without_accepted"
    REVIEW_M2M_LEAK = "review_m2m_leak"
    DUPLICATE_COMPATIBILITY = "duplicate_compatibility"
    OTHER = "other"


REASON_REQUIRES_TEXT = frozenset({"other"})


def validate_reason(code: str, text: str) -> str | None:
    """Return error message if reason is invalid."""
    if not code:
        return "Reason code is required."
    if code in REASON_REQUIRES_TEXT and not (text or "").strip():
        return "Reason text is required when reason code is 'other'."
    return None
