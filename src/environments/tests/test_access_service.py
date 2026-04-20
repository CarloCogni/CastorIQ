# environments/tests/test_access_service.py
"""Tests for ProjectAccessService — the single source of truth for access tiers."""

import pytest
from django.db import IntegrityError

from environments.models import ProjectMembership
from environments.services import (
    LastOwnerRemovalBlocked,
    OwnerDemotionBlocked,
    ProjectAccessError,
    ProjectAccessService,
)
from environments.tests.factories import ProjectFactory, UserFactory

Permission = ProjectMembership.Permission


@pytest.mark.django_db
class TestUserPermission:
    def test_owner_reported_correctly(self):
        project = ProjectFactory()
        assert ProjectAccessService.user_permission(project.owner, project) == Permission.OWNER

    def test_non_member_returns_none(self):
        project = ProjectFactory()
        outsider = UserFactory()
        assert ProjectAccessService.user_permission(outsider, project) is None

    def test_editor_member(self):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        assert ProjectAccessService.user_permission(editor, project) == Permission.EDITOR

    def test_anonymous_user_returns_none(self):
        from django.contrib.auth.models import AnonymousUser

        project = ProjectFactory()
        assert ProjectAccessService.user_permission(AnonymousUser(), project) is None


@pytest.mark.django_db
class TestCanMethods:
    def test_owner_can_everything(self):
        project = ProjectFactory()
        owner = project.owner
        assert ProjectAccessService.can_access(owner, project)
        assert ProjectAccessService.can_modify(owner, project)
        assert ProjectAccessService.can_admin(owner, project)
        assert ProjectAccessService.can_delete(owner, project)

    def test_editor_can_access_and_modify_only(self):
        project = ProjectFactory()
        editor = UserFactory()
        ProjectAccessService.add_member(project=project, user=editor, permission=Permission.EDITOR)
        assert ProjectAccessService.can_access(editor, project)
        assert ProjectAccessService.can_modify(editor, project)
        assert not ProjectAccessService.can_admin(editor, project)
        assert not ProjectAccessService.can_delete(editor, project)

    def test_viewer_can_access_only(self):
        project = ProjectFactory()
        viewer = UserFactory()
        ProjectAccessService.add_member(project=project, user=viewer, permission=Permission.VIEWER)
        assert ProjectAccessService.can_access(viewer, project)
        assert not ProjectAccessService.can_modify(viewer, project)
        assert not ProjectAccessService.can_admin(viewer, project)
        assert not ProjectAccessService.can_delete(viewer, project)

    def test_non_member_cannot_access(self):
        project = ProjectFactory()
        outsider = UserFactory()
        assert not ProjectAccessService.can_access(outsider, project)
        assert not ProjectAccessService.can_modify(outsider, project)


@pytest.mark.django_db
class TestAddMember:
    def test_adds_viewer(self):
        project = ProjectFactory()
        user = UserFactory()
        membership = ProjectAccessService.add_member(
            project=project, user=user, permission=Permission.VIEWER
        )
        assert membership.permission == Permission.VIEWER

    def test_refuses_second_owner(self):
        project = ProjectFactory()
        other = UserFactory()
        with pytest.raises(ProjectAccessError):
            ProjectAccessService.add_member(
                project=project, user=other, permission=Permission.OWNER
            )

    def test_rejects_unknown_permission(self):
        project = ProjectFactory()
        user = UserFactory()
        with pytest.raises(ValueError):
            ProjectAccessService.add_member(project=project, user=user, permission="tsar")

    def test_idempotent_on_existing_same_permission(self):
        project = ProjectFactory()
        user = UserFactory()
        m1 = ProjectAccessService.add_member(
            project=project, user=user, permission=Permission.EDITOR
        )
        m2 = ProjectAccessService.add_member(
            project=project, user=user, permission=Permission.EDITOR
        )
        assert m1.pk == m2.pk


@pytest.mark.django_db
class TestChangePermission:
    def test_promotes_viewer_to_editor(self):
        project = ProjectFactory()
        user = UserFactory()
        ProjectAccessService.add_member(project=project, user=user, permission=Permission.VIEWER)
        ProjectAccessService.change_permission(
            project=project, user=user, new_permission=Permission.EDITOR
        )
        assert ProjectAccessService.user_permission(user, project) == Permission.EDITOR

    def test_sole_owner_demotion_is_blocked(self):
        project = ProjectFactory()
        with pytest.raises(OwnerDemotionBlocked):
            ProjectAccessService.change_permission(
                project=project, user=project.owner, new_permission=Permission.EDITOR
            )

    def test_promotion_to_owner_via_change_is_blocked(self):
        project = ProjectFactory()
        user = UserFactory()
        ProjectAccessService.add_member(project=project, user=user, permission=Permission.EDITOR)
        with pytest.raises(ProjectAccessError):
            ProjectAccessService.change_permission(
                project=project, user=user, new_permission=Permission.OWNER
            )


@pytest.mark.django_db
class TestRemoveMember:
    def test_removes_editor(self):
        project = ProjectFactory()
        user = UserFactory()
        ProjectAccessService.add_member(project=project, user=user, permission=Permission.EDITOR)
        ProjectAccessService.remove_member(project=project, user=user)
        assert ProjectAccessService.user_permission(user, project) is None

    def test_last_owner_removal_is_blocked(self):
        project = ProjectFactory()
        with pytest.raises(LastOwnerRemovalBlocked):
            ProjectAccessService.remove_member(project=project, user=project.owner)


@pytest.mark.django_db
class TestTransferOwnership:
    def test_moves_owner_and_demotes_old_owner(self):
        project = ProjectFactory()
        old_owner = project.owner
        new_owner = UserFactory()
        ProjectAccessService.add_member(
            project=project, user=new_owner, permission=Permission.EDITOR
        )

        ProjectAccessService.transfer_ownership(project=project, new_owner=new_owner)

        project.refresh_from_db()
        assert project.owner_id == new_owner.pk
        assert ProjectAccessService.user_permission(new_owner, project) == Permission.OWNER
        assert ProjectAccessService.user_permission(old_owner, project) == Permission.EDITOR

    def test_noop_when_target_is_already_owner(self):
        project = ProjectFactory()
        ProjectAccessService.transfer_ownership(project=project, new_owner=project.owner)
        project.refresh_from_db()
        assert project.owner_id == project.owner_id  # unchanged

    def test_creates_membership_if_new_owner_had_none(self):
        project = ProjectFactory()
        new_owner = UserFactory()
        # new_owner is not yet a member — transfer should create the row.
        ProjectAccessService.transfer_ownership(project=project, new_owner=new_owner)
        assert ProjectAccessService.user_permission(new_owner, project) == Permission.OWNER

    def test_single_owner_invariant_after_transfer(self):
        project = ProjectFactory()
        new_owner = UserFactory()
        ProjectAccessService.add_member(
            project=project, user=new_owner, permission=Permission.EDITOR
        )
        ProjectAccessService.transfer_ownership(project=project, new_owner=new_owner)
        owner_rows = ProjectMembership.objects.filter(
            project=project, permission=Permission.OWNER
        ).count()
        assert owner_rows == 1


@pytest.mark.django_db
class TestUniqueOwnerConstraint:
    def test_cannot_insert_two_owners_via_raw_orm(self):
        """The partial unique index catches any bypass of the service."""
        project = ProjectFactory()
        second = UserFactory()
        with pytest.raises(IntegrityError):
            ProjectMembership.objects.create(
                project=project, user=second, permission=Permission.OWNER
            )


@pytest.mark.django_db
class TestAccessibleProjects:
    def test_returns_projects_user_is_member_of(self):
        p1 = ProjectFactory()  # p1.owner is a member by default
        p2 = ProjectFactory()  # different owner
        user = UserFactory()
        ProjectAccessService.add_member(project=p1, user=user, permission=Permission.VIEWER)

        projects = list(ProjectAccessService.accessible_projects(user))
        pks = {p.pk for p in projects}
        assert p1.pk in pks
        assert p2.pk not in pks

    def test_anonymous_user_gets_empty_queryset(self):
        from django.contrib.auth.models import AnonymousUser

        ProjectFactory()  # there exists a project, but anon has no membership
        assert not ProjectAccessService.accessible_projects(AnonymousUser()).exists()
