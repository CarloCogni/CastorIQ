# environments/tests/test_people_page.py
"""Tests for the People page view + HTMX endpoints."""

import pytest
from django.urls import reverse

from environments.models import ProjectMembership
from environments.services import ProjectAccessService
from environments.tests.factories import ProjectFactory, UserFactory

Permission = ProjectMembership.Permission


def _login(client, user):
    client.force_login(user)


@pytest.mark.django_db
class TestPeopleViewRendering:
    def test_owner_sees_manage_controls(self, client):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        _login(client, project.owner)
        resp = client.get(reverse("projects:people", args=[project.pk]))
        assert resp.status_code == 200
        assert b"Add member" in resp.content
        assert b"Transfer" in resp.content  # transfer modal trigger
        assert editor.username.encode() in resp.content

    def test_viewer_cannot_see_manage_controls(self, client):
        project = ProjectFactory()
        viewer = UserFactory()
        ProjectAccessService.add_member(project=project, user=viewer, permission=Permission.VIEWER)
        _login(client, viewer)
        resp = client.get(reverse("projects:people", args=[project.pk]))
        assert resp.status_code == 200
        assert b"Add member" not in resp.content

    def test_non_member_is_refused(self, client):
        project = ProjectFactory()
        outsider = UserFactory()
        _login(client, outsider)
        resp = client.get(reverse("projects:people", args=[project.pk]))
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMemberAddView:
    def test_owner_can_add_editor_by_email(self, client):
        project = ProjectFactory()
        target = UserFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"email": target.email, "permission": Permission.EDITOR},
        )
        assert resp.status_code == 200
        assert target.username.encode() in resp.content
        assert ProjectAccessService.user_permission(target, project) == Permission.EDITOR

    def test_unknown_email_returns_400(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"email": "no-such-user@example.com", "permission": Permission.VIEWER},
        )
        assert resp.status_code == 400

    def test_editor_cannot_add_members(self, client):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        target = UserFactory()
        _login(client, editor)
        resp = client.post(
            reverse("projects:member_add", args=[project.pk]),
            {"email": target.email, "permission": Permission.VIEWER},
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMemberChangePermissionView:
    def test_owner_can_change_editor_to_viewer(self, client):
        project = ProjectFactory()
        target = UserFactory()
        ProjectAccessService.add_member(project=project, user=target, permission=Permission.EDITOR)
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_change_permission", args=[project.pk, target.pk]),
            {"permission": Permission.VIEWER},
        )
        assert resp.status_code == 200
        assert ProjectAccessService.user_permission(target, project) == Permission.VIEWER

    def test_owner_cannot_self_demote(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:member_change_permission", args=[project.pk, project.owner.pk]),
            {"permission": Permission.EDITOR},
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestMemberRemoveView:
    def test_owner_can_remove_editor(self, client):
        project = ProjectFactory()
        target = UserFactory()
        ProjectAccessService.add_member(project=project, user=target, permission=Permission.EDITOR)
        _login(client, project.owner)
        resp = client.post(reverse("projects:member_remove", args=[project.pk, target.pk]))
        assert resp.status_code == 200
        assert ProjectAccessService.user_permission(target, project) is None

    def test_owner_cannot_remove_themselves(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        resp = client.post(reverse("projects:member_remove", args=[project.pk, project.owner.pk]))
        assert resp.status_code == 400


@pytest.mark.django_db
class TestTransferOwnershipView:
    def test_owner_can_transfer_to_existing_member(self, client):
        project = ProjectFactory()
        target = UserFactory()
        ProjectAccessService.add_member(project=project, user=target, permission=Permission.EDITOR)
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:transfer_ownership", args=[project.pk]),
            {"new_owner_id": target.pk},
        )
        assert resp.status_code == 204
        project.refresh_from_db()
        assert project.owner_id == target.pk

    def test_cannot_transfer_to_non_member(self, client):
        project = ProjectFactory()
        stranger = UserFactory()
        _login(client, project.owner)
        resp = client.post(
            reverse("projects:transfer_ownership", args=[project.pk]),
            {"new_owner_id": stranger.pk},
        )
        assert resp.status_code == 400

    def test_editor_cannot_transfer(self, client):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        _login(client, editor)
        resp = client.post(
            reverse("projects:transfer_ownership", args=[project.pk]),
            {"new_owner_id": project.owner.pk},
        )
        assert resp.status_code == 403
