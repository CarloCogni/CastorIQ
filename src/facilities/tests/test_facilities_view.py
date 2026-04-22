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
