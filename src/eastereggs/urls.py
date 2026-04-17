# eastereggs/urls.py
"""URL configuration for the eastereggs gallery."""

from django.urls import path

from . import views

app_name = "eastereggs"

urlpatterns = [
    path("", views.GalleryView.as_view(), name="gallery"),
    # Castor Slug — two entry points: standalone and project-linked
    path(
        "castor-slug/",
        views.CastorSlugView.as_view(),
        name="castor_slug_standalone",
    ),
    path(
        "castor-slug/<uuid:project_id>/",
        views.CastorSlugView.as_view(),
        name="castor_slug",
    ),
]
