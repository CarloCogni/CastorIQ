# scheduling/admin.py
"""Django admin for schedule source version foundation (read-focused)."""

from django.contrib import admin

from scheduling.models import (
    BaselineAuditEvent,
    BaselineTaskState,
    BaselineVersion,
    ScheduleActivity,
    ScheduleImportRun,
    ScheduleSourceVersion,
)


class BaselineTaskStateInline(admin.TabularInline):
    """Read-only task states for published baselines."""

    model = BaselineTaskState
    extra = 0
    readonly_fields = (
        "schedule_activity",
        "name_snapshot",
        "planned_start",
        "planned_finish",
        "baseline_cost",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return obj is not None and not obj.is_immutable


@admin.register(BaselineVersion)
class BaselineVersionAdmin(admin.ModelAdmin):
    """Inspect baseline versions — lifecycle via services only."""

    list_display = (
        "name",
        "project",
        "baseline_type",
        "status",
        "revision_number",
        "is_selected_for_analysis",
        "published_at",
    )
    list_filter = ("baseline_type", "status", "is_selected_for_analysis")
    search_fields = ("name", "code")
    readonly_fields = (
        "id",
        "project",
        "revision_number",
        "published_at",
        "approved_at",
        "superseded_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project", "source_version", "parent_baseline", "created_by")
    inlines = [BaselineTaskStateInline]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.is_immutable:
            fields.extend(
                [
                    "source_version",
                    "baseline_type",
                    "data_date",
                    "effective_date",
                    "currency",
                    "methodology_version",
                    "parent_baseline",
                    "name",
                    "code",
                    "status",
                ]
            )
        return fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_immutable:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(BaselineAuditEvent)
class BaselineAuditEventAdmin(admin.ModelAdmin):
    """Append-only baseline audit trail."""

    list_display = ("event_type", "baseline_version", "project", "actor", "created_at")
    list_filter = ("event_type",)
    readonly_fields = (
        "id",
        "project",
        "baseline_version",
        "event_type",
        "previous_status",
        "new_status",
        "actor",
        "reason",
        "source_version",
        "metadata",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
