# facilities/admin.py
"""Admin registrations for the facilities app.

M1 registers the Asset Register models: Classification / ClassificationReference
and FacilityAsset / AssetInventory. Work, maintenance, systems, sensors, etc.
are added in later milestones.
"""

from django.contrib import admin

from .models import (
    AssetInventory,
    Classification,
    ClassificationReference,
    ExportJob,
    ExportProfile,
    FacilityAsset,
    FMDelta,
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
