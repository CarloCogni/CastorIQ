# takeoff/urls.py
"""URL routes for the Quantity Take-Off app — QTO dashboard + unit costs + export."""

from django.urls import path

from . import views

app_name = "takeoff"

urlpatterns = [
    path("projects/<uuid:pk>/", views.QTOView.as_view(), name="qto"),
    path("projects/<uuid:pk>/data/", views.QTODataView.as_view(), name="qto_data"),
    path(
        "projects/<uuid:pk>/recompute/",
        views.QTORecomputeView.as_view(),
        name="qto_recompute",
    ),
    path(
        "projects/<uuid:pk>/unit-cost/",
        views.QTOUnitCostUpdateView.as_view(),
        name="qto_unit_cost",
    ),
    path(
        "projects/<uuid:pk>/export/",
        views.QTOExportView.as_view(),
        name="qto_export",
    ),
]
