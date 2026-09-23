# ifc_viewer/tests/test_viewer_quality21.py
"""VIEWER-QUALITY-21 — selection→props, zero-result Vis, Color-by request guards."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

VIEWER_HTML = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer.html"


def _login(client, user) -> None:
    client.force_login(user)


def _viewer_src() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


@pytest.mark.django_db
class TestViewerQuality21StaticContract:
    """Standalone viewer.html must encode the repaired selection/filter contract."""

    def test_props_open_on_selection_sync(self):
        src = _viewer_src()
        assert "_syncPropsToSelection" in src
        assert "_propsRequestSeq" in src
        assert "seq !== _propsRequestSeq" in src
        assert "_syncPropsToSelection();" in src

    def test_visibility_filters_survive_reset_all_colors(self):
        src = _viewer_src()
        assert "_reapplyVisibilityFilters" in src
        assert "resetAllColors" in src
        assert "_reapplyVisibilityFilters()" in src or (
            'typeof _visCache !== "undefined") _reapplyVisibilityFilters()' in src
        )

    def test_format_prop_value_handles_null_and_objects(self):
        src = _viewer_src()
        assert "_formatPropValue" in src
        assert "[object Object]" in src

    def test_ifc_query_pinned_on_data_urls(self):
        src = _viewer_src()
        assert "IFC_QUERY" in src
        assert "viewer_ifc_file.pk" in src

    def test_zero_result_banner_present(self):
        src = _viewer_src()
        assert 'id="vis-zero-banner"' in src
        assert "No elements visible" in src
        assert "_updateVisZeroBanner" in src

    def test_colormap_seq_and_active_reapply(self):
        src = _viewer_src()
        assert "_colorBySeq" in src
        assert "_activeColormap" in src
        assert "_reapplyActiveColormap" in src


@pytest.mark.django_db
class TestViewerQuality21ColormapHttp:
    """Colormap endpoint echoes scheme and Level is JSON-safe."""

    def test_level_returns_200_with_storey_strings(self, client):
        from ifc_processor.models import IFCSpatialElement

        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        storey_ent = IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBuildingStorey", name="Level 1")
        storey = IFCSpatialElement.objects.create(
            ifc_file=ifc,
            entity=storey_ent,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            elevation=0.0,
        )
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", spatial_container=storey)
        _login(client, project.owner)

        url = reverse("ifc_viewer:viewer_colormap", kwargs={"pk": project.pk})
        resp = client.get(url, {"by": "level"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["by"] == "level"
        labels = {row["label"] for row in data["legend"]}
        assert "Level 1" in labels
        assert "IfcBeam" not in labels

    def test_viewer_page_renders(self, client):
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, project.owner)
        url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"Castor Viewer" in resp.content or b"Castor Simulator" in resp.content
        assert b"vis-zero-banner" in resp.content
        assert b"_syncPropsToSelection" in resp.content

    def test_viewer_empty_ifc_state(self, client):
        project = ProjectFactory()
        _login(client, project.owner)
        url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"No processed IFC file" in resp.content

    def test_foreign_user_denied(self, client):
        project = ProjectFactory()
        IFCFileFactory(project=project)
        _login(client, UserFactory())
        url = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
        assert client.get(url).status_code == 403
