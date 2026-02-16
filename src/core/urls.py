"""Core URL configuration."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    # Test error logging (REMOVE IN PRODUCTION)
    path("TestErrorABCD/", views.test_error, name="test_error"),
    path("loader-gallery/", views.loader_gallery, name="loader_gallery"),
    path("login-matrix/", views.test_landing_page, name="login-matrix"),
]