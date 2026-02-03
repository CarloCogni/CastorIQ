"""Pytest configuration and fixtures."""

import pytest
from django.contrib.auth.models import User


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123"
    )


@pytest.fixture
def authenticated_client(client, user):
    """Return an authenticated test client."""
    client.login(username="testuser", password="testpass123")
    return client
