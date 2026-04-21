# facilities/urls.py
"""URL routes for the Facilities tab (7D FM)."""

from django.urls import path

from . import views

app_name = "facilities"

urlpatterns = [
    # Dashboard sub-tab (role-aware landing) — default Facilities entry.
    path("<uuid:pk>/facilities/", views.FacilitiesView.as_view(), name="tab"),
    path(
        "<uuid:pk>/facilities/role/switch/",
        views.RoleSwitchView.as_view(),
        name="role_switch",
    ),
    # Asset Register (M1).
    path(
        "<uuid:pk>/facilities/assets/",
        views.AssetListView.as_view(),
        name="assets_list",
    ),
    path(
        "<uuid:pk>/facilities/assets/promote/",
        views.AssetPromoteView.as_view(),
        name="assets_promote",
    ),
    path(
        "<uuid:pk>/facilities/assets/bulk/",
        views.AssetBulkView.as_view(),
        name="assets_bulk",
    ),
    path(
        "<uuid:pk>/facilities/assets/import/",
        views.AssetCSVImportView.as_view(),
        name="assets_import",
    ),
    path(
        "<uuid:pk>/facilities/assets/<uuid:asset_pk>/",
        views.AssetDetailView.as_view(),
        name="assets_detail",
    ),
    path(
        "<uuid:pk>/facilities/assets/<uuid:asset_pk>/update/",
        views.AssetUpdateView.as_view(),
        name="assets_update",
    ),
]
