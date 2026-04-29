# facilities/services/occupant_space_service.py
"""Resolve a TENANT/OCCUPANT user's assigned space on a project.

Companion to :class:`facilities.services.role_service.ProjectRoleService` —
single concern: pick the spatial container an occupant is seated in, so the
Occupant Portal can pre-fill location on intake.

A user may hold multiple concurrent TENANT/OCCUPANT rows (lease renewals,
short stays, dual-floor tenancy). Resolution is deterministic:

1. Active roles only (``valid_from`` past, ``valid_until`` future or null).
2. TENANT before OCCUPANT — long-lease over transient.
3. Most recent ``valid_from`` first inside each tier.
"""

from __future__ import annotations

import logging

from environments.models import ProjectRole

logger = logging.getLogger(__name__)


# TENANT outranks OCCUPANT — a long-lease seating wins over a transient pass.
_OCCUPANT_ROLE_PRIORITY: tuple[str, ...] = (
    ProjectRole.Role.TENANT,
    ProjectRole.Role.OCCUPANT,
)


class OccupantSpaceService:
    """Pick the active assigned space for an occupant on a project."""

    def __init__(self, project, user):
        self.project = project
        self.user = user

    def resolve_active_role(self) -> ProjectRole | None:
        """Return the active TENANT/OCCUPANT role for this user, or None."""
        if not self.user or not self.user.is_authenticated:
            return None

        active = list(
            ProjectRole.active_for(self.user, self.project)
            .filter(role__in=_OCCUPANT_ROLE_PRIORITY)
            .select_related("assigned_space", "assigned_space__entity")
            .order_by("-valid_from")
        )
        if not active:
            return None
        # TENANT first regardless of valid_from order — alphabetical sort would
        # put OCCUPANT before TENANT, which is the wrong precedence.
        active.sort(key=lambda r: _OCCUPANT_ROLE_PRIORITY.index(r.role))
        return active[0]

    def resolve(self):
        """Return the assigned :class:`IFCSpatialElement` or None.

        Returns ``None`` when (a) the user has no active TENANT/OCCUPANT role
        on this project, or (b) the active role has no ``assigned_space`` set.
        """
        role = self.resolve_active_role()
        if role is None:
            return None
        return role.assigned_space
