# ifc_viewer/tests/test_viewer_views.py
"""Tests for the viewer embed and data endpoints — inspect mode and ?ifc= scoping."""

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

# ── Helpers ────────────────────────────────────────────────────────────────


def _login(client, user):
    client.force_login(user)


def _embed_url(project, **params):
    url = reverse("ifc_viewer:viewer_embed", kwargs={"pk": project.pk})
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return url


# ── ViewerEmbedView ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestViewerEmbedView:
    """GET /viewer/projects/<pk>/embed/."""

    def test_embed_returns_200_with_sameorigin_header(self, client):
        """The embed page renders and is framable same-origin only."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(_embed_url(project))

        assert response.status_code == 200
        assert response["X-Frame-Options"] == "SAMEORIGIN"

    def test_embed_no_access_returns_403(self, client):
        """Non-members cannot load the embed."""
        project = ProjectFactory()
        _login(client, UserFactory())

        response = client.get(_embed_url(project))

        assert response.status_code == 403

    def test_embed_default_mode_shows_4d_context_actions(self, client):
        """Without mode=inspect the 4D task actions stay in the context menu."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(_embed_url(project))

        assert b'id="sel-ctx-addtask"' in response.content
        assert b"INSPECT_MODE = false" in response.content

    def test_embed_inspect_mode_hides_4d_context_actions(self, client):
        """mode=inspect removes 4D task actions and flags the JS constant."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(_embed_url(project, mode="inspect"))

        assert b'id="sel-ctx-addtask"' not in response.content
        assert b"INSPECT_MODE = true" in response.content

    def test_embed_ifc_param_selects_named_file(self, client):
        """?ifc=<pk> pins an older completed file instead of the latest."""
        project = ProjectFactory()
        older = IFCFileFactory(project=project)
        IFCFileFactory(project=project)  # newer file becomes the default
        _login(client, project.owner)

        response = client.get(_embed_url(project, ifc=older.pk))

        assert older.file.url.encode() in response.content

    def test_embed_ifc_param_cross_project_falls_back_to_latest(self, client):
        """A file pk from another project must not leak — fall back to latest."""
        project = ProjectFactory()
        own_file = IFCFileFactory(project=project)
        foreign_file = IFCFileFactory()  # other project
        _login(client, project.owner)

        response = client.get(_embed_url(project, ifc=foreign_file.pk))

        assert own_file.file.url.encode() in response.content
        assert foreign_file.file.url.encode() not in response.content

    def test_embed_ifc_param_garbage_falls_back_to_latest(self, client):
        """A non-UUID ?ifc= value is ignored instead of erroring."""
        project = ProjectFactory()
        own_file = IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(_embed_url(project, ifc="not-a-uuid"))

        assert response.status_code == 200
        assert own_file.file.url.encode() in response.content


# ── ElementPropertiesView ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestElementPropertiesView:
    """GET /viewer/projects/<pk>/element/<global_id>/."""

    def _props_url(self, project, global_id):
        return reverse(
            "ifc_viewer:viewer_element_props",
            kwargs={"pk": project.pk, "global_id": global_id},
        )

    def test_props_found_returns_entity_json(self, client):
        """A known GlobalId returns its type, name and properties."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        entity = IFCEntityFactory(ifc_file=ifc_file, name="Wall-A")
        _login(client, project.owner)

        response = client.get(self._props_url(project, entity.global_id))

        data = response.json()
        assert data["found"] is True
        assert data["name"] == "Wall-A"
        assert data["ifc_type"] == "IfcWall"

    def test_props_unknown_gid_returns_found_false(self, client):
        """An unknown GlobalId reports found=false, not a 404."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(self._props_url(project, "MISSING-GID"))

        assert response.json()["found"] is False

    def test_props_ifc_param_scopes_lookup_to_named_file(self, client):
        """An entity in an older file is only found when ?ifc= pins that file."""
        project = ProjectFactory()
        older = IFCFileFactory(project=project)
        IFCFileFactory(project=project)  # newer file becomes the default
        entity = IFCEntityFactory(ifc_file=older)
        _login(client, project.owner)

        default_response = client.get(self._props_url(project, entity.global_id))
        pinned_response = client.get(
            self._props_url(project, entity.global_id), {"ifc": str(older.pk)}
        )

        assert default_response.json()["found"] is False
        assert pinned_response.json()["found"] is True
