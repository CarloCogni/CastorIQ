# ifc_viewer/urls.py
"""URL routes for the IFC 3D viewer app — web-ifc playback + 4D timeline."""

from django.urls import path

from . import views

app_name = "ifc_viewer"

urlpatterns = [
    path("projects/<uuid:pk>/", views.ViewerView.as_view(), name="viewer"),
    path(
        "projects/<uuid:pk>/fragments/",
        views.FragmentsCacheView.as_view(),
        name="viewer_fragments",
    ),
    path(
        "projects/<uuid:pk>/colormap/",
        views.ColormapView.as_view(),
        name="viewer_colormap",
    ),
    path(
        "projects/<uuid:pk>/gap-analysis/",
        views.GapAnalysisView.as_view(),
        name="viewer_gap_analysis",
    ),
    path(
        "projects/<uuid:pk>/entity-types/",
        views.EntityTypesView.as_view(),
        name="viewer_entity_types",
    ),
    path(
        "projects/<uuid:pk>/spatial-tree/",
        views.SpatialTreeView.as_view(),
        name="viewer_spatial_tree",
    ),
    path(
        "projects/<uuid:pk>/build-sequence/",
        views.BuildSequenceView.as_view(),
        name="viewer_build_sequence",
    ),
    path(
        "projects/<uuid:pk>/timeline/",
        views.TimelineView.as_view(),
        name="viewer_timeline",
    ),
    path(
        "projects/<uuid:pk>/embed/",
        views.ViewerEmbedView.as_view(),
        name="viewer_embed",
    ),
    path(
        "projects/<uuid:pk>/export-report/",
        views.ExportReportView.as_view(),
        name="export_report",
    ),
    path(
        "projects/<uuid:pk>/element/<str:global_id>/",
        views.ElementPropertiesView.as_view(),
        name="viewer_element_props",
    ),
]
