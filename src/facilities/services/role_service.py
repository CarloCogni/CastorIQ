# facilities/services/role_service.py
"""Resolve, list, switch, and mutate a user's ProjectRole assignments."""

from __future__ import annotations

import logging
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from environments.models import Project, ProjectRole

logger = logging.getLogger(__name__)

SESSION_KEY = "facilities_active_role_id"


class ProjectRoleError(Exception):
    """Base for role-service guard violations (duplicate, bad dates, etc.)."""


class ProjectRoleService:
    """
    Facade over :class:`environments.models.ProjectRole`.

    The service reads/writes a single session key (per logged-in user) so the
    Facilities tab can be rendered against a user-selected role. Session
    scoping is intentional: role is a short-lived *view preference*, not a
    permanent user attribute.
    """

    def __init__(self, project, user):
        self.project = project
        self.user = user

    def active_roles(self) -> list[ProjectRole]:
        """All currently valid roles for the user on this project, stable ordering."""
        return list(ProjectRole.active_for(self.user, self.project).order_by("role", "valid_from"))

    def resolve_active(self, session) -> ProjectRole | None:
        """
        Return the session-selected role if still valid, otherwise the first active role.

        Falls back to None when the user has no active role for the project.
        """
        roles = self.active_roles()
        if not roles:
            return None

        selected_id = session.get(SESSION_KEY)
        if selected_id:
            for role in roles:
                if str(role.id) == str(selected_id):
                    return role
            # stale selection — drop it so future reads don't keep missing
            session.pop(SESSION_KEY, None)

        return roles[0]

    def set_active(self, session, role_id) -> dict:
        """Persist the chosen role in the session after validating ownership + validity."""
        if not role_id:
            return {"role": None, "error": "role_id required"}

        try:
            role = ProjectRole.objects.get(pk=role_id, user=self.user, project=self.project)
        except ProjectRole.DoesNotExist:
            logger.warning(
                "User %s attempted to activate foreign role %s on project %s",
                self.user.pk,
                role_id,
                self.project.pk,
            )
            return {"role": None, "error": "role not found for user and project"}

        if not role.is_active_at(timezone.now()):
            return {"role": None, "error": "role is outside its validity window"}

        session[SESSION_KEY] = str(role.id)
        logger.info(
            "Active role set: user=%s project=%s role=%s",
            self.user.pk,
            self.project.pk,
            role.role,
        )
        return {"role": role, "error": None}

    # ---- Mutations ---------------------------------------------------------
    #
    # Class-level mutation helpers used by the People-page role CRUD endpoints.
    # Instance methods above are scoped to a single (project, user) view
    # context; these take their targets as explicit arguments.

    @classmethod
    def add_role(
        cls,
        *,
        project: Project,
        user,
        role: str,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> ProjectRole:
        """Create a ProjectRole row.

        Rejects valid_from >= valid_until (a zero-length window). Duplicate
        (user, project, role, valid_from) tuples surface as ProjectRoleError
        via the unique constraint at the DB layer.
        """
        if role not in ProjectRole.Role.values:
            raise ProjectRoleError(f"Unknown role: {role!r}")

        resolved_from = valid_from or timezone.now()
        if valid_until is not None and valid_until <= resolved_from:
            raise ProjectRoleError("valid_until must be after valid_from.")

        try:
            with transaction.atomic():
                instance = ProjectRole.objects.create(
                    project=project,
                    user=user,
                    role=role,
                    valid_from=resolved_from,
                    valid_until=valid_until,
                )
        except IntegrityError as exc:
            raise ProjectRoleError(
                "This user already has that role starting on the same date."
            ) from exc

        logger.info(
            "Added functional role: user=%s project=%s role=%s valid_from=%s valid_until=%s",
            user.pk,
            project.pk,
            role,
            resolved_from.isoformat(),
            valid_until.isoformat() if valid_until else None,
        )
        return instance

    @classmethod
    def remove_role(cls, *, project: Project, role_id) -> None:
        """Delete a ProjectRole row scoped to the given project.

        Scoping by project prevents cross-project role deletion via a forged
        role_id from a URL on a different project.
        """
        deleted, _ = ProjectRole.objects.filter(pk=role_id, project=project).delete()
        if deleted == 0:
            raise ProjectRoleError("Role not found on this project.")
        logger.info("Removed functional role: project=%s role_id=%s", project.pk, role_id)
