# scheduling/services/governance/authority.py
"""Central governance authority policy (E2-F) — delegates to ProjectAccessService."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from environments.models import ProjectMembership
from environments.services.access_service import ProjectAccessService

logger = logging.getLogger(__name__)

GOVERNANCE_AUTHORITY_POLICY_ID = "governance-authority-v1"


class GovernanceCapability(StrEnum):
    """Fine-grained governance operations mapped to project membership tiers."""

    VIEW_GOVERNANCE = "can_view_governance"
    VIEW_AUDIT = "can_view_audit"
    PROPOSE = "can_propose"
    APPROVE_INDIVIDUAL = "can_approve_individual"
    APPROVE_BULK = "can_approve_bulk"
    APPROVE_EXACT_PREVIEW = "can_approve_exact_preview"
    REJECT = "can_reject"
    REAFFIRM = "can_reaffirm"
    REVERSE = "can_reverse"
    SUPERSEDE = "can_supersede"
    REPAIR_M2M_ADD = "can_repair_m2m_add"
    REPAIR_M2M_REMOVE = "can_repair_m2m_remove"
    MANAGE_POLICY = "can_manage_governance_policy"
    EXPORT = "can_export_governance"


# Minimum project permission tier per capability (no role-name strings).
_EDITOR = ProjectMembership.Permission.EDITOR
_OWNER = ProjectMembership.Permission.OWNER
_VIEWER = ProjectMembership.Permission.VIEWER

_CAPABILITY_MIN_PERMISSION: dict[GovernanceCapability, str] = {
    GovernanceCapability.VIEW_GOVERNANCE: _VIEWER,
    GovernanceCapability.VIEW_AUDIT: _VIEWER,
    GovernanceCapability.PROPOSE: _EDITOR,
    GovernanceCapability.APPROVE_INDIVIDUAL: _EDITOR,
    GovernanceCapability.APPROVE_BULK: _OWNER,
    GovernanceCapability.APPROVE_EXACT_PREVIEW: _OWNER,
    GovernanceCapability.REJECT: _EDITOR,
    GovernanceCapability.REAFFIRM: _EDITOR,
    GovernanceCapability.REVERSE: _OWNER,
    GovernanceCapability.SUPERSEDE: _OWNER,
    GovernanceCapability.REPAIR_M2M_ADD: _EDITOR,
    GovernanceCapability.REPAIR_M2M_REMOVE: _OWNER,
    GovernanceCapability.MANAGE_POLICY: _OWNER,
    GovernanceCapability.EXPORT: _VIEWER,
}

_RANK = {
    ProjectMembership.Permission.VIEWER: 1,
    ProjectMembership.Permission.EDITOR: 2,
    ProjectMembership.Permission.OWNER: 3,
}


@dataclass
class AuthorityResult:
    """Outcome of a single capability check."""

    allowed: bool
    capability: str
    authority_source: str
    permission: str | None
    reason: str
    scope: str = "project"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GovernanceAuthorityError(Exception):
    """Raised when a governance operation lacks required authority."""

    def __init__(self, result: AuthorityResult) -> None:
        self.result = result
        super().__init__(result.reason)


class GovernanceAuthorityPolicy:
    """Project-scoped governance capability checks — single decision path."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user
        self._permission = ProjectAccessService.user_permission(user, project)

    def check(self, capability: GovernanceCapability | str) -> AuthorityResult:
        """Return whether *user* may perform *capability* on *project*."""
        cap = GovernanceCapability(capability)
        if self.user and getattr(self.user, "is_superuser", False):
            return AuthorityResult(
                allowed=True,
                capability=cap.value,
                authority_source="superuser_override",
                permission=self._permission,
                reason="Superuser override — auditable elevated access.",
                warnings=["Superuser actions should be rare and traceable."],
            )

        required = _CAPABILITY_MIN_PERMISSION[cap]
        if self._permission is None:
            return AuthorityResult(
                allowed=False,
                capability=cap.value,
                authority_source="none",
                permission=None,
                reason="No project membership.",
            )

        allowed = _RANK[self._permission] >= _RANK[required]
        source = "project_membership"
        if allowed:
            reason = f"Permitted via {self._permission} membership (requires {required})."
        else:
            reason = (
                f"Requires {required} permission on this project; "
                f"current membership is {self._permission}."
            )
        return AuthorityResult(
            allowed=allowed,
            capability=cap.value,
            authority_source=source,
            permission=self._permission,
            reason=reason,
        )

    def require(self, capability: GovernanceCapability | str) -> AuthorityResult:
        """Raise :class:`GovernanceAuthorityError` when capability is denied."""
        result = self.check(capability)
        if not result.allowed:
            logger.warning(
                "governance authority denied user=%s project=%s cap=%s",
                getattr(self.user, "pk", None),
                self.project.pk,
                result.capability,
            )
            raise GovernanceAuthorityError(result)
        return result

    def capabilities_summary(self) -> dict[str, Any]:
        """All capabilities for the current user — used by overview UI."""
        caps = {cap.value: self.check(cap).allowed for cap in GovernanceCapability}
        return {
            "policy_id": GOVERNANCE_AUTHORITY_POLICY_ID,
            "permission": self._permission,
            "capabilities": caps,
            "destructive_available": caps[GovernanceCapability.REVERSE]
            or caps[GovernanceCapability.SUPERSEDE]
            or caps[GovernanceCapability.REPAIR_M2M_REMOVE],
        }


def require_parity_repair_authority(
    policy: GovernanceAuthorityPolicy,
    repair_type: str,
) -> AuthorityResult:
    """Map parity repair type to add vs remove capability."""
    if repair_type == "accepted_missing_m2m":
        return policy.require(GovernanceCapability.REPAIR_M2M_ADD)
    if repair_type in ("m2m_without_accepted", "review_m2m_leak"):
        return policy.require(GovernanceCapability.REPAIR_M2M_REMOVE)
    raise GovernanceAuthorityError(
        AuthorityResult(
            allowed=False,
            capability=GovernanceCapability.REPAIR_M2M_ADD,
            authority_source="none",
            permission=policy._permission,
            reason=f"Unknown parity repair type: {repair_type}",
        )
    )
