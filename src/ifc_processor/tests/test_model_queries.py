# ifc_processor/tests/test_model_queries.py
"""Tests for ModelQueryService — the deterministic Ask executors."""

from __future__ import annotations

import pytest

from ifc_processor.services.model_queries import ModelQueryService
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def ifc_file():
    return IFCFileFactory(schema_version="IFC4", project_units={"AREAUNIT": "m²"})


def test_count_entities_expands_subtypes(ifc_file):
    """IfcWall count covers IfcWallStandardCase via the schema hierarchy."""
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWallStandardCase")

    result = ModelQueryService(ifc_file.project).count_entities("IfcWall")
    assert result["value"] == 2


def test_count_excludes_incomplete_files(ifc_file):
    """Entities of failed/pending files must not leak into counts."""
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcDoor")
    pending = IFCFileFactory(project=ifc_file.project, status="pending")
    IFCEntityFactory(ifc_file=pending, ifc_type="IfcDoor")

    result = ModelQueryService(ifc_file.project).count_entities("IfcDoor")
    assert result["value"] == 1


def test_total_wall_area_uses_net_then_gross(ifc_file):
    """One wall carries Net, one only Gross — both contribute."""
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={"Qto_WallBaseQuantities.NetSideArea": 10.0},
    )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={"Qto_WallBaseQuantities.GrossSideArea": 5.5},
    )

    result = ModelQueryService(ifc_file.project).total_wall_area()
    assert result["value"] == 15.5
    assert result["unit"] == "m²"
    assert "2/2" in result["provenance"]


def test_total_wall_area_without_quantities_is_empty(ifc_file):
    """No Qto data → value None so the caller falls back to RAG."""
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", properties={})
    result = ModelQueryService(ifc_file.project).total_wall_area()
    assert result["value"] is None


def test_window_wall_ratio_prefers_external_walls(ifc_file):
    """WWR uses IsExternal walls and says so in the provenance."""
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={
            "Pset_WallCommon.IsExternal": True,
            "Qto_WallBaseQuantities.NetSideArea": 90.0,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={
            "Pset_WallCommon.IsExternal": False,
            "Qto_WallBaseQuantities.NetSideArea": 500.0,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWindow",
        properties={"Qto_WindowBaseQuantities.Area": 10.0},
    )

    result = ModelQueryService(ifc_file.project).window_wall_ratio()
    assert result["value"] == 0.1  # 10 / (10 + 90) — internal wall excluded
    assert "external walls" in result["provenance"]


def test_window_wall_ratio_falls_back_to_all_walls(ifc_file):
    """Without IsExternal flags, all walls are used and provenance flags it."""
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={"Qto_WallBaseQuantities.NetSideArea": 40.0},
    )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWindow",
        properties={"Qto_WindowBaseQuantities.Area": 10.0},
    )

    result = ModelQueryService(ifc_file.project).window_wall_ratio()
    assert result["value"] == 0.2
    assert "no IsExternal flags" in result["provenance"]


def test_window_area_falls_back_to_overall_dims_in_mm(ifc_file):
    """Windows without Qto areas use OverallWidth×OverallHeight (mm → m²)."""
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWall",
        properties={"Qto_WallBaseQuantities.NetSideArea": 98.0},
    )
    IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcWindow",
        properties={"OverallWidth": 1000.0, "OverallHeight": 2000.0},  # 2 m²
    )

    result = ModelQueryService(ifc_file.project).window_wall_ratio()
    assert result["value"] == 0.02  # 2 / (2 + 98)


def test_schema_version_reads_latest_file(ifc_file):
    """Schema answer comes straight from the file header field."""
    result = ModelQueryService(ifc_file.project).schema_version()
    assert result["value"] == "IFC4"
    assert result["provenance"] == ifc_file.name


def test_type_breakdown_orders_by_count(ifc_file):
    """Breakdown rows arrive most-numerous first."""
    IFCEntityFactory.create_batch(3, ifc_file=ifc_file, ifc_type="IfcWall")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcDoor")

    result = ModelQueryService(ifc_file.project).type_breakdown()
    assert result["value"] == 4
    assert result["rows"][0] == {"ifc_type": "IfcWall", "count": 3}
