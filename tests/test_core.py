"""Tests for core app."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_check(client):
    """Test the health check endpoint."""
    response = client.get(reverse("health_check"))
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
