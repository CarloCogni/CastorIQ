# beta/admin.py
"""Operator-facing admin for the beta vetting funnel."""

from django.contrib import admin
from django.utils import timezone

from .models import BetaApplication


@admin.register(BetaApplication)
class BetaApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "job_title",
        "status",
        "created_at",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("email", "name", "job_title", "description", "notes")
    readonly_fields = (
        "id",
        "submitted_ip",
        "submitted_user_agent",
        "created_user",
        "created_at",
        "updated_at",
    )
    list_per_page = 50
    date_hierarchy = "created_at"

    fieldsets = (
        ("Application", {"fields": ("email", "name", "job_title", "description")}),
        (
            "Vetting",
            {"fields": ("status", "notes", "reviewed_by", "reviewed_at", "created_user")},
        ),
        (
            "Submission context",
            {
                "fields": ("submitted_ip", "submitted_user_agent", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
        ("Internal", {"fields": ("id",), "classes": ("collapse",)}),
    )

    actions = ["mark_called", "mark_rejected"]

    @admin.action(description="Mark as 'intro call scheduled'")
    def mark_called(self, request, queryset):
        updated = queryset.update(
            status=BetaApplication.Status.CALLED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Marked {updated} application(s) as called.")

    @admin.action(description="Mark as rejected")
    def mark_rejected(self, request, queryset):
        updated = queryset.update(
            status=BetaApplication.Status.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} application(s).")
