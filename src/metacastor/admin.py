# metacastor/admin.py
"""MetaCastor admin registrations."""

from django.contrib import admin

from .models import FailureRecord


@admin.register(FailureRecord)
class FailureRecordAdmin(admin.ModelAdmin):
    """Admin view for D3 failure memory."""

    list_display = [
        "short_query",
        "error_type",
        "category",
        "failure_phase",
        "tier",
        "project",
        "created_at",
    ]
    list_filter = ["category", "error_type", "failure_phase", "tier"]
    search_fields = ["query_text", "error_detail"]
    readonly_fields = ["query_embedding", "created_at", "updated_at"]
    ordering = ["-created_at"]

    @admin.display(description="Query")
    def short_query(self, obj: FailureRecord) -> str:
        return obj.query_text[:80]
