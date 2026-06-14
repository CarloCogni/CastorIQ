# scheduling/admin.py
"""Django admin for schedule source version foundation (read-focused)."""

from django.contrib import admin

from scheduling.models import ScheduleActivity, ScheduleImportRun, ScheduleSourceVersion


@admin.register(ScheduleActivity)
class ScheduleActivityAdmin(admin.ModelAdmin):
    """Inspect logical schedule activities."""

    list_display = (
        "canonical_activity_key",
        "project",
        "origin",
        "identity_status",
        "activity_code",
        "external_activity_id",
    )
    list_filter = ("origin", "identity_status", "source_identity_hint")
    search_fields = (
        "canonical_activity_key",
        "activity_code",
        "external_activity_id",
        "display_name",
    )
    readonly_fields = (
        "id",
        "project",
        "canonical_activity_key",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project",)


@admin.register(ScheduleSourceVersion)
class ScheduleSourceVersionAdmin(admin.ModelAdmin):
    """Inspect schedule source versions — status changes via services only."""

    list_display = (
        "version_number",
        "project",
        "source_filename",
        "source_type",
        "status",
        "data_date",
        "imported_at",
    )
    list_filter = ("status", "source_type")
    search_fields = ("source_filename", "content_hash", "external_project_id")
    readonly_fields = (
        "id",
        "project",
        "version_number",
        "content_hash",
        "imported_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project", "schedule_source", "supersedes", "created_by")


@admin.register(ScheduleImportRun)
class ScheduleImportRunAdmin(admin.ModelAdmin):
    """Inspect import run audit records."""

    list_display = (
        "source_filename",
        "project",
        "status",
        "mode",
        "started_at",
        "completed_at",
        "task_count",
    )
    list_filter = ("status", "mode", "source_type")
    search_fields = ("source_filename", "error_summary")
    readonly_fields = (
        "id",
        "project",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project", "schedule_source", "source_version", "requested_by")
