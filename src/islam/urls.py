# islam/urls.py
"""Root URL dispatcher for the islam 4D Insights module.

Mounted at /islam/ by config/urls.py.
All three sub-apps share the 'islam' namespace.
"""

from django.urls import path

from islam.ifc_insights import views as insights_views
from islam.ifc_viewer import views as viewer_views
from islam.scheduling import views as scheduling_views

app_name = "islam"

urlpatterns = [
    # ------------------------------------------------------------------ #
    # Main sub-tab entry points (each renders project_detail.html shell)  #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/viewer/",
        viewer_views.ViewerView.as_view(),
        name="viewer",
    ),
    path(
        "projects/<uuid:pk>/schedule/",
        scheduling_views.ScheduleView.as_view(),
        name="schedule",
    ),
    path(
        "projects/<uuid:pk>/insights/",
        insights_views.InsightsView.as_view(),
        name="insights",
    ),
    # ------------------------------------------------------------------ #
    # Scheduling HTMX endpoints                                           #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/schedule/upload/",
        scheduling_views.TaskUploadView.as_view(),
        name="schedule_upload",
    ),
    path(
        "projects/<uuid:pk>/schedule/save/",
        scheduling_views.TaskSaveView.as_view(),
        name="schedule_save",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/auto/",
        scheduling_views.LinkAutoView.as_view(),
        name="schedule_link_auto",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/param/",
        scheduling_views.LinkParamView.as_view(),
        name="schedule_link_param",
    ),
    path(
        "projects/<uuid:pk>/schedule/tasks/",
        scheduling_views.TaskListPartialView.as_view(),
        name="task_list_partial",
    ),
    path(
        "projects/<uuid:pk>/schedule/tasks/<uuid:task_pk>/delete/",
        scheduling_views.TaskDeleteView.as_view(),
        name="task_delete",
    ),
    path(
        "projects/<uuid:pk>/schedule/gantt-data/",
        scheduling_views.GanttDataView.as_view(),
        name="gantt_data",
    ),
    # ------------------------------------------------------------------ #
    # IFC Insights HTMX endpoints                                         #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/insights/rerun/",
        insights_views.InsightsRerunView.as_view(),
        name="insights_rerun",
    ),
    path(
        "projects/<uuid:pk>/insights/export/",
        insights_views.InsightsExportView.as_view(),
        name="insights_export",
    ),
]
