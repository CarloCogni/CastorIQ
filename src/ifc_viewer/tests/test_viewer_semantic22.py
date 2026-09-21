# ifc_viewer/tests/test_viewer_semantic22.py
"""VIEWER-SEMANTIC-22 — Color-by Level, scheme echo, Schedule Status empty truth."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.models import IFCSpatialElement
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from ifc_viewer.services.colormap import build_colormap

VIEWER_HTML = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer.html"


def _login(client, user) -> None:
    client.force_login(user)


def _viewer_src() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


def _storey(ifc_file, name: str) -> IFCSpatialElement:
    """Create a building-storey spatial container with a named entity."""
    storey_ent = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBuildingStorey", name=name)
    return IFCSpatialElement.objects.create(
        ifc_file=ifc_file,
        entity=storey_ent,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        elevation=0.0,
    )


@pytest.mark.django_db
class TestColorBySemantic22:
    """Backend + static contracts that prove meaning, not mere legend presence."""

    def test_level_group_keys_are_storey_names_not_ifc_classes(self):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        l0 = _storey(ifc, "L00_Ground")
        l1 = _storey(ifc, "L01_First")
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", spatial_container=l0)
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", spatial_container=l1)
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", spatial_container=None)

        payload = build_colormap(ifc, "level")
        labels = {row["label"] for row in payload["legend"]}

        assert payload["by"] == "level"
        assert "L00_Ground" in labels
        assert "L01_First" in labels
        assert "Unassigned" in labels
        assert "IfcBeam" not in labels
        assert "IfcWall" not in labels
        assert "IfcSlab" not in labels

    def test_element_type_returns_ifc_classes(self):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam")
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn")

        payload = build_colormap(ifc, "element_type")
        labels = {row["label"] for row in payload["legend"]}

        assert payload["by"] == "element_type"
        assert "IfcBeam" in labels
        assert "IfcColumn" in labels

    def test_schemes_are_distinct(self):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        storey = _storey(ifc, "B03_Basement")
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", spatial_container=storey)

        level = build_colormap(ifc, "level")
        etype = build_colormap(ifc, "element_type")
        assert level["by"] == "level"
        assert etype["by"] == "element_type"
        assert {r["label"] for r in level["legend"]} != {r["label"] for r in etype["legend"]}

    def test_colormap_view_level_query_param(self, client):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        storey = _storey(ifc, "L02_Level")
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", spatial_container=storey)
        _login(client, project.owner)

        url = reverse("ifc_viewer:viewer_colormap", kwargs={"pk": project.pk})
        data = client.get(url, {"by": "level", "ifc": str(ifc.pk)}).json()

        assert data["by"] == "level"
        labels = [row["label"] for row in data["legend"]]
        assert "L02_Level" in labels
        assert "IfcBeam" not in labels

    def test_colormap_view_defaults_element_type_when_by_missing(self, client):
        """Documents historical default — client must always send by= explicitly."""
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall")
        _login(client, project.owner)

        url = reverse("ifc_viewer:viewer_colormap", kwargs={"pk": project.pk})
        data = client.get(url, {"ifc": str(ifc.pk)}).json()
        assert data["by"] == "element_type"
        assert "IfcWall" in {r["label"] for r in data["legend"]}

    def test_viewer_html_uses_qs_append_not_double_question(self):
        src = _viewer_src()
        assert "_qsAppend" in src
        assert "_activeColorBy" in src
        assert "_colorBySeq" in src
        assert "data.by !== by" in src
        assert "_reapplyActiveColormap" in src
        assert "${COLORMAP_URL}?by=" not in src

    def test_invalid_scheme_echoes_error(self):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        payload = build_colormap(ifc, "not_a_scheme")
        assert payload["by"] == "not_a_scheme"
        assert payload.get("error") == "invalid_scheme"
        assert payload["legend"] == []

    def test_viewer_html_reset_clears_colorby_to_none(self):
        src = _viewer_src()
        assert 'document.getElementById("colorby-select").value = "none"' in src
        assert "_clearColorByUi()" in src

    def test_broken_double_question_url_is_not_emitted(self):
        """Regression: `${COLORMAP_URL}?by=` made Django drop by= entirely."""
        src = _viewer_src()
        assert "_qsAppend(COLORMAP_URL, { by })" in src
        assert 'url.includes("?") ? "&" : "?"' in src

    def test_material_and_schedule_echo_scheme(self):
        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcWall",
            properties={"Material": "Concrete", "Pset Activity Id": "ACT-FAKE"},
        )
        mat = build_colormap(ifc, "material")
        sch = build_colormap(ifc, "schedule_status", project_id=str(project.pk))
        assert mat["by"] == "material"
        assert "Concrete" in {r["label"] for r in mat["legend"]}
        assert sch["by"] == "schedule_status"
        assert {r["label"] for r in sch["legend"]} == {"No schedule imported"}
        assert all(c == "#64748b" for c in sch["colormap"].values())
        assert "Linked to schedule" not in {r["label"] for r in sch["legend"]}

    def test_schedule_status_requires_task_entity_binding(self):
        from scheduling.models import ScheduleSource, Task, TaskEntityBinding

        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        linked = IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id="GID-LINKED",
            properties={"Activity Id": "SHOULD-NOT-COUNT"},
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcColumn",
            global_id="GID-UNLINKED",
            properties={"Activity Id": "ALSO-IGNORE"},
        )
        source = ScheduleSource.objects.create(
            project=project, filename="job.xer", source_format="xer", task_count=1
        )
        task = Task.objects.create(
            project=project,
            name="Pour",
            start_date="2025-01-01",
            end_date="2025-01-05",
            schedule_source=source,
        )
        TaskEntityBinding.objects.create(
            task=task, entity_global_id=linked.global_id, needs_review=False
        )

        sch = build_colormap(ifc, "schedule_status", project_id=str(project.pk))
        assert sch["by"] == "schedule_status"
        assert sch["counts"]["linked"] == 1
        assert sch["colormap"][linked.global_id] == "#22c55e"
        assert sch["colormap"]["GID-UNLINKED"] == "#94a3b8"
        assert "Linked to schedule" in {r["label"] for r in sch["legend"]}

    def test_schedule_with_source_but_no_binding_is_not_linked(self):
        from scheduling.models import ScheduleSource

        project = ProjectFactory()
        ifc = IFCFileFactory(project=project)
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-ONLY")
        ScheduleSource.objects.create(
            project=project, filename="empty.xer", source_format="xer", task_count=0
        )

        sch = build_colormap(ifc, "schedule_status", project_id=str(project.pk))
        assert sch["by"] == "schedule_status"
        assert {r["label"] for r in sch["legend"]} == {"Not linked"}
        assert sch["colormap"]["GID-ONLY"] == "#94a3b8"
