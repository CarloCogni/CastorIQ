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
    FacilityAsset,
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
    """Admin for the FacilityAsset register."""

    list_display = (
        "asset_tag",
        "manufacturer",
        "model_number",
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
        "manufacturer",
        "model_number",
        "serial_number",
        "barcode",
        "ifc_entity__name",
        "ifc_entity__global_id",
    )
    raw_id_fields = ("ifc_entity", "responsible_party")
    filter_horizontal = ("classifications",)
    inlines = [AssetInventoryInline]
    date_hierarchy = "warranty_end"
    ordering = ("asset_tag",)


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
