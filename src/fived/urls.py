# fived/urls.py
"""URL routes for 5D preparation snapshot surfaces."""

from django.urls import path

from fived import views

app_name = "fived"

urlpatterns = [
    path(
        "projects/<uuid:pk>/versions/<uuid:version_id>/schema-insight/",
        views.SchemaQuantityInsightReportView.as_view(),
        name="schema_quantity_insight_report",
    ),
]
