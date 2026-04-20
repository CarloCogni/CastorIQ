# facilities/services/role_service.py
"""Resolve, list, and switch a user's active ProjectRole for a given project."""

import logging

from django.utils import timezone

from environments.models import ProjectRole

logger = logging.getLogger(__name__)

SESSION_KEY = "facilities_active_role_id"


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
