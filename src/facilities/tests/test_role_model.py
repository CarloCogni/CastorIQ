# facilities/tests/test_role_model.py
"""Tests for environments.ProjectRole — multi-role-per-user, active-window semantics."""

from datetime import timedelta

import pytest
from django.utils import timezone

from environments.models import ProjectRole
from environments.tests.factories import ProjectFactory, ProjectRoleFactory, UserFactory


@pytest.mark.django_db
class TestProjectRoleModel:
    """Core invariants of the ProjectRole model."""

    def test_str_renders_user_project_and_role_display(self):
        """__str__ is human-readable and uses the role's display label."""
        user = UserFactory(username="alice")
        project = ProjectFactory(name="Tower A")
        role = ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        assert "alice" in str(role)
        assert "Tower A" in str(role)
        assert "Facilities Manager" in str(role)

    def test_single_user_can_hold_multiple_concurrent_roles_on_same_project(self):
        """A user must be allowed multiple active roles per project (spec §3)."""
        user = UserFactory()
        project = ProjectFactory()

        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        ProjectRoleFactory(user=user, project=project, role="auditor")

        roles = ProjectRole.objects.filter(user=user, project=project)
        assert roles.count() == 2
        assert {r.role for r in roles} == {"facilitiesmanager", "auditor"}

    def test_duplicate_role_at_same_valid_from_is_rejected(self):
        """Unique constraint blocks duplicate (user, project, role, valid_from) tuples."""
        user = UserFactory()
        project = ProjectFactory()
        now = timezone.now()

        ProjectRoleFactory(user=user, project=project, role="contractor", valid_from=now)
        with pytest.raises(Exception):
            ProjectRoleFactory(user=user, project=project, role="contractor", valid_from=now)


@pytest.mark.django_db
class TestIsActiveAt:
    """ProjectRole.is_active_at validity-window logic."""

    def test_open_ended_role_is_active_after_valid_from(self):
        """A role with no valid_until stays active forever after valid_from."""
        role = ProjectRoleFactory(valid_from=timezone.now() - timedelta(days=1))
        assert role.is_active_at(timezone.now()) is True

    def test_role_before_valid_from_is_inactive(self):
        """A role is not yet active before its valid_from timestamp."""
        future = timezone.now() + timedelta(days=2)
        role = ProjectRoleFactory(valid_from=future)
        assert role.is_active_at(timezone.now()) is False

    def test_role_at_or_after_valid_until_is_inactive(self):
        """A role becomes inactive at the exact moment valid_until is hit."""
        now = timezone.now()
        role = ProjectRoleFactory(
            valid_from=now - timedelta(days=1),
            valid_until=now - timedelta(seconds=1),
        )
        assert role.is_active_at(now) is False

    def test_role_within_window_is_active(self):
        """A role is active when now is inside [valid_from, valid_until)."""
        now = timezone.now()
        role = ProjectRoleFactory(
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
        )
        assert role.is_active_at(now) is True


@pytest.mark.django_db
class TestActiveFor:
    """ProjectRole.active_for queryset helper."""

    def test_returns_only_currently_valid_roles(self):
        """Expired and not-yet-started roles are excluded."""
        user = UserFactory()
        project = ProjectFactory()
        now = timezone.now()

        live = ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        ProjectRoleFactory(
            user=user,
            project=project,
            role="auditor",
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(days=1),
        )
        ProjectRoleFactory(
            user=user,
            project=project,
            role="tenant",
            valid_from=now + timedelta(days=5),
        )

        active = list(ProjectRole.active_for(user, project))
        assert active == [live]

    def test_other_users_roles_are_excluded(self):
        """active_for filters by user — other users' roles don't leak."""
        user = UserFactory()
        other = UserFactory()
        project = ProjectFactory()

        mine = ProjectRoleFactory(user=user, project=project, role="contractor")
        ProjectRoleFactory(user=other, project=project, role="facilitiesmanager")

        active = list(ProjectRole.active_for(user, project))
        assert active == [mine]
