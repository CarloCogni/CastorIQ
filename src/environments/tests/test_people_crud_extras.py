# environments/tests/test_people_crud_extras.py
"""Tests for user-search typeahead, typeahead-backed member-add, and
functional-role CRUD on the People page.

Pairs with ``test_people_page.py`` (access-tier CRUD) — this file covers the
additions that landed after initial M0: UserSearchView, the user_id path
through MemberAddView, and RoleAddView / RoleRemoveView.
"""

import pytest
from django.urls import reverse

from environments.models import ProjectMembership, ProjectRole
from environments.services import ProjectAccessService
from environments.tests.factories import (
    ProjectFactory,
    ProjectRoleFactory,
    UserFactory,
)

Permission = ProjectMembership.Permission
Role = ProjectRole.Role


def _login(client, user):
    client.force_login(user)


# ---- UserSearchView --------------------------------------------------------


@pytest.mark.django_db
class TestUserSearchView:
    """UserSearchView returns JSON. Shape:
    {too_short: bool, matches: [{id, username, email}, ...], query, scope}
    """

    def test_owner_can_search_users(self, client):
        project = ProjectFactory()
        UserFactory(username="alice_smith", email="alice@test.com")
        UserFactory(username="bob_jones", email="bob@test.com")
        _login(client, project.owner)

        resp = client.get(reverse("projects:user_search", args=[project.pk]), {"q": "alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["too_short"] is False
        usernames = [m["username"] for m in data["matches"]]
        assert "alice_smith" in usernames
        assert "bob_jones" not in usernames

    def test_short_query_returns_too_short_flag(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.get(reverse("projects:user_search", args=[project.pk]), {"q": "a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["too_short"] is True
        assert data["matches"] == []

    def test_search_excludes_existing_members_in_member_scope(self, client):
        project = ProjectFactory()
        existing = UserFactory(username="already_here")
        ProjectAccessService.add_member(
            project=project, user=existing, permission=Permission.EDITOR
        )
        _login(client, project.owner)

        resp = client.get(
            reverse("projects:user_search", args=[project.pk]),
            {"q": "already", "scope": "member"},
        )
        assert resp.status_code == 200
        usernames = [m["username"] for m in resp.json()["matches"]]
        assert "already_here" not in usernames

    def test_search_includes_existing_members_in_role_scope(self, client):
        project = ProjectFactory()
        existing = UserFactory(username="already_here")
        ProjectAccessService.add_member(
            project=project, user=existing, permission=Permission.EDITOR
        )
        _login(client, project.owner)

        resp = client.get(
            reverse("projects:user_search", args=[project.pk]),
            {"q": "already", "scope": "role"},
        )
        assert resp.status_code == 200
        usernames = [m["username"] for m in resp.json()["matches"]]
        assert "already_here" in usernames

    def test_non_admin_cannot_search(self, client):
        project = ProjectFactory()
        viewer = UserFactory()
        ProjectAccessService.add_member(project=project, user=viewer, permission=Permission.VIEWER)
        _login(client, viewer)
        resp = client.get(reverse("projects:user_search", args=[project.pk]), {"q": "bob"})
        assert resp.status_code == 403


# ---- MemberAddView (user_id path + invite fallback) -----------------------


@pytest.mark.django_db
class TestMemberAddViaUserId:
    def test_owner_can_add_member_by_user_id(self, client):
        project = ProjectFactory()
        target = UserFactory()
        _login(client, project.owner)

        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"user_id": target.pk, "permission": Permission.EDITOR},
        )
        assert resp.status_code == 200
        assert ProjectAccessService.user_permission(target, project) == Permission.EDITOR

    def test_missing_user_id_returns_400_with_prompt(self, client):
        """MemberAddView is user_id-only now — the email-invite fallback has
        been split out into a client-side mailto: widget."""
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"permission": Permission.VIEWER},
        )
        assert resp.status_code == 400
        assert b"Pick a user from the suggestions" in resp.content

    def test_unknown_user_id_returns_404(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"user_id": "9999999", "permission": Permission.VIEWER},
        )
        assert resp.status_code == 404


# ---- RoleAddView -----------------------------------------------------------


@pytest.mark.django_db
class TestRoleAddView:
    def test_owner_can_assign_role(self, client):
        project = ProjectFactory()
        target = UserFactory()
        _login(client, project.owner)

        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {
                "user_id": target.pk,
                "role": Role.FACILITIESMANAGER,
                "valid_from": "2026-04-01",
            },
        )
        assert resp.status_code == 200
        assert ProjectRole.objects.filter(
            project=project, user=target, role=Role.FACILITIESMANAGER
        ).exists()

    def test_role_can_be_assigned_to_non_member(self, client):
        """Functional role does not require project membership."""
        project = ProjectFactory()
        target = UserFactory()  # no ProjectMembership on `project`
        _login(client, project.owner)

        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {"user_id": target.pk, "role": Role.AUDITOR},
        )
        assert resp.status_code == 200
        assert ProjectRole.objects.filter(project=project, user=target).exists()

    def test_missing_user_id_returns_400(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {"role": Role.AUDITOR},
        )
        assert resp.status_code == 400

    def test_unknown_role_returns_400(self, client):
        project = ProjectFactory()
        target = UserFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {"user_id": target.pk, "role": "not_a_real_role"},
        )
        assert resp.status_code == 400

    def test_invalid_until_before_from_returns_400(self, client):
        project = ProjectFactory()
        target = UserFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {
                "user_id": target.pk,
                "role": Role.AUDITOR,
                "valid_from": "2026-04-10",
                "valid_until": "2026-04-01",
            },
        )
        assert resp.status_code == 400

    def test_editor_cannot_assign_roles(self, client):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        target = UserFactory()
        _login(client, editor)
        resp = client.post(
            reverse("projects:role_add", args=[project.pk]),
            {"user_id": target.pk, "role": Role.AUDITOR},
        )
        assert resp.status_code == 403


# ---- RoleRemoveView --------------------------------------------------------


@pytest.mark.django_db
class TestRoleRemoveView:
    def test_owner_can_remove_role(self, client):
        project = ProjectFactory()
        role = ProjectRoleFactory(project=project)
        _login(client, project.owner)
        resp = client.post(reverse("projects:role_remove", args=[project.pk, role.pk]))
        assert resp.status_code == 200
        assert not ProjectRole.objects.filter(pk=role.pk).exists()

    def test_cannot_remove_role_from_another_project(self, client):
        project = ProjectFactory()
        other_project = ProjectFactory()
        foreign_role = ProjectRoleFactory(project=other_project)
        _login(client, project.owner)

        resp = client.post(reverse("projects:role_remove", args=[project.pk, foreign_role.pk]))
        assert resp.status_code == 404
        # Role untouched in the other project.
        assert ProjectRole.objects.filter(pk=foreign_role.pk).exists()

    def test_viewer_cannot_remove_role(self, client):
        project = ProjectFactory()
        viewer = UserFactory()
        ProjectAccessService.add_member(project=project, user=viewer, permission=Permission.VIEWER)
        role = ProjectRoleFactory(project=project)
        _login(client, viewer)
        resp = client.post(reverse("projects:role_remove", args=[project.pk, role.pk]))
        assert resp.status_code == 403
