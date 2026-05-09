# core/admin.py
import requests as http_requests
from django.conf import settings
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from solo.admin import SingletonModelAdmin

from .models import (
    ErrorLog,
    LLMCallLog,
    SiteLaunchConfig,
    SiteLLMConfig,
    TeamNote,
    UserLLMConfig,
    UserTokenBudget,
)

# UserAdmin lives in ``users/admin.py`` now (the User model moved with it).


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
            "Runtime status",
            {
                "fields": (
                    "anthropic_key_status",
                    "groq_key_status",
                    "ollama_status",
                    "master_kill_status",
                ),
                "description": (
                    "Read-only snapshot of the .env-controlled inputs. If a cloud "
                    "provider is selected below but its key is missing here, calls "
                    "will fail at runtime — fix the .env entry and restart before "
                    "flipping the dropdown."
                ),
            },
        ),
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
    readonly_fields = (
        "updated_at",
        "anthropic_key_status",
        "groq_key_status",
        "ollama_status",
        "master_kill_status",
    )

    @admin.display(description="Anthropic API key")
    def anthropic_key_status(self, obj):
        if getattr(settings, "ANTHROPIC_API_KEY", ""):
            return format_html('<span style="color: #16a34a;">Configured ✓</span>')
        return format_html(
            '<span style="color: #dc2626;">Missing — set ANTHROPIC_API_KEY in .env</span>'
        )

    @admin.display(description="Groq API key")
    def groq_key_status(self, obj):
        if getattr(settings, "GROQ_API_KEY", ""):
            return format_html('<span style="color: #16a34a;">Configured ✓</span>')
        return format_html(
            '<span style="color: #dc2626;">Missing — set GROQ_API_KEY in .env</span>'
        )

    @admin.display(description="Ollama endpoint")
    def ollama_status(self, obj):
        host = getattr(settings, "OLLAMA_HOST", "")
        if not host:
            return format_html(
                '<span style="color: #dc2626;">Missing — set OLLAMA_HOST in .env</span>'
            )
        try:
            resp = http_requests.get(f"{host}/api/tags", timeout=2)
        except http_requests.RequestException as exc:
            return format_html(
                '<span style="color: #dc2626;">Unreachable</span> <code>{}</code> ({})',
                host,
                type(exc).__name__,
            )
        if resp.status_code == 200:
            return format_html(
                '<span style="color: #16a34a;">Reachable ✓</span> <code>{}</code>',
                host,
            )
        return format_html(
            '<span style="color: #dc2626;">HTTP {} from {}</span>',
            resp.status_code,
            host,
        )

    @admin.display(description="Master kill switch")
    def master_kill_status(self, obj):
        if getattr(settings, "LLM_MASTER_KILL", False):
            return format_html(
                '<span style="color: #dc2626;">Active ⛔ — all cloud calls blocked</span>'
            )
        return format_html('<span style="color: #16a34a;">Inactive</span>')


@admin.register(SiteLaunchConfig)
class SiteLaunchConfigAdmin(SingletonModelAdmin):
    """Public-face state. Operator flips the splash on/off here."""

    fieldsets = (
        (
            None,
            {
                "fields": ("state",),
                "description": (
                    "Switch between the pre-launch splash, the live landing page, "
                    "and the maintenance splash. Authenticated users (Ask, Modify, "
                    "admin) are unaffected by this toggle — it only controls the "
                    "public unauthenticated face at /."
                ),
            },
        ),
        ("Audit", {"fields": ("updated_at",)}),
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
