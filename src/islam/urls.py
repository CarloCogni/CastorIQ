# islam/urls.py
"""Root URL dispatcher for the islam 4D Insights module.

Mounted at /islam/ by config/urls.py.
All three sub-apps share the 'islam' namespace.
"""

from django.urls import path

from islam.ifc_insights import views as insights_views
from islam.ifc_viewer import views as viewer_views
from islam.intelligence import views as intelligence_views
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
        "projects/<uuid:pk>/viewer/fragments/",
        viewer_views.FragmentsCacheView.as_view(),
        name="viewer_fragments",
    ),
    path(
        "projects/<uuid:pk>/viewer/colormap/",
        viewer_views.ColormapView.as_view(),
        name="viewer_colormap",
    ),
    path(
        "projects/<uuid:pk>/viewer/gap_analysis/",
        viewer_views.GapAnalysisView.as_view(),
        name="viewer_gap_analysis",
    ),
    path(
        "projects/<uuid:pk>/viewer/build_sequence/",
        viewer_views.BuildSequenceView.as_view(),
        name="viewer_build_sequence",
    ),
    path(
        "projects/<uuid:pk>/viewer/timeline/",
        viewer_views.TimelineView.as_view(),
        name="viewer_timeline",
    ),
    path(
        "projects/<uuid:pk>/viewer/embed/",
        viewer_views.ViewerEmbedView.as_view(),
        name="viewer_embed",
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
        "projects/<uuid:pk>/schedule/preview/",
        scheduling_views.SchedulePreviewView.as_view(),
        name="schedule_preview",
    ),
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
        "projects/<uuid:pk>/schedule/clear/",
        scheduling_views.ScheduleClearView.as_view(),
        name="schedule_clear",
    ),
    path(
        "projects/<uuid:pk>/schedule/source/<uuid:source_pk>/delete/",
        scheduling_views.ScheduleSourceDeleteView.as_view(),
        name="schedule_source_delete",
    ),
    path(
        "projects/<uuid:pk>/schedule/wbs-heatmap/",
        scheduling_views.WBSHeatmapView.as_view(),
        name="wbs_heatmap",
    ),
    path(
        "projects/<uuid:pk>/schedule/delay-distribution/",
        scheduling_views.DelayDistributionView.as_view(),
        name="delay_distribution",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/auto/",
        scheduling_views.LinkAutoView.as_view(),
        name="schedule_link_auto",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/smart/",
        scheduling_views.AutoLinkView.as_view(),
        name="schedule_link_smart",
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
        "projects/<uuid:pk>/schedule/tasks/<uuid:task_pk>/actual-dates/",
        scheduling_views.TaskActualDateView.as_view(),
        name="task_actual_dates",
    ),
    path(
        "projects/<uuid:pk>/schedule/gantt-data/",
        scheduling_views.GanttDataView.as_view(),
        name="gantt_data",
    ),
    path(
        "projects/<uuid:pk>/schedule/task-detail/<uuid:task_pk>/",
        scheduling_views.TaskDetailView.as_view(),
        name="task_detail",
    ),
    path(
        "projects/<uuid:pk>/schedule/critical-path/",
        scheduling_views.CriticalPathView.as_view(),
        name="critical_path",
    ),
    path(
        "projects/<uuid:pk>/schedule/evm/",
        scheduling_views.EVMDataView.as_view(),
        name="evm_data",
    ),
    path(
        "projects/<uuid:pk>/schedule/lookahead/",
        scheduling_views.LookaheadDataView.as_view(),
        name="lookahead_data",
    ),
    path(
        "projects/<uuid:pk>/schedule/mapping/submit/",
        scheduling_views.MappingSubmitView.as_view(),
        name="schedule_mapping_submit",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/embed/",
        scheduling_views.EmbedLinkView.as_view(),
        name="schedule_embed_link",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/accept/<uuid:feedback_pk>/",
        scheduling_views.LinkAcceptView.as_view(),
        name="link_accept",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/reject/<uuid:feedback_pk>/",
        scheduling_views.LinkRejectView.as_view(),
        name="link_reject",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/change/<uuid:feedback_pk>/",
        scheduling_views.LinkChangeView.as_view(),
        name="link_change",
    ),
    path(
        "projects/<uuid:pk>/schedule/link/search/",
        scheduling_views.LinkSearchView.as_view(),
        name="link_search",
    ),
    # ------------------------------------------------------------------ #
    # Link Review — binding review tab                                     #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/schedule/review/",
        scheduling_views.LinkReviewView.as_view(),
        name="review",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/<uuid:binding_pk>/accept/",
        scheduling_views.BindingAcceptView.as_view(),
        name="binding_accept",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/<uuid:binding_pk>/remove/",
        scheduling_views.BindingRemoveView.as_view(),
        name="binding_remove",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/bulk-accept/",
        scheduling_views.BulkAcceptView.as_view(),
        name="binding_bulk_accept",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/export/",
        scheduling_views.BindingExportView.as_view(),
        name="binding_export",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/add/",
        scheduling_views.BindingAddView.as_view(),
        name="binding_add",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/search/",
        scheduling_views.BindingSearchView.as_view(),
        name="binding_search",
    ),
    path(
        "projects/<uuid:pk>/schedule/review/task/<uuid:task_pk>/toggle-physical/",
        scheduling_views.TaskToggleNonPhysicalView.as_view(),
        name="task_toggle_physical",
    ),
    # ------------------------------------------------------------------ #
    # Level Panel                                                         #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/levels/",
        insights_views.LevelsView.as_view(),
        name="levels",
    ),
    path(
        "projects/<uuid:pk>/levels/suggest/",
        insights_views.LevelSuggestView.as_view(),
        name="level_suggest",
    ),
    path(
        "projects/<uuid:pk>/levels/add/",
        insights_views.LevelAddView.as_view(),
        name="level_add",
    ),
    path(
        "projects/<uuid:pk>/levels/<uuid:level_pk>/edit/",
        insights_views.LevelEditView.as_view(),
        name="level_edit",
    ),
    path(
        "projects/<uuid:pk>/levels/<uuid:level_pk>/delete/",
        insights_views.LevelDeleteView.as_view(),
        name="level_delete",
    ),
    path(
        "projects/<uuid:pk>/levels/apply/",
        insights_views.LevelApplyView.as_view(),
        name="level_apply",
    ),
    # ------------------------------------------------------------------ #
    # IFC Issues tab                                                      #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/ifc-issues/",
        insights_views.IssuesView.as_view(),
        name="ifc_issues",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/count/",
        insights_views.IssuesCountView.as_view(),
        name="issues_count",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/missing-activity-id/",
        insights_views.IssuesMissingActivityView.as_view(),
        name="issues_missing_activity",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/missing-cost/",
        insights_views.IssuesMissingCostView.as_view(),
        name="issues_missing_cost",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/activity-audit/",
        insights_views.IssuesActivityAuditView.as_view(),
        name="issues_activity_audit",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/levels-health/",
        insights_views.IssuesLevelsHealthView.as_view(),
        name="issues_levels_health",
    ),
    path(
        "projects/<uuid:pk>/insights/issues/export/",
        insights_views.IssuesExportView.as_view(),
        name="issues_export",
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
        "projects/<uuid:pk>/insights/progress-mode/",
        insights_views.ProgressModeView.as_view(),
        name="insights_progress_mode",
    ),
    path(
        "projects/<uuid:pk>/insights/progress-ring/",
        insights_views.ProgressRingView.as_view(),
        name="insights_progress_ring",
    ),
    path(
        "projects/<uuid:pk>/insights/breakdown/<str:breakdown_type>/",
        insights_views.InsightsBreakdownView.as_view(),
        name="insights_breakdown",
    ),
    path(
        "projects/<uuid:pk>/insights/export/",
        insights_views.InsightsExportView.as_view(),
        name="insights_export",
    ),
    # ------------------------------------------------------------------ #
    # QTO (Quantity Take-Off)                                             #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/qto/",
        insights_views.QTOView.as_view(),
        name="qto",
    ),
    path(
        "projects/<uuid:pk>/qto/data/",
        insights_views.QTODataView.as_view(),
        name="qto_data",
    ),
    path(
        "projects/<uuid:pk>/qto/recompute/",
        insights_views.QTORecomputeView.as_view(),
        name="qto_recompute",
    ),
    path(
        "projects/<uuid:pk>/qto/unit-cost/",
        insights_views.QTOUnitCostUpdateView.as_view(),
        name="qto_unit_cost",
    ),
    path(
        "projects/<uuid:pk>/qto/export/",
        insights_views.QTOExportView.as_view(),
        name="qto_export",
    ),
    # ------------------------------------------------------------------ #
    # Intelligence tab                                                     #
    # ------------------------------------------------------------------ #
    path(
        "projects/<uuid:pk>/schedule/intelligence/status/",
        intelligence_views.IntelligenceStatusView.as_view(),
        name="intelligence_status",
    ),
    path(
        "projects/<uuid:pk>/schedule/intelligence/embed/",
        intelligence_views.IntelligenceEmbedView.as_view(),
        name="intelligence_embed",
    ),
    path(
        "projects/<uuid:pk>/schedule/intelligence/ask/",
        intelligence_views.IntelligenceAskView.as_view(),
        name="intelligence_ask",
    ),
]
