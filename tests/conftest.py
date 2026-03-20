# tests/conftest.py
"""
Root-level pytest fixtures shared across all apps.

Provides: authenticated users, projects, IFC files, and common mocks.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

User = get_user_model()


# ── Users ────────────────────────────────────────────────────


@pytest.fixture
def user(db):
    """A basic authenticated user."""
    return User.objects.create_user(
        username="testuser",
        email="testuser@test.com",
        password="testpass123",
    )


@pytest.fixture
def other_user(db):
    """A second user for permission / multi-user tests."""
    return User.objects.create_user(
        username="otheruser",
        email="other@test.com",
        password="testpass123",
    )


@pytest.fixture
def auth_client(client, user):
    """Django test client already logged in as `user`."""
    client.login(username="testuser", password="testpass123")
    return client


# ── Projects ─────────────────────────────────────────────────


@pytest.fixture
def project(user):
    """A project owned by `user`."""
    from environments.models import Project

    return Project.objects.create(
        name="Test Project",
        owner=user,
    )


# ── IFC Files ────────────────────────────────────────────────


@pytest.fixture
def ifc_file(project, user):
    """An IFC file belonging to `project`, uploaded by `user`."""
    from ifc_processor.models import IFCFile

    fake_file = SimpleUploadedFile(
        "test_model.ifc",
        b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;",
        content_type="application/octet-stream",
    )

    return IFCFile.objects.create(
        project=project,
        name="test_model.ifc",
        file=fake_file,
        file_hash="abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
        status="completed",
    )


@pytest.fixture
def ifc_entities(ifc_file):
    """A small set of IFC entities for testing filters and context building."""
    from ifc_processor.models import IFCEntity

    entities = []
    for i in range(5):
        entities.append(
            IFCEntity.objects.create(
                ifc_file=ifc_file,
                ifc_type="IfcWall",
                name=f"Wall-{i + 1:03d}",
                global_id=f"GUID-WALL-{i + 1:03d}",
                properties={
                    "Pset_WallCommon.IsExternal": True,
                    "Pset_WallCommon.FireRating": "EI60",
                    "Pset_WallCommon.LoadBearing": False,
                },
            )
        )
    # Add a door for cross-type tests
    entities.append(
        IFCEntity.objects.create(
            ifc_file=ifc_file,
            ifc_type="IfcDoor",
            name="D-001",
            global_id="GUID-DOOR-001",
            properties={
                "Pset_DoorCommon.IsExternal": False,
                "Pset_DoorCommon.FireRating": "EI30",
            },
        )
    )
    return entities


# ── LLM Mock ─────────────────────────────────────────────────


@pytest.fixture
def mock_llm_response():
    """
    Factory fixture: returns a helper that patches the LLM to return
    a specific string content.

    Usage:
        def test_something(mock_llm_response):
            mock = mock_llm_response('{"tier": 1, ...}')
            # now any LLM.invoke() call returns that string
    """
    from unittest.mock import MagicMock, patch

    def _make(content: str):
        mock_response = MagicMock()
        mock_response.content = content
        patcher = patch("core.llm.get_llm")
        mock_get_llm = patcher.start()
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm_instance
        return mock_llm_instance, patcher

    return _make
