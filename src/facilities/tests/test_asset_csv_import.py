# facilities/tests/test_asset_csv_import.py
"""End-to-end tests for the CSV import view."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from environments.models import ProjectMembership
from environments.services import ProjectAccessService
from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FacilityAsset
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _login(client, user):
    client.force_login(user)


@pytest.mark.django_db
class TestAssetCSVImportView:
    """POST /facilities/<uuid>/facilities/assets/import/"""

    def test_viewer_cannot_import(self, client):
        """Viewer is refused by ProjectModifyAccessMixin."""
        project = ProjectFactory()
        viewer = UserFactory()
        ProjectAccessService.add_member(
            project=project,
            user=viewer,
            permission=ProjectMembership.Permission.VIEWER,
        )
        _login(client, viewer)

        upload = SimpleUploadedFile("x.csv", b"global_id,asset_tag\nGID,AST\n")
        response = client.post(
            reverse("facilities:assets_import", args=[project.pk]),
            data={"file": upload, "dry_run": "true"},
        )
        assert response.status_code == 403

    def test_dry_run_does_not_write(self, client):
        """Dry-run preview validates the CSV without persisting rows."""
        project = ProjectFactory()
        entity = IFCEntityFactory(ifc_file=IFCFileFactory(project=project))
        _login(client, project.owner)

        csv_payload = f"global_id,asset_tag\n{entity.global_id},AST-01\n".encode()
        upload = SimpleUploadedFile("import.csv", csv_payload, content_type="text/csv")
        response = client.post(
            reverse("facilities:assets_import", args=[project.pk]),
            data={"file": upload, "dry_run": "true"},
        )
        assert response.status_code == 200
        assert response.context["result"]["created"] == 1
        assert not FacilityAsset.objects.filter(project=project).exists()

    def test_commit_writes_rows(self, client):
        """Commit mode actually persists the parsed rows."""
        project = ProjectFactory()
        entity = IFCEntityFactory(ifc_file=IFCFileFactory(project=project))
        _login(client, project.owner)

        csv_payload = f"global_id,asset_tag\n{entity.global_id},AST-02\n".encode()
        upload = SimpleUploadedFile("import.csv", csv_payload, content_type="text/csv")
        response = client.post(
            reverse("facilities:assets_import", args=[project.pk]),
            data={"file": upload, "dry_run": "false"},
        )
        assert response.status_code == 200
        assert FacilityAsset.objects.filter(project=project, asset_tag="AST-02").exists()

    def test_missing_file_is_400(self, client):
        """Missing file upload surfaces as bad request."""
        project = ProjectFactory()
        _login(client, project.owner)
        response = client.post(
            reverse("facilities:assets_import", args=[project.pk]),
            data={"dry_run": "true"},
        )
        assert response.status_code == 400

    def test_missing_global_id_column_is_400(self, client):
        """A CSV without global_id surfaces as bad request (AssetValidationError)."""
        project = ProjectFactory()
        _login(client, project.owner)
        upload = SimpleUploadedFile("bad.csv", b"asset_tag\nAST-01\n", content_type="text/csv")
        response = client.post(
            reverse("facilities:assets_import", args=[project.pk]),
            data={"file": upload, "dry_run": "true"},
        )
        assert response.status_code == 400
