"""Core URL configuration."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    # Test error logging (REMOVE IN PRODUCTION)
    path("TestErrorABCD/", views.test_error, name="test_error"),
]