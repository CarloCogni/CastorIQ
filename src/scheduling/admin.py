# scheduling/admin.py
"""Django admin for schedule source version foundation (read-focused)."""

from django.contrib import admin

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalDimensionValue,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    AnalyticalSnapshot,
    AnalyticalSnapshotAuditEvent,
    AnalyticalSnapshotPeriod,
    AnalyticalSnapshotResult,
    AnalyticalSnapshotSeriesPoint,
    BaselineAuditEvent,
    BaselineTaskState,
    BaselineVersion,
    MappingGovernanceEvent,
    Resource,
    ResourceAssignment,
    ScheduleActivity,
    ScheduleImportRun,
    ScheduleSourceVersion,
    WBSNode,
    WBSVersion,
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


class AnalyticalSnapshotAuditEventInline(admin.TabularInline):
    """Read-only snapshot audit trail."""

    model = AnalyticalSnapshotAuditEvent
    extra = 0
    readonly_fields = (
        "event_type",
        "previous_status",
        "new_status",
        "actor",
        "reason",
        "created_at",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AnalyticalSnapshot)
class AnalyticalSnapshotAdmin(admin.ModelAdmin):
    """Inspect analytical snapshot manifests — lifecycle via services only."""

    list_display = (
        "name",
        "project",
        "snapshot_type",
        "status",
        "data_date",
        "as_of_date",
        "repeatability_status",
        "requested_at",
    )
    list_filter = ("snapshot_type", "status", "repeatability_status")
    search_fields = ("name", "input_fingerprint", "scope_fingerprint")
    readonly_fields = (
        "id",
        "project",
        "sequence_number",
        "requested_at",
        "calculation_started_at",
        "calculation_completed_at",
        "published_at",
        "superseded_at",
        "archived_at",
        "input_fingerprint",
        "scope_fingerprint",
        "source_content_hash",
        "created_at",
        "updated_at",
    )
    raw_id_fields = (
        "project",
        "source_version",
        "baseline_version",
        "requested_by",
        "calculated_by",
        "published_by",
        "archived_by",
        "supersedes",
    )
    inlines = [AnalyticalSnapshotAuditEventInline]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.is_provenance_immutable:
            fields.extend(
                [
                    "source_version",
                    "baseline_version",
                    "data_date",
                    "as_of_date",
                    "methodology_version",
                    "capability_profile_version",
                    "trust_policy_version",
                    "input_manifest",
                    "filter_context",
                    "name",
                    "snapshot_type",
                    "status",
                ]
            )
        return fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_provenance_immutable:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AnalyticalSnapshotAuditEvent)
class AnalyticalSnapshotAuditEventAdmin(admin.ModelAdmin):
    """Append-only analytical snapshot audit trail."""

    list_display = ("event_type", "snapshot", "project", "actor", "created_at")
    list_filter = ("event_type",)
    readonly_fields = (
        "id",
        "project",
        "snapshot",
        "event_type",
        "previous_status",
        "new_status",
        "actor",
        "reason",
        "source_version",
        "baseline_version",
        "methodology_version",
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


@admin.register(AnalyticalSnapshotResult)
class AnalyticalSnapshotResultAdmin(admin.ModelAdmin):
    """Read-only persisted snapshot analytics."""

    list_display = ("snapshot", "methodology_mode", "spi", "cpi", "content_hash", "created_at")
    list_filter = ("methodology_mode", "historical_authority")
    readonly_fields = (
        "id",
        "snapshot",
        "schema_version",
        "content_hash",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AnalyticalSnapshotSeriesPoint)
class AnalyticalSnapshotSeriesPointAdmin(admin.ModelAdmin):
    """Read-only snapshot series points."""

    list_display = ("snapshot", "series_type", "period_start", "cumulative_value", "sequence")
    list_filter = ("series_type",)
    readonly_fields = ("id", "snapshot", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AnalyticalSnapshotPeriod)
class AnalyticalSnapshotPeriodAdmin(admin.ModelAdmin):
    """Read-only snapshot period rows."""

    list_display = ("snapshot", "period_start", "pv", "ev", "spi", "sequence")
    readonly_fields = ("id", "snapshot", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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


class WBSNodeInline(admin.TabularInline):
    """Inspect nodes on draft WBS versions only."""

    model = WBSNode
    extra = 0
    readonly_fields = (
        "external_id",
        "code",
        "name",
        "parent",
        "path",
        "depth",
        "sequence",
        "node_type",
        "identity_status",
        "authority",
    )
    raw_id_fields = ("parent",)

    def has_add_permission(self, request, obj=None):
        return obj is not None and obj.status == WBSVersion.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == WBSVersion.Status.DRAFT


@admin.register(WBSVersion)
class WBSVersionAdmin(admin.ModelAdmin):
    """Inspect canonical WBS versions — lifecycle via services only."""

    list_display = (
        "name",
        "project",
        "origin",
        "status",
        "revision_number",
        "is_selected_for_analysis",
        "activated_at",
    )
    list_filter = ("origin", "status", "is_selected_for_analysis")
    search_fields = ("name", "code")
    readonly_fields = (
        "id",
        "project",
        "revision_number",
        "activated_at",
        "superseded_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project", "source_version", "parent_version", "created_by", "activated_by")
    inlines = [WBSNodeInline]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.is_hierarchy_immutable:
            fields.extend(
                ["source_version", "origin", "name", "code", "data_date", "parent_version"]
            )
        return fields

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == WBSVersion.Status.DRAFT


@admin.register(WBSNode)
class WBSNodeAdmin(admin.ModelAdmin):
    """Inspect canonical WBS nodes."""

    list_display = ("name", "code", "wbs_version", "depth", "node_type", "identity_status")
    list_filter = ("node_type", "identity_status", "authority")
    search_fields = ("name", "code", "external_id")
    readonly_fields = ("id", "path", "depth", "created_at", "updated_at")
    raw_id_fields = ("wbs_version", "parent")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.wbs_version.is_hierarchy_immutable:
            fields.extend(
                [
                    "wbs_version",
                    "parent",
                    "external_id",
                    "external_parent_id",
                    "code",
                    "name",
                    "sequence",
                    "node_type",
                    "identity_status",
                    "authority",
                ]
            )
        return fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.wbs_version.status == WBSVersion.Status.DRAFT


class AnalyticalDimensionValueInline(admin.TabularInline):
    """Inspect values on draft dimensions only."""

    model = AnalyticalDimensionValue
    extra = 0
    readonly_fields = ("code", "name", "parent", "path", "depth", "sequence", "status")
    raw_id_fields = ("parent",)

    def has_add_permission(self, request, obj=None):
        return obj is not None and obj.status == AnalyticalDimension.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == AnalyticalDimension.Status.DRAFT


@admin.register(AnalyticalDimension)
class AnalyticalDimensionAdmin(admin.ModelAdmin):
    """Inspect governed dimensions — lifecycle via services only."""

    list_display = (
        "dimension_key",
        "name",
        "project",
        "dimension_type",
        "status",
        "revision_number",
        "is_selected_for_analysis",
    )
    list_filter = ("dimension_type", "status", "structure_type", "cardinality")
    search_fields = ("dimension_key", "name")
    readonly_fields = (
        "id",
        "revision_number",
        "activated_at",
        "superseded_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("project", "parent_dimension", "created_by", "activated_by")
    inlines = [AnalyticalDimensionValueInline]

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == AnalyticalDimension.Status.DRAFT


@admin.register(AnalyticalDimensionValue)
class AnalyticalDimensionValueAdmin(admin.ModelAdmin):
    """Inspect dimension values."""

    list_display = ("name", "code", "dimension", "depth", "status", "authority")
    list_filter = ("status", "identity_status", "authority")
    search_fields = ("name", "code", "external_id")
    raw_id_fields = ("dimension", "parent")

    def has_add_permission(self, request):
        return False


@admin.register(AnalyticalMappingSet)
class AnalyticalMappingSetAdmin(admin.ModelAdmin):
    """Inspect mapping sets."""

    list_display = (
        "name",
        "project",
        "dimension",
        "status",
        "revision",
        "is_selected_for_analysis",
    )
    list_filter = ("status", "is_selected_for_analysis")
    raw_id_fields = (
        "project",
        "dimension",
        "source_version",
        "baseline_version",
        "supersedes",
        "created_by",
        "approved_by",
    )

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == AnalyticalMappingSet.Status.DRAFT


@admin.register(AnalyticalMappingAssignment)
class AnalyticalMappingAssignmentAdmin(admin.ModelAdmin):
    """Inspect mapping assignments."""

    list_display = (
        "target_type",
        "dimension_value",
        "mapping_set",
        "governance_status",
        "authority",
    )
    list_filter = ("target_type", "governance_status", "authority", "mapping_method")
    raw_id_fields = (
        "mapping_set",
        "dimension_value",
        "task",
        "wbs_node",
        "ifc_file",
        "schedule_activity",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return (
            obj is not None
            and obj.mapping_set.status == AnalyticalMappingSet.Status.DRAFT
            and obj.governance_status != AnalyticalMappingAssignment.GovernanceStatus.APPROVED
        )


@admin.register(MappingGovernanceEvent)
class MappingGovernanceEventAdmin(admin.ModelAdmin):
    """Append-only mapping audit events."""

    list_display = ("event_type", "project", "dimension", "created_at")
    list_filter = ("event_type",)
    readonly_fields = (
        "id",
        "project",
        "dimension",
        "mapping_set",
        "assignment",
        "event_type",
        "previous_state",
        "resulting_state",
        "target_type",
        "target_id",
        "reason_code",
        "reason_text",
        "evidence_summary",
        "actor",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    """Inspect canonical resources (DF-E1) — population arrives in DF-E2."""

    list_display = (
        "name",
        "resource_code",
        "resource_type",
        "status",
        "project",
        "source_system",
    )
    list_filter = ("resource_type", "status", "source_system")
    search_fields = ("name", "resource_code", "external_id")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("project",)


@admin.register(ResourceAssignment)
class ResourceAssignmentAdmin(admin.ModelAdmin):
    """Inspect canonical resource assignments (DF-E1) — not wired to EVM yet."""

    list_display = (
        "id",
        "project",
        "task",
        "resource",
        "planned_cost",
        "actual_cost",
        "is_pending",
        "status",
    )
    list_filter = ("status", "is_pending", "source_system")
    search_fields = ("external_id", "p6_resource_object_id", "p6_assignment_object_id")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = (
        "project",
        "task",
        "resource",
        "schedule_activity",
        "source_version",
        "schedule_source",
    )
