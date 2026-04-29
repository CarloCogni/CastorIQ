# facilities/tests/test_facilities_view.py
"""Tests for facilities.views — FacilitiesView + RoleSwitchView."""

import pytest
from django.urls import reverse

from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.services.role_service import SESSION_KEY


def _login(client, user):
    client.force_login(user)


@pytest.mark.django_db
class TestFacilitiesView:
    """GET /facilities/<uuid>/facilities/ — role-aware landing."""

    def test_unauthenticated_redirects_to_login(self, client):
        """ProjectAccessMixin (via ProjectTabMixin) redirects anonymous users."""
        project = ProjectFactory()
        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))
        assert response.status_code == 302

    def test_non_member_cannot_access_project(self, client):
        """Users without project access get 403 via ProjectAccessMixin."""
        outsider = UserFactory()
        project = ProjectFactory()
        _login(client, outsider)
        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))
        assert response.status_code == 403

    def test_owner_without_fm_role_sees_implicit_fm_dashboard(self, client):
        """Project owner with no explicit FM role gets the FM dashboard as an implicit fallback."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)
        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.status_code == 200
        assert response.context["facilities_active_role"] is None
        assert response.context["facilities_roles"] == []
        assert response.context["facilities_is_implicit_owner"] is True
        assert "dashboards/_fm.html" in response.context["facilities_dashboard_template"]
        assert "You don't yet have a facility-management role" not in response.content.decode()

    def test_non_owner_member_without_role_sees_default_with_people_link(self, client):
        """VIEWER/EDITOR members with no FM role still see the 'no role' landing — with a People link, not a Django admin reference."""
        viewer = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=viewer, permission="viewer")
        _login(client, viewer)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.status_code == 200
        assert response.context["facilities_active_role"] is None
        assert response.context["facilities_is_implicit_owner"] is False
        assert "dashboards/_default.html" in response.context["facilities_dashboard_template"]

        body = response.content.decode()
        assert "Django admin" not in body
        assert "M0 bootstrap" not in body
        assert reverse("projects:people", kwargs={"pk": project.pk}) in body

    def test_fm_role_renders_fm_dashboard(self, client):
        """A FACILITIESMANAGER role resolves to the FM dashboard template."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.status_code == 200
        assert response.context["facilities_active_role"].role == "facilitiesmanager"
        assert "dashboards/_fm.html" in response.context["facilities_dashboard_template"]

    def test_tenant_role_renders_tenant_dashboard(self, client):
        """A TENANT role resolves to the tenant dashboard template."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="tenant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert "dashboards/_tenant.html" in response.context["facilities_dashboard_template"]

    def test_multiple_roles_surface_all_in_context(self, client):
        """facilities_roles exposes every live role, in a stable ordering."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        ProjectRoleFactory(user=user, project=project, role="auditor")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        roles = {r.role for r in response.context["facilities_roles"]}
        assert roles == {"facilitiesmanager", "auditor"}


@pytest.mark.django_db
class TestOccupantMode:
    """The is_occupant_mode flag — drives portal-shell rendering vs. full FM workspace."""

    def test_owner_is_not_in_occupant_mode(self, client):
        """OWNER membership keeps the full workspace even with no functional role."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.status_code == 200
        assert response.context["facilities_is_occupant_mode"] is False

    def test_viewer_with_no_role_is_not_in_occupant_mode(self, client):
        """A bare VIEWER without a TENANT/OCCUPANT role is not in portal mode."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="viewer")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["facilities_is_occupant_mode"] is False

    def test_tenant_viewer_is_in_occupant_mode(self, client):
        """TENANT role + VIEWER access flips the user into portal mode."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="viewer")
        ProjectRoleFactory(user=user, project=project, role="tenant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["facilities_is_occupant_mode"] is True

    def test_occupant_viewer_is_in_occupant_mode(self, client):
        """OCCUPANT role + VIEWER access also flips into portal mode."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="viewer")
        ProjectRoleFactory(user=user, project=project, role="occupant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["facilities_is_occupant_mode"] is True

    def test_editor_with_tenant_role_is_not_in_occupant_mode(self, client):
        """FM staff testing as TENANT keep the full workspace — can_modify wins."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="editor")
        ProjectRoleFactory(user=user, project=project, role="tenant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["facilities_is_occupant_mode"] is False

    def test_owner_with_occupant_role_is_not_in_occupant_mode(self, client):
        """Owner + OCCUPANT role — owner can_modify, so portal mode stays off."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="occupant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["facilities_is_occupant_mode"] is False

    def test_portal_landing_context_loaded_for_tenant(self, client):
        """In occupant mode, FacilitiesView injects portal_assigned_space + recent ARs."""
        from facilities.tests.factories import ActionRequestFactory
        from ifc_processor.tests.factories import IFCSpatialElementFactory

        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="viewer")
        space = IFCSpatialElementFactory()
        ProjectRoleFactory(user=user, project=project, role="tenant", assigned_space=space)
        ar = ActionRequestFactory(project=project, submitted_by=user, title="Cold room")
        # AR submitted by another user should NOT appear in this user's recent list.
        other = UserFactory()
        ActionRequestFactory(project=project, submitted_by=other, title="Other AR")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))

        assert response.context["portal_assigned_space"] == space
        recent_titles = [a.title for a in response.context["portal_recent_ars"]]
        assert "Cold room" in recent_titles
        assert "Other AR" not in recent_titles
        assert ar in response.context["portal_recent_ars"]

    def test_top_tab_strip_hides_other_tabs_for_occupant(self, client):
        """The project_detail.html tab strip omits Ask/Modify/etc. when in portal mode."""
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(project=project, user=user, permission="viewer")
        ProjectRoleFactory(user=user, project=project, role="tenant")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))
        body = response.content.decode()

        # Facilities tab still rendered, but as "Portal".
        assert "tab-facilities" in body
        assert "</i>Portal" in body
        # Other tab links stripped.
        assert reverse("writeback:modify", kwargs={"pk": project.pk}) not in body
        assert reverse("writeback:conflicts", kwargs={"pk": project.pk}) not in body
        assert reverse("projects:ask", kwargs={"pk": project.pk}) not in body

    def test_top_tab_strip_full_for_fm_user(self, client):
        """FM staff still see the whole tab strip — Ask/Modify/Conflicts/etc."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        _login(client, user)

        response = client.get(reverse("facilities:tab", kwargs={"pk": project.pk}))
        body = response.content.decode()

        # Original label rendered, not "Portal".
        assert "</i>Facilities" in body
        assert "</i>Portal" not in body
        assert reverse("writeback:modify", kwargs={"pk": project.pk}) in body
        assert reverse("writeback:conflicts", kwargs={"pk": project.pk}) in body


@pytest.mark.django_db
class TestRoleSwitchView:
    """POST /facilities/<uuid>/facilities/role/switch/ — HTMX partial re-render."""

    def test_switch_requires_login(self, client):
        """Anonymous POST is rejected."""
        project = ProjectFactory()
        url = reverse("facilities:role_switch", kwargs={"pk": project.pk})
        response = client.post(url, {})
        assert response.status_code in {302, 403}

    def test_missing_role_id_returns_400(self, client):
        """Empty role_id is a bad request."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)
        url = reverse("facilities:role_switch", kwargs={"pk": project.pk})

        response = client.post(url, {})
        assert response.status_code == 400

    def test_foreign_role_id_returns_400(self, client):
        """Activating someone else's role is rejected."""
        user = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=user)
        foreign = ProjectRoleFactory(user=other, project=project, role="auditor")
        _login(client, user)
        url = reverse("facilities:role_switch", kwargs={"pk": project.pk})

        response = client.post(url, {"role_id": str(foreign.id)})
        assert response.status_code == 400

    def test_valid_switch_renders_partial_and_sets_session(self, client):
        """Happy path re-renders the tab body and persists the selection."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        target = ProjectRoleFactory(user=user, project=project, role="auditor")
        _login(client, user)

        url = reverse("facilities:role_switch", kwargs={"pk": project.pk})
        response = client.post(url, {"role_id": str(target.id)})

        assert response.status_code == 200
        assert client.session[SESSION_KEY] == str(target.id)
        # partial body rendered, not the full project_detail chrome
        body = response.content.decode()
        assert "facilities-tab-body" in body
