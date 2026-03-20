# documents/tests/conftest.py
"""Shared fixtures for documents tests."""

import pytest

from environments.tests.factories import ProjectFactory, UserFactory


@pytest.fixture
def user():
    """A user fixture built via factory."""
    return UserFactory()


@pytest.fixture
def project(user):
    """A project owned by the test user."""
    return ProjectFactory(owner=user)
