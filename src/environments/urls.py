"""Project URL configuration."""

from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    # Project list and create
    path("", views.ProjectListView.as_view(), name="list"),
    path("create/", views.ProjectCreateView.as_view(), name="create"),

    # Project detail/tabs
    path("<uuid:pk>/", views.ProjectDetailView.as_view(), name="detail"),
    path("<uuid:pk>/modify/", views.ModifyView.as_view(), name="modify"),
    path("<uuid:pk>/conflicts/", views.ConflictsView.as_view(), name="conflicts"),
    path("<uuid:pk>/history/", views.HistoryView.as_view(), name="history"),

    # Chat session management
    path("<uuid:pk>/ask/", views.AskView.as_view(), name="ask"),
    path("<uuid:pk>/ask/<uuid:session_id>/", views.AskView.as_view(), name="ask_session"),
    path("<uuid:pk>/ask/<uuid:session_id>/delete/", views.DeleteSessionView.as_view(), name="delete_session"),
    path("<uuid:pk>/ask/<uuid:session_id>/rename/", views.RenameSessionView.as_view(), name="rename_session"),

    # File Uploads
    path("<uuid:pk>/upload/ifc/", views.UploadIFCView.as_view(), name="upload_ifc"),
    # path("<uuid:pk>/upload/document/", views.UploadDocumentView.as_view(), name="upload_document"),
    path("<uuid:pk>/processed/", views.FileProcessedView.as_view(), name="file_processed"),
    path("<uuid:pk>/upload/", views.FileUploadView.as_view(), name="upload"),

    # Project CRUD
    path("<uuid:pk>/edit/", views.ProjectUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/delete/", views.ProjectDeleteView.as_view(), name="delete"),

    # IFC CRUD
    path("ifc/<uuid:pk>/edit/", views.IFCFileUpdateView.as_view(), name="ifc_edit"),
    path("ifc/<uuid:pk>/delete/", views.IFCFileDeleteView.as_view(), name="ifc_delete"),

    # Document CRUD
    path("document/<uuid:pk>/edit/", views.DocumentUpdateView.as_view(), name="document_edit"),
    path("document/<uuid:pk>/delete/", views.DocumentDeleteView.as_view(), name="document_delete"),

# --- MODIFY TAB URLs ---
    path("<uuid:pk>/modify/", views.ModifyView.as_view(), name="modify"),
    # 👇 ADD THIS LINE 👇
    path("<uuid:pk>/modify/<uuid:session_id>/", views.ModifyView.as_view(), name="modify_session"),

    path("<uuid:pk>/conflicts/", views.ConflictsView.as_view(), name="conflicts"),
    path("<uuid:pk>/history/", views.HistoryView.as_view(), name="history"),

    # restore HISTORY
path("<uuid:pk>/history/restore/<uuid:commit_id>/", views.RestoreCommitView.as_view(), name="restore_commit"),
]