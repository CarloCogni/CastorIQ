# classification/admin.py
"""Django admin for Classification Layer registry (C1).

Internal management only — no product UI. No Quantities/FM/Ask/Modify integration.
"""

from django.contrib import admin

from .models import (
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)


@admin.register(ClassificationSchema)
class ClassificationSchemaAdmin(admin.ModelAdmin):
    """Admin for classification schemas (registry only)."""

    list_display = (
        "key",
        "name",
        "edition",
        "scope",
        "project",
        "purpose",
        "origin",
        "status",
        "is_official_claim",
        "is_editable",
    )
    list_filter = (
        "scope",
        "status",
        "purpose",
        "origin",
        "is_official_claim",
        "is_editable",
    )
    search_fields = ("key", "name", "edition", "source_uri")
    readonly_fields = ("created_at", "updated_at", "contract_version")
    autocomplete_fields = ("project", "created_by")
    ordering = ("key", "edition")


@admin.register(ClassificationNode)
class ClassificationNodeAdmin(admin.ModelAdmin):
    """Admin for classification nodes (codes) within a schema."""

    list_display = (
        "schema",
        "code",
        "label",
        "parent",
        "depth",
        "status",
        "sort_order",
    )
    list_filter = ("schema", "status")
    search_fields = ("code", "label", "description", "external_id")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("schema", "parent")
    ordering = ("schema", "sort_order", "code")


@admin.register(ProjectClassificationSchema)
class ProjectClassificationSchemaAdmin(admin.ModelAdmin):
    """Admin for project adoption of classification schemas."""

    list_display = (
        "project",
        "schema",
        "purpose_role",
        "is_primary",
        "priority",
        "status",
        "adopted_at",
    )
    list_filter = ("project", "purpose_role", "status", "is_primary")
    search_fields = (
        "project__name",
        "schema__key",
        "schema__name",
        "schema__edition",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("project", "schema", "adopted_by")
    ordering = ("project", "purpose_role", "-is_primary", "priority")
