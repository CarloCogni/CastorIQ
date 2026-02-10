# ifc_processor/admin.py
from django.contrib import admin, messages
from .models import IFCFile, IFCEntity, IFCDataIssue
from django.utils.html import format_html
import json
import numpy as np


class DataIssueInline(admin.TabularInline):
    model = IFCDataIssue
    extra = 0
    readonly_fields = ('issue_type', 'global_id', 'is_resolved')
    can_delete = False
    show_change_link = True  # Allows jumping to the full issue page

@admin.register(IFCFile)
class IFCFileAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'status', 'schema_version', 'entity_count', 'processed_at')
    list_filter = ('status', 'schema_version', 'project')
    search_fields = ('name', 'project_name', 'file_hash', 'project__name')
    readonly_fields = ('id', 'file_hash', 'entity_count', 'processed_at', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    list_select_related = ('project',)  # Performance
    autocomplete_fields = ['project']
    inlines = [DataIssueInline]

    fieldsets = (
        ('File Info', {
            'fields': ('id', 'project', 'name', 'file', 'file_hash')
        }),
        ('IFC Metadata', {
            'fields': ('schema_version', 'project_name', 'description'),
        }),
        ('Processing', {
            'fields': ('status', 'processed_at', 'error_message', 'entity_count'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    actions = ['recalculate_file_hash', 'trigger_ai_processing', 'mark_as_pending']

    @admin.action(description="🔄 Recalculate SHA-256 Hash")
    def recalculate_file_hash(self, request, queryset):
        updated = 0
        for obj in queryset:
            obj.file_hash = obj.calculate_hash()
            obj.save(update_fields=['file_hash'])
            updated += 1
        self.message_user(request, f"Recalculated hash for {updated} files.", messages.SUCCESS)

    @admin.action(description="🚀 Run AI Entity Extraction")
    def trigger_ai_processing(self, request, queryset):
        already_processing = queryset.filter(status=IFCFile.Status.PROCESSING).count()
        if already_processing:
            self.message_user(
                request,
                f"{already_processing} file(s) are already being processed.",
                messages.WARNING
            )

        to_process = queryset.exclude(status=IFCFile.Status.PROCESSING)
        count = to_process.count()

        for ifc_file in to_process:
            # TODO: hypothetical_task.delay(ifc_file.id)
            ifc_file.status = IFCFile.Status.PROCESSING
            ifc_file.save(update_fields=['status'])

        if count:
            self.message_user(
                request,
                f"AI processing queued for {count} file(s).",
                messages.SUCCESS
            )

    @admin.action(description="⏪ Reset to Pending")
    def mark_as_pending(self, request, queryset):
        updated = queryset.update(status=IFCFile.Status.PENDING, error_message='')
        self.message_user(request, f"Reset {updated} file(s) to pending.", messages.SUCCESS)


@admin.register(IFCEntity)
class IFCEntityAdmin(admin.ModelAdmin):
    list_display = ('global_id_short', 'ifc_type', 'name', 'building_storey', 'ifc_file')
    list_filter = ('ifc_type', 'building_storey', 'ifc_file__project')
    search_fields = ('global_id', 'name', 'description', 'ifc_file__name')
    readonly_fields = ('id', 'properties_pretty', 'embedding_preview', 'created_at', 'updated_at')
    list_per_page = 50
    show_full_result_count = False
    list_select_related = ('ifc_file', 'ifc_file__project')

    fieldsets = (
        ('Identification', {
            'fields': ('id', 'ifc_file', 'global_id', 'ifc_type', 'name')
        }),
        ('Spatial Location', {
            'fields': ('building', 'building_storey', 'space'),
        }),
        ('Properties', {
            'fields': ('properties_pretty', 'description'),
        }),
        ('Embedding', {
            # 2. CHANGE: Replace 'embedding' with 'embedding_preview' here too
            'fields': ('embedding_preview',),
            'classes': ('collapse',),
        }),
    )

    def global_id_short(self, obj):
        return obj.global_id[:12] + "..." if len(obj.global_id) > 12 else obj.global_id

    global_id_short.short_description = "Global ID"
    global_id_short.admin_order_field = 'global_id'

    def properties_pretty(self, obj):
        """Display JSON properties in a readable format."""
        import json
        from django.utils.html import format_html
        if obj.properties:
            formatted = json.dumps(obj.properties, indent=2)
            return format_html('<pre style="margin:0; white-space:pre-wrap;">{}</pre>', formatted)
        return "-"

    properties_pretty.short_description = "Properties"

    def embedding_preview(self, obj):
        """Format the vector embedding for display to avoid Numpy boolean errors."""
        if obj.embedding is None:
            return "-"

        # Convert to list if it's a numpy array to make it safe
        vector = obj.embedding
        if hasattr(vector, 'tolist'):
            vector = vector.tolist()

        # Create a short preview
        preview = f"Vector [{len(vector)} dims]: {vector[:3]}..."

        # Return full vector in a scrollable box (safe string)
        return format_html(
            '<div style="max-height: 100px; overflow-y: auto; background: #f8f9fa; padding: 5px; border-radius: 4px; font-family: monospace; font-size: 11px;">{}</div>',
            str(vector)
        )

    embedding_preview.short_description = "Embedding Vector"

@admin.register(IFCDataIssue)
class IFCDataIssueAdmin(admin.ModelAdmin):
    # 1. Quick Glance Information
    list_display = ('issue_type_badge', 'global_id', 'ifc_type', 'ifc_file', 'is_resolved', 'created_at')
    list_filter = ('issue_type', 'is_resolved', 'ifc_type', 'ifc_file__project')
    search_fields = ('global_id', 'description', 'ifc_file__name')
    list_editable = ('is_resolved',)  # Allow quick marking as fixed

    # 2. Organization
    readonly_fields = ('id', 'issue_type', 'global_id', 'ifc_type', 'ifc_file', 'raw_data_pretty', 'created_at')

    fieldsets = (
        ('Issue Details', {
            'fields': ('id', 'ifc_file', 'issue_type', 'is_resolved')
        }),
        ('Technical Context', {
            'fields': ('global_id', 'ifc_type', 'description'),
        }),
        ('Rejected Data Snapshot', {
            'fields': ('raw_data_pretty',),
            'description': 'This is the data that was skipped during the main parsing process.'
        }),
    )

    # 3. Custom Display Methods
    def issue_type_badge(self, obj):
        colors = {
            'duplicate_guid': '#ef4444',  # Red
            'invalid_geometry': '#f59e0b',  # Orange
            'missing_property': '#3b82f6',  # Blue
        }
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:12px; font-size:10px; font-weight:bold; text-transform:uppercase;">{}</span>',
            colors.get(obj.issue_type, '#6b7280'),
            obj.get_issue_type_display()
        )

    issue_type_badge.short_description = "Issue Type"

    def raw_data_pretty(self, obj):
        """Renders the JSON raw_data into a readable format in the admin."""
        if not obj.raw_data:
            return "-"
        return format_html(
            '<pre style="background: #f4f4f4; padding: 10px; border-radius: 5px; font-family: monospace;">{}</pre>',
            json.dumps(obj.raw_data, indent=2)
        )

    raw_data_pretty.short_description = "Raw Data Content"

    # 4. Mass Actions
    actions = ['mark_as_resolved']

    @admin.action(description="✅ Mark selected issues as resolved")
    def mark_as_resolved(self, request, queryset):
        queryset.update(is_resolved=True)
        self.message_user(request, "Selected issues have been marked as resolved.")

