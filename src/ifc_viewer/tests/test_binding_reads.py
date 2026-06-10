# ifc_viewer/tests/test_binding_reads.py
"""Tests for binding-aware schedule linkage reads in viewer services."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from ifc_viewer.services.colormap import build_colormap
from ifc_viewer.services.gap_analysis import build_gap_analysis
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory

_GREEN = "#22c55e"
_GRAY = "#94a3b8"


@pytest.mark.django_db
def test_colormap_schedule_status_binding_only_entity_is_linked():
    """Binding-only entity is green without Activity ID property."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-COLOR-BIND", properties={})
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )

    result = build_colormap(ifc_file, "schedule_status", project_id=str(project.pk))
    assert result["colormap"][entity.global_id] == _GREEN


@pytest.mark.django_db
def test_colormap_schedule_status_property_only_still_linked():
    """Activity ID property still marks entity linked when no binding exists."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(
        ifc_file=ifc_file,
        global_id="GID-COLOR-PROP",
        properties={"Castor.Activity ID": "ACT-100"},
    )

    result = build_colormap(ifc_file, "schedule_status", project_id=str(project.pk))
    assert result["colormap"][entity.global_id] == _GREEN


@pytest.mark.django_db
def test_colormap_schedule_status_unlinked_entity_is_gray():
    """Entity with no binding and no Activity ID stays not linked."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-COLOR-UNLINKED", properties={})

    result = build_colormap(ifc_file, "schedule_status", project_id=str(project.pk))
    assert result["colormap"][entity.global_id] == _GRAY


@pytest.mark.django_db
def test_gap_analysis_counts_binding_only_as_linked():
    """Gap analysis linked count includes binding-only entities."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(
        ifc_file=ifc_file,
        global_id="GID-GAP-BIND",
        ifc_type="IfcWall",
        properties={},
    )
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )

    rows = build_gap_analysis(ifc_file, "element_type", project_id=str(project.pk))
    wall_row = next(r for r in rows if r["group"] == "IfcWall")
    assert wall_row["linked"] == 1
    assert wall_row["total"] == 1


@pytest.mark.django_db
def test_gap_analysis_property_only_still_linked():
    """Gap analysis still counts Activity ID property as linked."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcSlab",
        properties={"Pset.Activity ID": "ACT-200"},
    )

    rows = build_gap_analysis(ifc_file, "element_type", project_id=str(project.pk))
    slab_row = next(r for r in rows if r["group"] == "IfcSlab")
    assert slab_row["linked"] == 1
