# facilities/tests/test_explore_data_feed.py
"""Phase B — IFC data feeding into the Explore iframe.

Covers the three read-only JSON endpoints (floors / rooms / catalog)
plus their backing services. No model writes — the floor / room
descriptors are derived from existing IFC spatial elements.
"""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.services.explore_catalog_service import build_table_catalog
from facilities.services.explore_spatial_service import (
    list_floors_for_project,
    list_rooms_for_floor,
)
from facilities.tests.factories import FacilityAssetFactory, WorkOrderFactory
from ifc_processor.models import IFCSpatialElement
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)

pytestmark = pytest.mark.django_db


def _make_storey(ifc_file, name: str, props: dict | None = None) -> IFCSpatialElement:
    entity = IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcBuildingStorey",
        name=name,
        properties=props or {},
    )
    return IFCSpatialElementFactory(
        ifc_file=ifc_file,
        entity=entity,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
    )


def _make_space(ifc_file, parent_storey, name: str, props: dict | None = None) -> IFCSpatialElement:
    entity = IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcSpace",
        name=name,
        properties=props or {},
    )
    return IFCSpatialElementFactory(
        ifc_file=ifc_file,
        entity=entity,
        parent=parent_storey,
        spatial_type=IFCSpatialElement.SpatialType.SPACE,
    )


class TestListFloorsForProject:
    def test_returns_only_storeys_for_this_project(self):
        project = ProjectFactory()
        other_project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        other_ifc_file = IFCFileFactory(project=other_project)
        _make_storey(ifc_file, "Level 1")
        _make_storey(ifc_file, "Level 2")
        _make_storey(other_ifc_file, "Other building")

        floors = list_floors_for_project(project)

        assert len(floors) == 2
        names = {f["name"] for f in floors}
        assert names == {"Level 1", "Level 2"}

    def test_floor_descriptor_shape(self):
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        storey = _make_storey(ifc_file, "Level 1")

        floors = list_floors_for_project(project)

        assert len(floors) == 1
        floor = floors[0]
        assert floor["id"] == str(storey.pk)
        assert floor["name"] == "Level 1"
        assert floor["planType"] == "image"
        assert floor["plan"] is None  # no ExploreFloorPlan persisted
        assert floor["rooms"] == []

    def test_excludes_non_storey_spatial_elements(self):
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        # A building (not a storey)
        building_entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBuilding")
        IFCSpatialElementFactory(
            ifc_file=ifc_file,
            entity=building_entity,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING,
        )

        floors = list_floors_for_project(project)

        assert floors == []


class TestListRoomsForFloor:
    def test_returns_child_spaces_with_props(self):
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        storey = _make_storey(ifc_file, "Level 1")
        _make_space(
            ifc_file,
            storey,
            "Office 4B",
            props={"number": "4B", "department": "TechSpace"},
        )
        _make_space(ifc_file, storey, "Office 4A", props={"number": "4A"})

        rooms = list_rooms_for_floor(storey)

        assert len(rooms) == 2
        by_name = {r["name"]: r for r in rooms}
        assert by_name["Office 4B"]["ifcType"] == "IfcSpace"
        assert by_name["Office 4B"]["props"]["number"] == "4B"
        assert by_name["Office 4B"]["props"]["department"] == "TechSpace"
        # Unknown prop keys are dropped, missing ones don't appear.
        assert "department" not in by_name["Office 4A"]["props"]


class TestBuildTableCatalog:
    def test_catalog_has_default_tables(self):
        project = ProjectFactory()
        catalog = build_table_catalog(project)
        assert set(catalog.keys()) == {"assets", "work", "permits", "requests"}
        # Grouped by Facilities module name for the "add table" picker.
        assert catalog["assets"]["group"] == "Assets"
        assert catalog["work"]["group"] == "Work"
        assert catalog["permits"]["group"] == "Permits"
        assert catalog["requests"]["group"] == "Requests"

    def test_assets_table_filters_by_project(self):
        project = ProjectFactory()
        other_project = ProjectFactory()
        FacilityAssetFactory(project=project)
        FacilityAssetFactory(project=other_project)

        catalog = build_table_catalog(project)

        assert len(catalog["assets"]["rows"]) == 1

    def test_work_table_only_includes_open_status(self):
        project = ProjectFactory()
        WorkOrderFactory(project=project)  # DRAFT — open
        catalog = build_table_catalog(project)
        # DRAFT is in KANBAN_STATUSES per workorder_service definition.
        assert len(catalog["work"]["rows"]) == 1
        assert catalog["work"]["rows"][0]["title"]


class TestExploreApiAccess:
    def test_floors_endpoint_requires_project_access(self, client):
        project = ProjectFactory()
        outsider = UserFactory()
        client.force_login(outsider)
        response = client.get(f"/{project.pk}/facilities/explore/floors/")
        assert response.status_code in (302, 403, 404)

    def test_floors_endpoint_returns_json(self, client):
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.get(f"/{project.pk}/facilities/explore/floors/")
        assert response.status_code == 200
        body = response.json()
        assert "floors" in body
        assert isinstance(body["floors"], list)

    def test_catalog_endpoint_returns_tables(self, client):
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.get(f"/{project.pk}/facilities/explore/catalog/")
        assert response.status_code == 200
        body = response.json()
        assert "tables" in body
        assert set(body["tables"].keys()) == {"assets", "work", "permits", "requests"}
