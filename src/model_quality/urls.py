# model_quality/urls.py
"""URL routes for the Model Quality app — IFC checks + Levels manager + Issues panel."""

from django.urls import path

from . import views

app_name = "model_quality"

urlpatterns = [
    # Main panel (insights overview)
    path("projects/<uuid:pk>/", views.InsightsView.as_view(), name="insights"),
    path(
        "projects/<uuid:pk>/rerun/",
        views.InsightsRerunView.as_view(),
        name="insights_rerun",
    ),
    path(
        "projects/<uuid:pk>/progress-mode/",
        views.ProgressModeView.as_view(),
        name="insights_progress_mode",
    ),
    path(
        "projects/<uuid:pk>/progress-ring/",
        views.ProgressRingView.as_view(),
        name="insights_progress_ring",
    ),
    path(
        "projects/<uuid:pk>/breakdown/<str:breakdown_type>/",
        views.InsightsBreakdownView.as_view(),
        name="insights_breakdown",
    ),
    path(
        "projects/<uuid:pk>/export/",
        views.InsightsExportView.as_view(),
        name="insights_export",
    ),
    # Levels manager
    path("projects/<uuid:pk>/levels/", views.LevelsView.as_view(), name="levels"),
    path(
        "projects/<uuid:pk>/levels/suggest/",
        views.LevelSuggestView.as_view(),
        name="level_suggest",
    ),
    path(
        "projects/<uuid:pk>/levels/add/",
        views.LevelAddView.as_view(),
        name="level_add",
    ),
    path(
        "projects/<uuid:pk>/levels/<uuid:level_pk>/edit/",
        views.LevelEditView.as_view(),
        name="level_edit",
    ),
    path(
        "projects/<uuid:pk>/levels/<uuid:level_pk>/delete/",
        views.LevelDeleteView.as_view(),
        name="level_delete",
    ),
    path(
        "projects/<uuid:pk>/levels/apply/",
        views.LevelApplyView.as_view(),
        name="level_apply",
    ),
    # IFC Issues
    path(
        "projects/<uuid:pk>/ifc-issues/",
        views.IssuesView.as_view(),
        name="ifc_issues",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/count/",
        views.IssuesCountView.as_view(),
        name="issues_count",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/missing-activity-id/",
        views.IssuesMissingActivityView.as_view(),
        name="issues_missing_activity",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/missing-cost/",
        views.IssuesMissingCostView.as_view(),
        name="issues_missing_cost",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/activity-audit/",
        views.IssuesActivityAuditView.as_view(),
        name="issues_activity_audit",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/levels-health/",
        views.IssuesLevelsHealthView.as_view(),
        name="issues_levels_health",
    ),
    path(
        "projects/<uuid:pk>/ifc-issues/export/",
        views.IssuesExportView.as_view(),
        name="issues_export",
    ),
]
