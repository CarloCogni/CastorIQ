# facilities/admin.py
"""Admin registrations for the facilities app.

M1 registers the Asset Register models: Classification / ClassificationReference
and FacilityAsset / AssetInventory. M2 adds the Export Reconciliation models
(ExportProfile / ExportJob / FMDelta). M3 adds the Work Order family
(WorkOrder / WorkOrderStatusEvent / WorkOrderAttachment / Permit /
ActionRequest). Maintenance, systems, sensors, etc. land in later milestones.
"""

from django.contrib import admin

from .models import (
    ActionRequest,
    AssetInventory,
    Classification,
    ClassificationReference,
    ExportJob,
    ExportProfile,
    FacilityAsset,
    FMDelta,
    FMIntentProposal,
    Permit,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderStatusEvent,
)


class ClassificationReferenceInline(admin.TabularInline):
    """Inline for codes within a classification system."""

    model = ClassificationReference
    extra = 0
    fields = ("code", "name", "description")


@admin.register(Classification)
class ClassificationAdmin(admin.ModelAdmin):
    """Admin for classification taxonomies (Uniclass, OmniClass, …)."""

    list_display = ("name", "edition", "project", "created_at")
    list_filter = ("project",)
    search_fields = ("name", "edition")
    inlines = [ClassificationReferenceInline]
    ordering = ("name", "edition")


@admin.register(ClassificationReference)
class ClassificationReferenceAdmin(admin.ModelAdmin):
    """Admin for single classification codes (e.g. Ss_25_10_30)."""

    list_display = ("code", "name", "classification", "created_at")
    list_filter = ("classification__project", "classification")
    search_fields = ("code", "name", "description")
    ordering = ("classification__name", "code")


class AssetInventoryInline(admin.StackedInline):
    """Inline for the cost / acquisition companion of an asset."""

    model = AssetInventory
    extra = 0
    fields = (
        "original_value",
        "current_value",
        "total_replacement_cost",
        "depreciated_value",
        "acquisition_date",
    )


@admin.register(FacilityAsset)
class FacilityAssetAdmin(admin.ModelAdmin):
    """Admin for the FacilityAsset register — supports linked and orphan rows."""

    list_display = (
        "asset_tag",
        "display_name",
        "display_ifc_type",
        "is_orphan",
        "manufacturer",
        "condition_score",
        "warranty_end",
        "project",
        "responsible_party",
    )
    list_filter = (
        "project",
        "manufacturer",
        "condition_score",
    )
    search_fields = (
        "asset_tag",
        "name",
        "ifc_type",
        "manufacturer",
        "model_number",
        "serial_number",
        "barcode",
        "location_text",
        "ifc_entity__name",
        "ifc_entity__global_id",
    )
    raw_id_fields = ("ifc_entity", "spatial_container", "responsible_party")
    filter_horizontal = ("classifications",)
    inlines = [AssetInventoryInline]
    date_hierarchy = "warranty_end"
    ordering = ("asset_tag",)
    fieldsets = (
        (
            "Linkage",
            {
                "fields": ("project", "ifc_entity", "name", "ifc_type"),
                "description": (
                    "Leave 'IFC Entity' blank for orphan assets (not in the IFC model) — "
                    "in that case 'Name' and 'IFC Type' are required."
                ),
            },
        ),
        (
            "Location (orphans only)",
            {
                "fields": ("spatial_container", "location_text"),
                "description": "Ignored for IFC-linked assets — they inherit location from the IFC entity.",
            },
        ),
        (
            "Identification",
            {
                "fields": (
                    "asset_tag",
                    "manufacturer",
                    "model_number",
                    "serial_number",
                    "barcode",
                ),
            },
        ),
        (
            "Lifecycle & condition",
            {
                "fields": (
                    "commissioning_date",
                    "expected_service_life_years",
                    "decommissioned_at",
                    "condition_score",
                    "warranty_start",
                    "warranty_end",
                ),
            },
        ),
        (
            "Assignment & notes",
            {
                "fields": ("responsible_party", "classifications", "notes"),
            },
        ),
    )


@admin.register(AssetInventory)
class AssetInventoryAdmin(admin.ModelAdmin):
    """Admin for AssetInventory rows, viewed separately from an asset."""

    list_display = (
        "asset",
        "original_value",
        "current_value",
        "total_replacement_cost",
        "acquisition_date",
    )
    search_fields = ("asset__asset_tag", "asset__serial_number")
    raw_id_fields = ("asset",)


# ── Export Reconciliation (M2) ────────────────────────────────────────────


@admin.register(ExportProfile)
class ExportProfileAdmin(admin.ModelAdmin):
    """Default + custom export profiles per project."""

    list_display = ("name", "project", "is_default", "created_at")
    list_filter = ("project", "is_default")
    search_fields = ("name",)
    raw_id_fields = ("project",)


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    """One row per Export IFC attempt — read-mostly."""

    list_display = (
        "id",
        "project",
        "ifc_file",
        "status",
        "delta_count",
        "entity_count",
        "conflict_count",
        "commit_hash",
        "initiated_by",
        "created_at",
    )
    list_filter = ("status", "project")
    search_fields = ("commit_hash", "ifc_file__name")
    raw_id_fields = ("project", "ifc_file", "initiated_by", "export_profile")
    readonly_fields = (
        "started_at",
        "completed_at",
        "commit_hash",
        "delta_count",
        "entity_count",
        "conflict_count",
        "error_reason",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"


@admin.register(FMDelta)
class FMDeltaAdmin(admin.ModelAdmin):
    """Audit view for FM-generated IFC writes — read-mostly."""

    list_display = (
        "entity_guid",
        "operation",
        "project",
        "asset",
        "is_pending",
        "created_at",
        "applied_to_ifc_at",
    )
    list_filter = ("operation", "project")
    search_fields = ("entity_guid", "ifc_commit_hash")
    raw_id_fields = ("project", "asset", "created_by", "export_job")
    readonly_fields = (
        "payload",
        "applied_to_ifc_at",
        "superseded_at",
        "ifc_commit_hash",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description="Pending")
    def is_pending(self, obj: FMDelta) -> bool:
        return obj.is_pending


# ── Work Orders (M3) ──────────────────────────────────────────────────────


class WorkOrderStatusEventInline(admin.TabularInline):
    """Read-only inline rendering of a WO's full status timeline."""

    model = WorkOrderStatusEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "from_status", "to_status", "actor", "actor_role", "note")
    readonly_fields = fields
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):  # noqa: D401
        """Status events are append-only via the service layer, not admin."""
        return False


class WorkOrderAttachmentInline(admin.TabularInline):
    """Inline list of files attached to the WO."""

    model = WorkOrderAttachment
    extra = 0
    fields = ("kind", "file", "caption", "uploaded_by", "created_at")
    readonly_fields = ("created_at",)


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    """Admin for the WorkOrder lifecycle — read-mostly, mutations go through the service."""

    list_display = (
        "wo_number",
        "title",
        "project",
        "status",
        "priority",
        "category",
        "assignee_user",
        "assignee_vendor",
        "scheduled_start",
        "due_at",
        "created_at",
    )
    list_filter = ("status", "priority", "category", "project")
    search_fields = (
        "wo_number",
        "title",
        "description",
        "assignee_vendor",
        "affected_asset__asset_tag",
    )
    raw_id_fields = (
        "project",
        "affected_asset",
        "affected_spatial",
        "requested_by",
        "assignee_user",
        "verified_by",
        "source_action_request",
    )
    readonly_fields = (
        "wo_number",
        "actual_start",
        "actual_end",
        "verified_at",
        "verified_by",
        "closed_at",
        "source_intent_batch_id",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    inlines = [WorkOrderStatusEventInline, WorkOrderAttachmentInline]
    fieldsets = (
        (
            "Identity",
            {"fields": ("project", "wo_number", "title", "description", "category", "priority")},
        ),
        (
            "Anchors",
            {"fields": ("affected_asset", "affected_spatial")},
        ),
        (
            "Lifecycle",
            {
                "fields": (
                    "status",
                    "requested_by",
                    "assignee_user",
                    "assignee_vendor",
                    "scheduled_start",
                    "scheduled_end",
                    "actual_start",
                    "actual_end",
                    "due_at",
                    "verified_at",
                    "verified_by",
                    "closed_at",
                ),
            },
        ),
        (
            "Provenance",
            {"fields": ("source_action_request", "source_intent_batch_id")},
        ),
    )


@admin.register(WorkOrderStatusEvent)
class WorkOrderStatusEventAdmin(admin.ModelAdmin):
    """Append-only audit log of WO transitions."""

    list_display = (
        "work_order",
        "from_status",
        "to_status",
        "actor",
        "actor_role",
        "created_at",
    )
    list_filter = ("to_status", "actor_role")
    search_fields = ("work_order__wo_number", "note")
    raw_id_fields = ("work_order", "actor")
    readonly_fields = ("work_order", "from_status", "to_status", "actor", "actor_role", "note")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):  # noqa: D401
        """Status events are written by the service layer only."""
        return False

    def has_change_permission(self, request, obj=None):  # noqa: D401
        """Append-only — no edits."""
        return False


@admin.register(WorkOrderAttachment)
class WorkOrderAttachmentAdmin(admin.ModelAdmin):
    """Admin for attachments — light CRUD."""

    list_display = ("work_order", "kind", "caption", "uploaded_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("caption", "work_order__wo_number")
    raw_id_fields = ("work_order", "uploaded_by")


@admin.register(Permit)
class PermitAdmin(admin.ModelAdmin):
    """Admin for Permits — full CRUD per M3 user decision."""

    list_display = (
        "permit_number",
        "title",
        "kind",
        "status",
        "issued_to",
        "valid_from",
        "valid_until",
        "project",
    )
    list_filter = ("kind", "status", "project")
    search_fields = ("permit_number", "title", "issued_to", "notes")
    raw_id_fields = ("project",)
    filter_horizontal = ("work_orders",)
    date_hierarchy = "valid_until"


@admin.register(ActionRequest)
class ActionRequestAdmin(admin.ModelAdmin):
    """Admin for occupant-submitted Action Requests."""

    list_display = (
        "title",
        "project",
        "submitted_by",
        "severity",
        "status",
        "affected_asset",
        "affected_spatial",
        "created_at",
    )
    list_filter = ("status", "severity", "project")
    search_fields = ("title", "description", "triage_note")
    raw_id_fields = ("project", "submitted_by", "affected_asset", "affected_spatial")
    date_hierarchy = "created_at"


@admin.register(FMIntentProposal)
class FMIntentProposalAdmin(admin.ModelAdmin):
    """Audit view for Intent-to-WO batches — read-mostly."""

    list_display = (
        "id",
        "project",
        "user",
        "status",
        "confidence",
        "created_at",
        "applied_at",
    )
    list_filter = ("status", "project")
    search_fields = ("user_message", "error")
    raw_id_fields = ("project", "user")
    readonly_fields = (
        "user_message",
        "plan_json",
        "confidence",
        "applied_at",
        "created_wo_ids",
        "error",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
