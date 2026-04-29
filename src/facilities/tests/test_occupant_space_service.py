# facilities/tests/test_occupant_space_service.py
"""Tests for OccupantSpaceService — TENANT/OCCUPANT spatial resolver."""

from datetime import timedelta

import pytest
from django.utils import timezone

from environments.tests.factories import (
    ProjectFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.services.occupant_space_service import OccupantSpaceService
from ifc_processor.tests.factories import IFCSpatialElementFactory


@pytest.mark.django_db
class TestResolve:
    """Pick the right spatial container for a TENANT/OCCUPANT user."""

    def test_no_role_returns_none(self):
        """User with no functional role on the project gets None."""
        user = UserFactory()
        project = ProjectFactory()
        assert OccupantSpaceService(project, user).resolve() is None
        assert OccupantSpaceService(project, user).resolve_active_role() is None

    def test_non_occupant_role_returns_none(self):
        """A FACILITIESMANAGER role does NOT carry a portal seating."""
        user = UserFactory()
        project = ProjectFactory()
        space = IFCSpatialElementFactory()
        ProjectRoleFactory(
            user=user,
            project=project,
            role="facilitiesmanager",
            assigned_space=space,
        )
        assert OccupantSpaceService(project, user).resolve() is None

    def test_tenant_resolves_to_assigned_space(self):
        """Active TENANT row with an assigned space returns that space."""
        user = UserFactory()
        project = ProjectFactory()
        space = IFCSpatialElementFactory()
        ProjectRoleFactory(user=user, project=project, role="tenant", assigned_space=space)

        assert OccupantSpaceService(project, user).resolve() == space

    def test_occupant_resolves_to_assigned_space(self):
        """Active OCCUPANT row also resolves."""
        user = UserFactory()
        project = ProjectFactory()
        space = IFCSpatialElementFactory()
        ProjectRoleFactory(user=user, project=project, role="occupant", assigned_space=space)

        assert OccupantSpaceService(project, user).resolve() == space

    def test_tenant_outranks_occupant(self):
        """When a user holds both, TENANT wins — long-lease over transient."""
        user = UserFactory()
        project = ProjectFactory()
        tenant_space = IFCSpatialElementFactory()
        occupant_space = IFCSpatialElementFactory()
        ProjectRoleFactory(
            user=user,
            project=project,
            role="occupant",
            assigned_space=occupant_space,
        )
        ProjectRoleFactory(
            user=user,
            project=project,
            role="tenant",
            assigned_space=tenant_space,
        )

        assert OccupantSpaceService(project, user).resolve() == tenant_space

    def test_expired_role_is_skipped(self):
        """A role outside its validity window does not resolve."""
        user = UserFactory()
        project = ProjectFactory()
        space = IFCSpatialElementFactory()
        # valid_until in the past — no longer active.
        ProjectRoleFactory(
            user=user,
            project=project,
            role="tenant",
            assigned_space=space,
            valid_from=timezone.now() - timedelta(days=400),
            valid_until=timezone.now() - timedelta(days=1),
        )

        assert OccupantSpaceService(project, user).resolve() is None

    def test_role_without_assigned_space_returns_none(self):
        """An occupant role without seating returns None even when active."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectRoleFactory(user=user, project=project, role="tenant")

        assert OccupantSpaceService(project, user).resolve() is None
        # Active role still resolves — the seating is just blank.
        role = OccupantSpaceService(project, user).resolve_active_role()
        assert role is not None and role.role == "tenant"

    def test_role_on_different_project_does_not_leak(self):
        """A TENANT role on project B must not resolve for project A."""
        user = UserFactory()
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        space = IFCSpatialElementFactory()
        ProjectRoleFactory(user=user, project=project_b, role="tenant", assigned_space=space)

        assert OccupantSpaceService(project_a, user).resolve() is None

    def test_anonymous_user_returns_none(self):
        """Unauthenticated principals never resolve."""
        from django.contrib.auth.models import AnonymousUser

        project = ProjectFactory()
        assert OccupantSpaceService(project, AnonymousUser()).resolve() is None
