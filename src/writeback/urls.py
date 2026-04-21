"""Project URL configuration."""

from django.urls import path

from writeback import views

app_name = "writeback"

urlpatterns = [
    path("<uuid:pk>/modify/", views.ModifyView.as_view(), name="modify"),
    path("<uuid:pk>/modify/<uuid:session_id>/", views.ModifyView.as_view(), name="modify_session"),
    path("<uuid:pk>/conflicts/", views.ConflictsView.as_view(), name="conflicts"),
    path("<uuid:pk>/scan/", views.RunScanView.as_view(), name="run_scan"),
    path(
        "<uuid:pk>/conflicts/<uuid:conflict_id>/dismiss/",
        views.DismissConflictView.as_view(),
        name="dismiss_conflict",
    ),
    path("<uuid:pk>/conflicts/bulk-dismiss/", views.BulkDismissView.as_view(), name="bulk_dismiss"),
    path(
        "<uuid:pk>/conflicts/<uuid:conflict_id>/ignore/",
        views.IgnoreConflictView.as_view(),
        name="ignore_conflict",
    ),
    path(
        "<uuid:pk>/conflicts/bulk-ignore/",
        views.BulkIgnoreView.as_view(),
        name="bulk_ignore_conflicts",
    ),
    path(
        "<uuid:pk>/conflicts/bulk-resolve/",
        views.BulkResolveView.as_view(),
        name="bulk_resolve_conflicts",
    ),
    path(
        "<uuid:pk>/conflicts/delete-all/",
        views.DeleteAllConflictsView.as_view(),
        name="delete_all_conflicts",
    ),
    path(
        "<uuid:pk>/conflicts/<uuid:conflict_id>/restore/",
        views.RestoreConflictView.as_view(),
        name="restore_conflict",
    ),
    path(
        "<uuid:pk>/data-issues/<uuid:issue_id>/dismiss/",
        views.DismissDataIssueView.as_view(),
        name="dismiss_data_issue",
    ),
    path(
        "<uuid:pk>/data-issues/<uuid:issue_id>/restore/",
        views.RestoreDataIssueView.as_view(),
        name="restore_data_issue",
    ),
    path(
        "<uuid:pk>/data-issues/bulk-dismiss/",
        views.BulkDismissDataIssuesView.as_view(),
        name="bulk_dismiss_data_issues",
    ),
    path("<uuid:pk>/history/", views.HistoryView.as_view(), name="history"),
    path(
        "<uuid:pk>/history/restore/<uuid:commit_id>/",
        views.RestoreCommitView.as_view(),
        name="restore_commit",
    ),
]
