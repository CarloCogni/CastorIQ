# writeback/tests/conftest.py
"""
Shared fixtures for writeback tests.

Imports factories from ifc_processor and environments to build
the standard test data: user → project → ifc_file → entities.
"""

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.fixture
def user():
    """A user fixture built via factory."""
    return UserFactory()


@pytest.fixture
def project(user):
    """A project owned by the test user."""
    return ProjectFactory(owner=user)


@pytest.fixture
def ifc_file(project):
    """A completed IFC file in the test project."""
    return IFCFileFactory(project=project, status="completed")


@pytest.fixture
def wall_entities(ifc_file):
    """5 IfcWall entities with standard Pset_WallCommon properties."""
    return [
        IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcWall",
            name=f"Wall-{i:03d}",
            global_id=f"GUID-WB-{i:06d}",
            properties={
                "Pset_WallCommon.IsExternal": True,
                "Pset_WallCommon.FireRating": "EI60",
                "Pset_WallCommon.LoadBearing": False,
            },
        )
        for i in range(5)
    ]


@pytest.fixture
def door_entity(ifc_file):
    """A single IfcDoor entity."""
    return IFCEntityFactory(
        ifc_file=ifc_file,
        ifc_type="IfcDoor",
        name="D-001",
        global_id="GUID-DOOR-0001",
        properties={
            "Pset_DoorCommon.IsExternal": False,
            "Pset_DoorCommon.FireRating": "EI30",
        },
    )
