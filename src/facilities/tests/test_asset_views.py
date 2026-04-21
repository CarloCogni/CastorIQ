# facilities/tests/test_asset_views.py
"""Tests for the asset-register views in ``facilities/views.py``."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.models import ProjectMembership
from environments.services import ProjectAccessService
from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FacilityAsset
from facilities.tests.factories import (
    ClassificationFactory,
    ClassificationReferenceFactory,
    FacilityAssetFactory,
)
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _login(client, user):
    client.force_login(user)


def _make_editor(project, user):
    ProjectAccessService.add_member(
        project=project, user=user, permission=ProjectMembership.Permission.EDITOR
    )


def _make_viewer(project, user):
    ProjectAccessService.add_member(
        project=project, user=user, permission=ProjectMembership.Permission.VIEWER
    )


@pytest.mark.django_db
class TestAssetListView:
    """GET /facilities/<uuid>/facilities/assets/ — card grid."""

    def test_unauthenticated_redirects_to_login(self, client):
        """Anonymous users are bounced to the login page."""
        project = ProjectFactory()
        response = client.get(reverse("facilities:assets_list", args=[project.pk]))
        assert response.status_code == 302

    def test_non_member_gets_403(self, client):
        """Users without any project access are refused."""
        outsider = UserFactory()
        project = ProjectFactory()
        _login(client, outsider)
        response = client.get(reverse("facilities:assets_list", args=[project.pk]))
        assert response.status_code == 403

    def test_owner_sees_full_page(self, client):
        """Owners can view the asset list (200 + context populated)."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _login(client, project.owner)
        response = client.get(reverse("facilities:assets_list", args=[project.pk]))
        assert response.status_code == 200
        assert asset in response.context["page_obj"].object_list

    def test_viewer_can_list(self, client):
        """Viewer members can read the asset list."""
        project = ProjectFactory()
        viewer = UserFactory()
        _make_viewer(project, viewer)
        FacilityAssetFactory(project=project)
        _login(client, viewer)
        response = client.get(reverse("facilities:assets_list", args=[project.pk]))
        assert response.status_code == 200

    def test_htmx_request_returns_grid_fragment(self, client):
        """HX-Request header swaps to the card-grid partial (no full shell)."""
        project = ProjectFactory()
        _login(client, project.owner)
        response = client.get(
            reverse("facilities:assets_list", args=[project.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b'id="asset-grid' in response.content or b"No assets yet" in response.content
        # The shell-specific pieces (sub-nav, role switcher) should NOT appear in the partial.
        assert b"facilities-tab-body" not in response.content

    def test_text_search_narrows_results(self, client):
        """``?q=`` applies the service filter and shows only matching assets."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="AHU-04", manufacturer="Carrier")
        FacilityAssetFactory(project=project, asset_tag="BLR-01", manufacturer="Bosch")
        _login(client, project.owner)
        response = client.get(reverse("facilities:assets_list", args=[project.pk]) + "?q=Bosch")
        assert response.status_code == 200
        tags = [a.asset_tag for a in response.context["page_obj"].object_list]
        assert tags == ["BLR-01"]


@pytest.mark.django_db
class TestAssetDetailView:
    """GET /facilities/<uuid>/facilities/assets/<asset_pk>/"""

    def test_renders_hero_for_project_owner(self, client):
        """Owners see the asset detail page."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _login(client, project.owner)
        response = client.get(reverse("facilities:assets_detail", args=[project.pk, asset.pk]))
        assert response.status_code == 200
        assert response.context["asset"] == asset

    def test_404_for_asset_in_other_project(self, client):
        """Asset PK from another project yields 404."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        foreign_asset = FacilityAssetFactory(project=project_b)
        _login(client, project_a.owner)
        response = client.get(
            reverse(
                "facilities:assets_detail",
                args=[project_a.pk, foreign_asset.pk],
            )
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestAssetPromoteView:
    """Bulk-promote drawer — GET list candidates, POST creates assets."""

    def test_viewer_cannot_open_drawer(self, client):
        """Viewers are refused by ProjectModifyAccessMixin."""
        project = ProjectFactory()
        viewer = UserFactory()
        _make_viewer(project, viewer)
        _login(client, viewer)
        response = client.get(reverse("facilities:assets_promote", args=[project.pk]))
        assert response.status_code == 403

    def test_editor_sees_candidates(self, client):
        """Editors see IFCEntities without existing assets as promotion candidates."""
        project = ProjectFactory()
        editor = UserFactory()
        _make_editor(project, editor)
        fresh_entity = IFCEntityFactory(ifc_file=IFCFileFactory(project=project))
        _login(client, editor)
        response = client.get(reverse("facilities:assets_promote", args=[project.pk]))
        assert response.status_code == 200
        assert fresh_entity in response.context["candidates"]

    def test_post_promotes_selected_entities(self, client):
        """POST with ifc_entity_ids[] creates FacilityAsset rows and redirects."""
        project = ProjectFactory()
        _login(client, project.owner)
        entities = [IFCEntityFactory(ifc_file=IFCFileFactory(project=project)) for _ in range(2)]
        response = client.post(
            reverse("facilities:assets_promote", args=[project.pk]),
            data={"ifc_entity_ids": [str(e.pk) for e in entities]},
        )
        assert response.status_code == 302
        assert FacilityAsset.objects.filter(project=project).count() == 2

    def test_post_without_selection_is_400(self, client):
        """No entity IDs → bad request."""
        project = ProjectFactory()
        _login(client, project.owner)
        response = client.post(reverse("facilities:assets_promote", args=[project.pk]), data={})
        assert response.status_code == 400


@pytest.mark.django_db
class TestAssetUpdateView:
    """POST /facilities/<uuid>/facilities/assets/<asset_pk>/update/"""

    def test_viewer_cannot_update(self, client):
        """Viewers are refused (mutation endpoint)."""
        project = ProjectFactory()
        viewer = UserFactory()
        _make_viewer(project, viewer)
        asset = FacilityAssetFactory(project=project)
        _login(client, viewer)
        response = client.post(
            reverse("facilities:assets_update", args=[project.pk, asset.pk]),
            data={"manufacturer": "Updated"},
        )
        assert response.status_code == 403

    def test_owner_updates_manufacturer(self, client):
        """Owners can persist a new value and get the detail card back."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project, manufacturer="Old")
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_update", args=[project.pk, asset.pk]),
            data={"manufacturer": "Updated"},
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.manufacturer == "Updated"


@pytest.mark.django_db
class TestAssetBulkView:
    """POST /facilities/<uuid>/facilities/assets/bulk/"""

    def test_viewer_cannot_bulk(self, client):
        """Viewers are refused."""
        project = ProjectFactory()
        viewer = UserFactory()
        _make_viewer(project, viewer)
        asset = FacilityAssetFactory(project=project)
        _login(client, viewer)
        response = client.post(
            reverse("facilities:assets_bulk", args=[project.pk]),
            data={"action": "delete", "asset_ids": [str(asset.pk)]},
        )
        assert response.status_code == 403

    def test_classify_assigns_reference(self, client):
        """Bulk classify attaches the reference to selected assets."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        reference = ClassificationReferenceFactory(
            classification=ClassificationFactory(project=project)
        )
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_bulk", args=[project.pk]),
            data={
                "action": "classify",
                "asset_ids": [str(asset.pk)],
                "classification_reference_id": str(reference.pk),
                "classify_mode": "add",
            },
        )
        assert response.status_code == 302
        assert reference in asset.classifications.all()

    def test_delete_removes_selected_assets(self, client):
        """Bulk delete removes the selected assets."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_bulk", args=[project.pk]),
            data={"action": "delete", "asset_ids": [str(asset.pk)]},
        )
        assert response.status_code == 302
        assert not FacilityAsset.objects.filter(pk=asset.pk).exists()

    def test_unknown_action_is_400(self, client):
        """Unknown actions surface as bad request, not a silent no-op."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_bulk", args=[project.pk]),
            data={"action": "sing_a_song", "asset_ids": [str(asset.pk)]},
        )
        assert response.status_code == 400

    def test_no_selection_is_400(self, client):
        """Bulk POST without asset_ids → bad request."""
        project = ProjectFactory()
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_bulk", args=[project.pk]),
            data={"action": "delete"},
        )
        assert response.status_code == 400
