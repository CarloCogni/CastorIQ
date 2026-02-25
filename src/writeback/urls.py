"""Project URL configuration."""

from django.urls import path

from writeback import views

app_name = "writeback"

urlpatterns = [
    path("<uuid:pk>/modify/",
         views.ModifyView.as_view(),
         name="modify"),
    path("<uuid:pk>/modify/<uuid:session_id>/",
         views.ModifyView.as_view(),
         name="modify_session"),

    path("<uuid:pk>/conflicts/",
         views.ConflictsView.as_view(),
         name="conflicts"),

    path("<uuid:pk>/history/",
         views.HistoryView.as_view(),
         name="history"),
    path("<uuid:pk>/history/restore/<uuid:commit_id>/",
         views.RestoreCommitView.as_view(),
         name="restore_commit"),

]


