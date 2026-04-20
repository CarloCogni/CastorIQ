# facilities/tests/test_role_service.py
"""Tests for facilities.services.role_service.ProjectRoleService."""

from datetime import timedelta

import pytest
from django.utils import timezone

from environments.tests.factories import ProjectFactory, ProjectRoleFactory, UserFactory
from facilities.services.role_service import SESSION_KEY, ProjectRoleService


@pytest.mark.django_db
class TestActiveRoles:
    """ProjectRoleService.active_roles returns only valid roles for the scoped user+project."""

    def test_returns_empty_list_when_user_has_no_roles(self):
        """No roles → empty list (not None)."""
        user = UserFactory()
        project = ProjectFactory()
        service = ProjectRoleService(project=project, user=user)
        assert service.active_roles() == []

    def test_returns_all_concurrent_roles_for_the_user(self):
        """Multi-role users see every live role they hold."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        ProjectRoleFactory(user=user, project=project, role="auditor")

        roles = ProjectRoleService(project, user).active_roles()
        assert {r.role for r in roles} == {"facilitiesmanager", "auditor"}

    def test_excludes_expired_roles(self):
        """Roles past their valid_until are not returned."""
        user = UserFactory()
        project = ProjectFactory()
        now = timezone.now()
        ProjectRoleFactory(
            user=user,
            project=project,
            role="contractor",
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(days=1),
        )
        assert ProjectRoleService(project, user).active_roles() == []


@pytest.mark.django_db
class TestResolveActive:
    """ProjectRoleService.resolve_active picks the right role given session state."""

    def test_returns_none_when_user_has_no_active_roles(self):
        """No roles → None active."""
        user = UserFactory()
        project = ProjectFactory()
        session = {}
        assert ProjectRoleService(project, user).resolve_active(session) is None

    def test_returns_session_selected_role_when_valid(self):
        """If the session points at a role the user still holds, honor it."""
        user = UserFactory()
        project = ProjectFactory()
        first = ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        second = ProjectRoleFactory(user=user, project=project, role="auditor")
        session = {SESSION_KEY: str(second.id)}

        resolved = ProjectRoleService(project, user).resolve_active(session)
        assert resolved == second
        assert resolved != first

    def test_falls_back_to_first_when_session_selection_is_stale(self):
        """A session key pointing at a deleted/expired role falls back to the first live one."""
        user = UserFactory()
        project = ProjectFactory()
        live = ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        session = {SESSION_KEY: "00000000-0000-0000-0000-000000000000"}

        resolved = ProjectRoleService(project, user).resolve_active(session)
        assert resolved == live
        assert SESSION_KEY not in session

    def test_falls_back_to_first_when_session_is_empty(self):
        """Empty session → first active role."""
        user = UserFactory()
        project = ProjectFactory()
        first = ProjectRoleFactory(user=user, project=project, role="auditor")
        ProjectRoleFactory(user=user, project=project, role="tenant")
        session: dict = {}

        resolved = ProjectRoleService(project, user).resolve_active(session)
        assert resolved == first


@pytest.mark.django_db
class TestSetActive:
    """ProjectRoleService.set_active writes the session after validating the target role."""

    def test_missing_role_id_returns_error(self):
        """No role_id → error, session untouched."""
        user = UserFactory()
        project = ProjectFactory()
        session: dict = {}

        result = ProjectRoleService(project, user).set_active(session, None)
        assert result["error"] == "role_id required"
        assert SESSION_KEY not in session

    def test_foreign_role_id_returns_error(self):
        """Attempting to activate someone else's role is rejected."""
        user = UserFactory()
        other = UserFactory()
        project = ProjectFactory()
        foreign = ProjectRoleFactory(user=other, project=project, role="contractor")
        session: dict = {}

        result = ProjectRoleService(project, user).set_active(session, str(foreign.id))
        assert result["role"] is None
        assert "not found" in result["error"]
        assert SESSION_KEY not in session

    def test_valid_role_is_persisted_in_session(self):
        """Happy path — role_id is stored in the session under SESSION_KEY."""
        user = UserFactory()
        project = ProjectFactory()
        role = ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        session: dict = {}

        result = ProjectRoleService(project, user).set_active(session, str(role.id))
        assert result["error"] is None
        assert result["role"] == role
        assert session[SESSION_KEY] == str(role.id)

    def test_expired_role_cannot_be_activated(self):
        """A role outside its validity window cannot be made active."""
        user = UserFactory()
        project = ProjectFactory()
        now = timezone.now()
        role = ProjectRoleFactory(
            user=user,
            project=project,
            role="auditor",
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(seconds=1),
        )
        session: dict = {}

        result = ProjectRoleService(project, user).set_active(session, str(role.id))
        assert result["role"] is None
        assert "outside" in result["error"]
