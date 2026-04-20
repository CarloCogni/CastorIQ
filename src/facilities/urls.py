# facilities/urls.py
"""URL routes for the Facilities tab (7D FM)."""

from django.urls import path

from . import views

app_name = "facilities"

urlpatterns = [
    path("<uuid:pk>/facilities/", views.FacilitiesView.as_view(), name="tab"),
    path(
        "<uuid:pk>/facilities/role/switch/",
        views.RoleSwitchView.as_view(),
        name="role_switch",
    ),
]
