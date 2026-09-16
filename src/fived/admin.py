# fived/admin.py
"""Django admin for 5D preparation snapshots (F2) — inspection only.

Not a cost/estimate/BOQ/EVM UI. No rates or readiness claims.
"""

from django.contrib import admin

from .models import FiveDDataModel, FiveDModelRow, FiveDModelVersion


@admin.register(FiveDDataModel)
class FiveDDataModelAdmin(admin.ModelAdmin):
    """Admin for preparation model containers."""

    list_display = (
        "name",
        "project",
        "status",
        "contract_version",
        "created_by",
        "updated_at",
    )
    list_filter = ("status", "contract_version")
    search_fields = ("name", "description", "project__name")
    readonly_fields = ("created_at", "updated_at", "contract_version")
    autocomplete_fields = ("project", "created_by")
    ordering = ("-updated_at",)


class FiveDModelRowInline(admin.TabularInline):
    """Read-only inline preview of frozen rows (first page only via max_num)."""

    model = FiveDModelRow
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "source_row_key",
        "ifc_class",
        "type_name",
        "quantity_basis",
        "total_quantity_display",
        "status",
    )
    readonly_fields = fields
    max_num = 0

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(FiveDModelVersion)
class FiveDModelVersionAdmin(admin.ModelAdmin):
    """Admin for frozen preparation snapshots."""

    list_display = (
        "data_model",
        "version_label",
        "source",
        "status",
        "row_count",
        "content_hash",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "source")
    search_fields = ("version_label", "content_hash", "data_model__name", "notes")
    readonly_fields = (
        "created_at",
        "updated_at",
        "content_hash",
        "content_hash_contract_version",
        "row_count",
        "settings_snapshot",
        "boundary_snapshot",
        "session_annotations_snapshot",
        "unresolved_register_snapshot",
        "semantic_source_readiness_snapshot",
        "source_query",
    )
    autocomplete_fields = ("data_model", "created_by")
    ordering = ("-created_at",)
    inlines = [FiveDModelRowInline]


@admin.register(FiveDModelRow)
class FiveDModelRowAdmin(admin.ModelAdmin):
    """Admin for frozen preparation rows (inspection)."""

    list_display = (
        "version",
        "source_row_key",
        "ifc_class",
        "type_name",
        "quantity_basis",
        "unit_basis",
        "total_quantity_display",
        "status",
    )
    list_filter = (
        "status",
        "ifc_class",
        "missing_classification",
        "missing_package_mapping",
        "missing_work_package",
        "basis_unresolved",
        "missing_quantity_source",
    )
    search_fields = (
        "source_row_key",
        "ifc_class",
        "type_name",
        "classification_code",
        "package_mapping",
        "work_package",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("version",)
    ordering = ("ifc_class", "type_name", "source_row_key")
