# core/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.html import format_html
from solo.admin import SingletonModelAdmin

from .models import (
    ErrorLog,
    LLMCallLog,
    SiteLLMConfig,
    TeamNote,
    UserLLMConfig,
    UserTokenBudget,
)

# Unregister the default UserAdmin and register with search_fields
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    search_fields = ("username", "email", "first_name", "last_name")
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "severity_badge",
        "exception_type",
        "message_excerpt",
        "view_name",
        "user",
        "is_resolved",
        "sent_to_supabase",
    )
    list_filter = ("severity", "is_resolved", "exception_type", "created_at")
    search_fields = ("message", "exception_type", "stacktrace", "url", "view_name")
    readonly_fields = (
        "id",
        "created_at",
        "severity",
        "message",
        "exception_type",
        "stacktrace_formatted",
        "url",
        "method",
        "view_name",
        "user",
        "user_agent",
        "ip_address",
        "request_data_formatted",
    )
    list_per_page = 50
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Error Details",
            {"fields": ("id", "created_at", "severity", "exception_type", "message")},
        ),
        (
            "Stack Trace",
            {
                "fields": ("stacktrace_formatted",),
                "classes": ("collapse",),
            },
        ),
        (
            "Request Context",
            {
                "fields": ("url", "method", "view_name", "user", "ip_address", "user_agent"),
            },
        ),
        (
            "Request Data",
            {
                "fields": ("request_data_formatted",),
                "classes": ("collapse",),
            },
        ),
        (
            "Resolution",
            {
                "fields": ("is_resolved", "resolved_by", "resolved_at", "resolution_note"),
            },
        ),
    )

    actions = ["mark_as_resolved", "mark_as_unresolved"]

    def severity_badge(self, obj):
        """Display severity as colored badge"""
        colors = {
            "debug": "#6b7280",
            "info": "#3b82f6",
            "warning": "#f59e0b",
            "error": "#ef4444",
            "critical": "#991b1b",
        }
        return format_html(
            '<span style="background:{}; color:white; padding:3px 10px; '
            'border-radius:4px; font-size:11px; font-weight:600;">{}</span>',
            colors.get(obj.severity, "#6b7280"),
            obj.get_severity_display().upper(),
        )

    severity_badge.short_description = "Severity"

    def message_excerpt(self, obj):
        """Show truncated message"""
        return obj.message[:80] + "..." if len(obj.message) > 80 else obj.message

    message_excerpt.short_description = "Message"

    def stacktrace_formatted(self, obj):
        """Display stacktrace in readable format"""
        return format_html(
            '<pre style="background:#1a1a1a; color:#10b981; padding:15px; '
            "border-radius:5px; overflow-x:auto; font-size:12px; "
            'font-family:monospace;">{}</pre>',
            obj.stacktrace,
        )

    stacktrace_formatted.short_description = "Stack Trace"

    def request_data_formatted(self, obj):
        """Display request data as formatted JSON"""
        import json

        if obj.request_data:
            return format_html(
                '<pre style="background:#f4f4f4; padding:10px; '
                'border-radius:5px; font-family:monospace; font-size:12px;">{}</pre>',
                json.dumps(obj.request_data, indent=2),
            )
        return "-"

    request_data_formatted.short_description = "Request Data"

    @admin.action(description="✅ Mark as resolved")
    def mark_as_resolved(self, request, queryset):
        """Mark selected errors as resolved"""
        updated = queryset.update(
            is_resolved=True, resolved_by=request.user, resolved_at=timezone.now()
        )
        self.message_user(request, f"Marked {updated} error(s) as resolved.")

    @admin.action(description="🔄 Mark as unresolved")
    def mark_as_unresolved(self, request, queryset):
        """Mark selected errors as unresolved"""
        updated = queryset.update(is_resolved=False, resolved_by=None, resolved_at=None)
        self.message_user(request, f"Marked {updated} error(s) as unresolved.")


@admin.register(UserLLMConfig)
class UserLLMConfigAdmin(admin.ModelAdmin):
    list_display = ("user", "active_model", "updated_at")
    list_filter = ("active_model",)
    readonly_fields = ("updated_at",)


@admin.register(UserTokenBudget)
class UserTokenBudgetAdmin(admin.ModelAdmin):
    """Per-user daily LLM token cap. Operator can reset, raise, or hard-block."""

    list_display = (
        "user",
        "daily_cap",
        "used_today",
        "remaining_display",
        "hard_blocked",
        "last_reset_at",
    )
    list_filter = ("hard_blocked",)
    search_fields = ("user__username", "user__email")
    actions = ["reset_used_today", "block_user", "unblock_user"]
    readonly_fields = ("last_reset_at", "updated_at")

    def remaining_display(self, obj: UserTokenBudget) -> str:
        return f"{obj.remaining:,}" if obj.daily_cap else "unlimited"

    remaining_display.short_description = "Remaining"

    @admin.action(description="Reset used_today to 0")
    def reset_used_today(self, request, queryset):
        updated = queryset.update(used_today=0, last_reset_at=timezone.now())
        self.message_user(request, f"Reset {updated} budget(s).")

    @admin.action(description="Block (refuse all cloud LLM calls)")
    def block_user(self, request, queryset):
        updated = queryset.update(hard_blocked=True)
        self.message_user(request, f"Hard-blocked {updated} user(s).")

    @admin.action(description="Unblock")
    def unblock_user(self, request, queryset):
        updated = queryset.update(hard_blocked=False)
        self.message_user(request, f"Unblocked {updated} user(s).")


@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    """Read-only LLM-call audit log. One row per invoke, success or failure."""

    list_display = (
        "created_at",
        "user",
        "provider",
        "model",
        "purpose",
        "tokens_in",
        "tokens_out",
        "estimated_cost_usd",
        "latency_ms",
        "succeeded",
    )
    list_filter = ("provider", "purpose", "succeeded", "created_at")
    search_fields = ("user__username", "user__email", "model", "error_type")
    date_hierarchy = "created_at"
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Allow purging old rows from admin if the table grows large.
        return request.user.is_superuser


@admin.register(SiteLLMConfig)
class SiteLLMConfigAdmin(SingletonModelAdmin):
    """Site-wide LLM provider configuration. Operator flips Ask/Modify per provider here."""

    fieldsets = (
        (
            "Ask pipeline (RAG)",
            {"fields": ("ask_provider", "ask_model")},
        ),
        (
            "Modify pipeline (writeback)",
            {"fields": ("modify_provider", "modify_model")},
        ),
        (
            "Emergency override",
            {
                "fields": ("force_local_ollama",),
                "description": (
                    "When enabled, every LLM call routes to local Ollama regardless "
                    "of the per-purpose settings above. Use for cost-free testing or "
                    "provider-outage failover."
                ),
            },
        ),
    )
    readonly_fields = ("updated_at",)


@admin.register(TeamNote)
class TeamNoteAdmin(admin.ModelAdmin):
    """Admin interface for cross-machine team notes."""

    list_display = (
        "title",
        "category",
        "priority",
        "author_username",
        "is_resolved",
        "sent_to_supabase",
        "created_at",
    )
    list_filter = (
        "category",
        "priority",
        "is_resolved",
        "sent_to_supabase",
        "author_username",
    )
    search_fields = (
        "title",
        "body",
        "author_username",
        "page_url",
    )
    readonly_fields = (
        "id",
        "author_username",
        "page_url",
        "browser_info",
        "sent_to_supabase",
        "supabase_id",
        "created_at",
        "updated_at",
    )
    list_per_page = 30
    date_hierarchy = "created_at"
    list_editable = ("is_resolved",)

    fieldsets = (
        (
            "Content",
            {
                "fields": ("title", "body", "category", "priority"),
            },
        ),
        (
            "Context",
            {
                "fields": ("author_username", "page_url", "browser_info"),
            },
        ),
        (
            "Resolution",
            {
                "fields": ("is_resolved", "resolved_by", "resolved_at", "resolution_note"),
            },
        ),
        (
            "Sync",
            {
                "classes": ("collapse",),
                "fields": ("sent_to_supabase", "supabase_id"),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("id", "created_at", "updated_at"),
            },
        ),
    )
