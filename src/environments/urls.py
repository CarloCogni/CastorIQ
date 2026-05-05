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
        "<uuid:pk>/sessions/delete-all/",
        views.DeleteAllSessionsView.as_view(),
        name="delete_all_sessions",
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
    # People (access + functional roles)
    path("<uuid:pk>/people/", views.PeopleView.as_view(), name="people"),
    path(
        "<uuid:pk>/people/users/search/",
        views.UserSearchView.as_view(),
        name="user_search",
    ),
    path("<uuid:pk>/people/add/", views.MemberAddView.as_view(), name="member_add"),
    path(
        "<uuid:pk>/people/<int:user_id>/change/",
        views.MemberChangePermissionView.as_view(),
        name="member_change_permission",
    ),
    path(
        "<uuid:pk>/people/<int:user_id>/remove/",
        views.MemberRemoveView.as_view(),
        name="member_remove",
    ),
    path(
        "<uuid:pk>/people/transfer/",
        views.TransferOwnershipView.as_view(),
        name="transfer_ownership",
    ),
    path("<uuid:pk>/people/roles/add/", views.RoleAddView.as_view(), name="role_add"),
    path(
        "<uuid:pk>/people/roles/<uuid:role_id>/remove/",
        views.RoleRemoveView.as_view(),
        name="role_remove",
    ),
    # IFC CRUD
    path("ifc/<uuid:pk>/edit/", views.IFCFileUpdateView.as_view(), name="ifc_edit"),
    path("ifc/<uuid:pk>/reparse/", views.IFCReparseView.as_view(), name="ifc_reparse"),
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
    path(
        "<uuid:pk>/explore/ifc/<uuid:ifc_id>/export/",
        views.ExploreExportView.as_view(),
        name="explore_export",
    ),
    # Schedule
    path("<uuid:pk>/schedule/", views.ScheduleView.as_view(), name="schedule"),
    path(
        "<uuid:pk>/schedule/ifc/<uuid:ifc_id>/",
        views.ScheduleView.as_view(),
        name="schedule_ifc",
    ),
    path(
        "<uuid:pk>/schedule/ifc/<uuid:ifc_id>/table/",
        views.ScheduleTablePartial.as_view(),
        name="schedule_table",
    ),
    path(
        "<uuid:pk>/schedule/ifc/<uuid:ifc_id>/export/",
        views.ScheduleExportView.as_view(),
        name="schedule_export",
    ),
    path(
        "<uuid:pk>/schedule/ifc/<uuid:ifc_id>/export/excel/",
        views.ScheduleExcelExportView.as_view(),
        name="schedule_export_excel",
    ),
]
