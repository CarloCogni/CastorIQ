# scheduling/services/governance/active_state.py
"""Central active-state filters for E2-E lifecycle (single source of truth)."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from scheduling.models import TaskEntityBinding


def trusted_filter() -> Q:
    """Bindings that count as active trusted truth for E2-A/E4 reads."""
    return Q(
        is_active=True,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        needs_review=False,
    )


def active_review_filter() -> Q:
    """Bindings shown in the active review queue."""
    return Q(
        is_active=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        needs_review=True,
    )


def apply_trusted(qs: QuerySet) -> QuerySet:
    return qs.filter(trusted_filter())


def apply_active_review(qs: QuerySet) -> QuerySet:
    return qs.filter(active_review_filter())


def is_trusted_binding(binding: TaskEntityBinding) -> bool:
    return (
        binding.is_active
        and binding.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
        and not binding.needs_review
    )


def is_active_review_binding(binding: TaskEntityBinding) -> bool:
    return (
        binding.is_active
        and binding.governance_status == TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW
        and binding.needs_review
    )


def promote_fields() -> dict:
    """Field updates when a review binding becomes trusted."""
    return {
        "needs_review": False,
        "governance_status": TaskEntityBinding.GovernanceStatus.TRUSTED,
        "is_active": True,
        "rejected_at": None,
        "reversed_at": None,
    }


def reject_fields() -> dict:
    return {
        "needs_review": True,
        "governance_status": TaskEntityBinding.GovernanceStatus.REJECTED,
        "is_active": False,
    }


def reverse_fields() -> dict:
    from django.utils import timezone

    return {
        "needs_review": False,
        "governance_status": TaskEntityBinding.GovernanceStatus.REVERSED,
        "is_active": False,
        "reversed_at": timezone.now(),
    }


def supersede_fields() -> dict:
    return {
        "needs_review": False,
        "governance_status": TaskEntityBinding.GovernanceStatus.SUPERSEDED,
        "is_active": False,
    }
