# metacastor/admin.py
"""MetaCastor admin registrations."""

from django.contrib import admin

from .models import SkillExample


@admin.register(SkillExample)
class SkillExampleAdmin(admin.ModelAdmin):
    """Admin view for the skill bank."""

    list_display = [
        "id",
        "short_query",
        "outcome_tier",
        "is_organic",
        "was_approved",
        "commit_success",
        "project",
        "created_at",
    ]
    list_filter = ["outcome_tier", "is_organic", "was_approved", "commit_success"]
    search_fields = ["query_text"]
    readonly_fields = ["embedding_preview", "created_at", "updated_at"]
    ordering = ["-created_at"]

    @admin.display(description="Query")
    def short_query(self, obj: SkillExample) -> str:
        return obj.query_text[:80]

    @admin.display(description="Query Embedding")
    def embedding_preview(self, obj: SkillExample) -> str:
        if obj.query_embedding is None:
            return "—"
        vec = list(obj.query_embedding)
        preview = ", ".join(f"{v:.4f}" for v in vec[:6])
        return f"[{preview}, …] ({len(vec)}d)"
