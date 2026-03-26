# environments/urls.py
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
    # Chat session management
    path("<uuid:pk>/ask/", views.AskView.as_view(), name="ask"),
    path("<uuid:pk>/ask/<uuid:session_id>/", views.AskView.as_view(), name="ask_session"),
    path(
        "<uuid:pk>/ask/<uuid:session_id>/messages/",
        views.AskMessagesView.as_view(),
        name="ask_session_messages",
    ),
    path(
        "<uuid:pk>/ask/<uuid:session_id>/delete/",
        views.DeleteSessionView.as_view(),
        name="delete_session",
    ),
    path(
        "<uuid:pk>/ask/<uuid:session_id>/rename/",
        views.RenameSessionView.as_view(),
        name="rename_session",
    ),
    # File Uploads
    path("<uuid:pk>/processed/", views.FileProcessedView.as_view(), name="file_processed"),
    path("<uuid:pk>/upload/", views.FileUploadView.as_view(), name="upload"),
    # Project CRUD
    path("<uuid:pk>/edit/", views.ProjectUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/delete/", views.ProjectDeleteView.as_view(), name="delete"),
    # IFC CRUD
    path("ifc/<uuid:pk>/edit/", views.IFCFileUpdateView.as_view(), name="ifc_edit"),
    path("ifc/<uuid:pk>/delete/", views.IFCFileDeleteView.as_view(), name="ifc_delete"),
    path("ifc/<uuid:pk>/convert/", views.IFCSchemaConvertView.as_view(), name="ifc_convert"),
    # Document CRUD
    path("document/<uuid:pk>/edit/", views.DocumentUpdateView.as_view(), name="document_edit"),
    path("document/<uuid:pk>/delete/", views.DocumentDeleteView.as_view(), name="document_delete"),
    path("document/<uuid:pk>/ocr/", views.DocumentOCRView.as_view(), name="document_ocr"),
    # IFC Explorer
    path("<uuid:pk>/explore/", views.ExploreView.as_view(), name="explore"),
    path("<uuid:pk>/explore/ifc/<uuid:ifc_id>/", views.ExploreView.as_view(), name="explore_ifc"),
    path(
        "<uuid:pk>/explore/ifc/<uuid:ifc_id>/tree/",
        views.ExploreTreePartial.as_view(),
        name="explore_tree",
    ),
    path(
        "<uuid:pk>/explore/ifc/<uuid:ifc_id>/entities/",
        views.ExploreEntitiesPartial.as_view(),
        name="explore_entities",
    ),
    path(
        "<uuid:pk>/explore/ifc/<uuid:ifc_id>/entity/<uuid:entity_id>/",
        views.ExploreEntityDetailPartial.as_view(),
        name="explore_entity_detail",
    ),
]
