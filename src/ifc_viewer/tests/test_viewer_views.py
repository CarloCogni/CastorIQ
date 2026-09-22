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
        """The embed always ships the 4D task actions and the Option-3 paint bridge."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        response = client.get(_embed_url(project))
        body = response.content

        # 4D selection context menu — link / unlink / add another task.
        assert b'id="sel-ctx-addtask"' in body
        assert b'id="sel-ctx-link"' in body
        assert b'id="sel-ctx-unlink"' in body
        # Option-3 Time View paint bridge: colour request in, ACK out.
        assert b"castor:timeline-colors" in body
        assert b"castor:timeline-applied" in body
        assert b"not_due" in body
        assert b"colorByGlobalIds" in body

    def test_embed_inspect_mode_hides_4d_context_actions(self, client):
        """Option-3: ``?mode=inspect`` is not a capability switch and must not strip 4D.

        Pre-Option-3 embeds had an inspect-only variant that hid selection
        context actions. The approved ifc-lite contract keeps the embed as the
        4D / Time View surface for every ``?mode=`` value — including
        ``inspect`` — so the paint bridge and task actions stay present.
        """
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)

        inspect_body = client.get(_embed_url(project, mode="inspect")).content

        assert b'id="sel-ctx-addtask"' in inspect_body
        assert b'id="sel-ctx-link"' in inspect_body
        assert b'id="sel-ctx-unlink"' in inspect_body
        assert b"castor:timeline-colors" in inspect_body
        assert b"castor:timeline-applied" in inspect_body
        assert b"colorByGlobalIds" in inspect_body
        assert b"INSPECT_MODE" not in inspect_body

    def test_embed_ifc_param_selects_named_file(self, client):
        """?ifc=<pk> pins an older completed file instead of the latest."""
        project = ProjectFactory()
        older = IFCFileFactory(project=project)
        newer = IFCFileFactory(project=project)  # newer file becomes the default
        _login(client, project.owner)

        response = client.get(_embed_url(project, ifc=older.pk))
        body = response.content.decode()

        assert older.file.url in body
        assert newer.file.url not in body

    def test_embed_ifc_param_scopes_geometry_frag_and_colormap_to_one_file(self, client):
        """Geometry URL, .frag cache and colormap must all resolve to the pinned file.

        The .frag cache path is derived from the IFC file path, so a mismatch
        would paint one model with another model's fragments.
        """
        project = ProjectFactory()
        older = IFCFileFactory(project=project)
        newer = IFCFileFactory(project=project)
        _login(client, project.owner)

        body = client.get(_embed_url(project, ifc=older.pk)).content.decode()

        assert older.file.url in body
        # fragments cache + colormap endpoints both carry the pinned pk
        assert body.count(f"?ifc={older.pk}") >= 2
        assert f"?ifc={newer.pk}" not in body

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

    def test_props_ifc_param_cross_project_does_not_leak(self, client):
        """A file pk from another project cannot be used to read its entities."""
        project = ProjectFactory()
        IFCFileFactory(project=project)
        foreign_file = IFCFileFactory()  # other project
        foreign_entity = IFCEntityFactory(ifc_file=foreign_file)
        _login(client, project.owner)

        response = client.get(
            self._props_url(project, foreign_entity.global_id),
            {"ifc": str(foreign_file.pk)},
        )

        assert response.json()["found"] is False
