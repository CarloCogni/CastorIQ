# core/admin.py
import logging
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError

import requests as http_requests
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from langchain_core.messages import HumanMessage
from solo.admin import SingletonModelAdmin

from .llm import (
    _BUILDERS,
    LLMConfigurationError,
    LLMMasterKillError,
    safe_invoke,
)
from .llm_cloud_registry import CLOUD_MODELS, PROVIDER_LABELS, is_valid
from .models import (
    ErrorLog,
    LLMCallLog,
    SiteLaunchConfig,
    SiteLLMConfig,
    SiteStorageConfig,
    TeamNote,
    UserLLMConfig,
    UserStorageQuota,
    UserTokenBudget,
    WeeklyDigestConfig,
)
from .services import digest_email

logger = logging.getLogger(__name__)

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


def _fmt_bytes_admin(num: int) -> str:
    """Compact byte formatter for the admin list display."""
    step = 1024.0
    value = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= step
    return f"{value:.1f} PB"


@admin.register(SiteStorageConfig)
class SiteStorageConfigAdmin(SingletonModelAdmin):
    """Site-wide storage caps. Operator changes the default per-user quota and
    the per-file upload ceiling here without redeploying."""

    fieldsets = (
        (
            "Default per-user quota",
            {
                "fields": ("default_per_user_quota_bytes",),
                "description": (
                    "Applied when a user's UserStorageQuota row has quota_bytes=0. "
                    "Counts uploaded files plus the per-project Git history that "
                    "grows on every Modify approval. 1 GB by default. Set in raw "
                    "bytes (1 GB = 1073741824, 500 MB = 524288000)."
                ),
            },
        ),
        (
            "Per-file upload cap",
            {
                "fields": ("per_file_cap_bytes",),
                "description": (
                    "Maximum size of any single uploaded file (IFC or document). "
                    "Failing fast here keeps oversized uploads from touching disk. "
                    "200 MB by default."
                ),
            },
        ),
        ("Audit", {"fields": ("updated_at",)}),
    )
    readonly_fields = ("updated_at",)


@admin.register(UserStorageQuota)
class UserStorageQuotaAdmin(admin.ModelAdmin):
    """Per-user storage cap. Operator can raise, reset cached usage, or block."""

    list_display = (
        "user",
        "effective_quota_display",
        "used_display",
        "pct_display",
        "hard_blocked",
        "last_recalculated_at",
    )
    list_filter = ("hard_blocked",)
    search_fields = ("user__username", "user__email")
    actions = ["recalculate_quotas", "block_user", "unblock_user", "clear_override"]
    readonly_fields = ("cached_used_bytes", "last_recalculated_at", "updated_at")

    @admin.display(description="Effective quota")
    def effective_quota_display(self, obj: UserStorageQuota) -> str:
        cap = obj.effective_quota()
        source = "override" if obj.quota_bytes else "site default"
        return f"{_fmt_bytes_admin(cap)} ({source})"

    @admin.display(description="Used (cached)")
    def used_display(self, obj: UserStorageQuota) -> str:
        return _fmt_bytes_admin(obj.cached_used_bytes)

    @admin.display(description="% used")
    def pct_display(self, obj: UserStorageQuota) -> str:
        return f"{obj.pct_used}%"

    @admin.action(description="Recalculate from disk")
    def recalculate_quotas(self, request, queryset):
        for quota in queryset:
            quota.recalculate()
        self.message_user(request, f"Recalculated {queryset.count()} quota(s) from disk.")

    @admin.action(description="Block (refuse all uploads)")
    def block_user(self, request, queryset):
        updated = queryset.update(hard_blocked=True)
        self.message_user(request, f"Hard-blocked {updated} user(s).")

    @admin.action(description="Unblock")
    def unblock_user(self, request, queryset):
        updated = queryset.update(hard_blocked=False)
        self.message_user(request, f"Unblocked {updated} user(s).")

    @admin.action(description="Clear override (use site default)")
    def clear_override(self, request, queryset):
        updated = queryset.update(quota_bytes=0)
        self.message_user(request, f"Cleared override on {updated} user(s).")


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


def _build_cloud_model_choices(current_value: str) -> list:
    """Grouped <optgroup> choices for the model dropdown.

    Includes a leading blank option (required when provider is Ollama) and
    appends a synthetic "Unknown" group containing ``current_value`` if the
    saved value isn't in the registry — otherwise Django's ChoiceField would
    refuse to render a saved value set via shell.
    """
    grouped: list = [("", "(use .env default — required for Ollama)")]
    by_provider: dict[str, list] = {}
    for m in CLOUD_MODELS:
        by_provider.setdefault(m.provider, []).append((m.model_id, m.label))
    for provider_key, label in PROVIDER_LABELS.items():
        if provider_key in by_provider:
            grouped.append((label, tuple(by_provider[provider_key])))

    if current_value and not is_valid_model_anywhere(current_value):
        grouped.append(
            ("Unknown (saved value)", ((current_value, f"{current_value} (not in registry)"),))
        )
    return grouped


def is_valid_model_anywhere(model_id: str) -> bool:
    """True if the model_id appears under any provider in the registry."""
    return any(m.model_id == model_id for m in CLOUD_MODELS)


class SiteLLMConfigForm(forms.ModelForm):
    """Admin form: render ``ask_model``/``modify_model`` as grouped dropdowns
    and cross-validate that the chosen model matches the chosen provider.
    """

    class Meta:
        model = SiteLLMConfig
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ask_value = (self.instance.ask_model or "") if self.instance else ""
        modify_value = (self.instance.modify_model or "") if self.instance else ""

        self.fields["ask_model"] = forms.ChoiceField(
            choices=_build_cloud_model_choices(ask_value),
            required=False,
            label="Ask Model",
            help_text=(
                "Cloud model for the Ask (RAG) pipeline. Must be blank when provider "
                "is Ollama — the Ollama model is set via OLLAMA_MODEL plus per-user override."
            ),
            initial=ask_value,
        )
        self.fields["modify_model"] = forms.ChoiceField(
            choices=_build_cloud_model_choices(modify_value),
            required=False,
            label="Modify Model",
            help_text=(
                "Cloud model for the Modify (writeback) pipeline. Must be blank when "
                "provider is Ollama."
            ),
            initial=modify_value,
        )

    def clean(self):
        cleaned = super().clean()
        for prov_field, model_field in (
            ("ask_provider", "ask_model"),
            ("modify_provider", "modify_model"),
        ):
            provider = cleaned.get(prov_field)
            model = (cleaned.get(model_field) or "").strip()

            if provider == "ollama":
                if model:
                    self.add_error(
                        model_field,
                        "Leave blank when provider is Ollama. The Ollama model is "
                        "configured via the OLLAMA_MODEL env var (and per-user override).",
                    )
                continue

            if provider in ("anthropic", "groq"):
                provider_label = PROVIDER_LABELS.get(provider, provider)
                if not model:
                    self.add_error(
                        model_field,
                        f"Choose a {provider_label} model from the dropdown.",
                    )
                elif not is_valid(provider, model):
                    self.add_error(
                        model_field,
                        f"{model!r} is not a valid {provider_label} model. "
                        f"Pick one from the dropdown.",
                    )
        return cleaned


def _ping_target(obj: SiteLLMConfig, purpose: str) -> tuple[str, str]:
    """(provider, model) the test ping should hit.

    Mirrors ``SiteLLMConfig.resolve()`` but deliberately ignores
    ``force_local_ollama``: the operator clicked "Test Ask connection"
    specifically to verify the configured Ask provider, not the panic switch.
    """
    if purpose == "ask":
        provider = obj.ask_provider
        model = obj.ask_model
        cloud_default = settings.ASK_MODEL
    else:
        provider = obj.modify_provider
        model = obj.modify_model
        cloud_default = settings.MODIFY_MODEL
    provider = str(provider)
    if provider == "ollama":
        return ("ollama", model or settings.OLLAMA_MODEL)
    return (provider, model or cloud_default)


def _run_admin_ping(obj: SiteLLMConfig, purpose: str) -> tuple[bool, str, int]:
    """Issue a 1-token probe with the saved config; return (ok, detail, elapsed_ms).

    Builds a raw provider client via ``core.llm._BUILDERS`` so the test exercises
    the production code path, then bypasses ``TrackedChatModel`` (no user budget
    to charge against). All exceptions are caught and reported as failures.
    """
    provider, model = _ping_target(obj, purpose)
    builder = _BUILDERS.get(provider)
    if builder is None:
        return False, f"Unknown provider {provider!r}", 0

    if provider == "ollama":
        kwargs: dict = {"num_predict": 1}
    else:
        kwargs = {"max_tokens": 1}

    started = time.perf_counter()
    try:
        llm = builder(model=model, temperature=0.0, format_json=False, kwargs=kwargs)
        safe_invoke(
            llm.invoke,
            [HumanMessage(content="ping")],
            timeout=15.0,
            thread_name_prefix=f"admin-ping-{purpose}",
        )
    except LLMConfigurationError as exc:
        return False, f"Configuration error: {exc}", 0
    except LLMMasterKillError as exc:
        return False, f"Master kill active: {exc}", 0
    except FuturesTimeoutError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return False, f"Timed out after {elapsed_ms} ms: {exc}", elapsed_ms
    except Exception as exc:  # provider client raises a wide variety of types
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("Admin LLM ping failed: provider=%s model=%s err=%s", provider, model, exc)
        return False, f"{type(exc).__name__}: {exc}", elapsed_ms

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return True, f"{provider} OK — {model} in {elapsed_ms} ms", elapsed_ms


@admin.register(SiteLLMConfig)
class SiteLLMConfigAdmin(SingletonModelAdmin):
    """Site-wide LLM provider configuration. Operator flips Ask/Modify per provider here."""

    form = SiteLLMConfigForm
    change_form_template = "admin/core/sitellmconfig/change_form.html"

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
                    "flipping the dropdown. Use the Test buttons at the bottom of "
                    "this page to verify the saved config can actually reach the "
                    "provider."
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
                    "provider-outage failover. (The Test buttons below ignore this "
                    "switch — they always probe the configured provider.)"
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

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "test-ask/",
                self.admin_site.admin_view(self.test_ask_view),
                name="sitellmconfig_test_ask",
            ),
            path(
                "test-modify/",
                self.admin_site.admin_view(self.test_modify_view),
                name="sitellmconfig_test_modify",
            ),
        ]
        return custom + urls

    def _dispatch_ping(self, request, purpose: str):
        if request.method != "POST":
            return redirect(self._change_url())
        obj = SiteLLMConfig.load()
        ok, detail, _ = _run_admin_ping(obj, purpose)
        label = "Ask" if purpose == "ask" else "Modify"
        if ok:
            messages.success(request, f"{label} ping: {detail}")
        else:
            messages.error(request, f"{label} ping FAILED: {detail}")
        return redirect(self._change_url())

    def test_ask_view(self, request):
        return self._dispatch_ping(request, "ask")

    def test_modify_view(self, request):
        return self._dispatch_ping(request, "modify")

    def _change_url(self) -> str:
        return reverse("admin:core_sitellmconfig_change", args=(SiteLLMConfig.load().pk,))

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


@admin.register(WeeklyDigestConfig)
class WeeklyDigestConfigAdmin(SingletonModelAdmin):
    """Weekly digest controls — on/off, recipients, day, included sections."""

    fieldsets = (
        (
            "Master switch",
            {
                "fields": ("enabled", "recipients", "send_day_of_week"),
                "description": (
                    "Crontab fires the command daily at 08:00 UTC; it checks "
                    '"Send day" below and only actually sends on that day. '
                    "Change the day here, never on the server."
                ),
            },
        ),
        (
            "Sections",
            {
                "fields": (
                    "include_investor_kpis",
                    "include_cost_summary",
                    "include_reliability_summary",
                    "include_engagement_summary",
                    "include_top_users_table",
                ),
            },
        ),
        (
            "Last send (audit)",
            {
                "fields": ("last_sent_at", "last_send_status", "last_send_log"),
                "classes": ("collapse",),
            },
        ),
        ("Updated", {"fields": ("updated_at",)}),
    )
    readonly_fields = (
        "last_sent_at",
        "last_send_status",
        "last_send_log",
        "updated_at",
    )
    actions = ["send_digest_now_action"]

    @admin.action(description="Send digest now (ignores enabled + send-day checks)")
    def send_digest_now_action(self, request, queryset):
        """One-click smoke test from the admin. Bypasses gates via ``force=True``.

        The admin's action signature requires a queryset, but the singleton
        only ever has one row, so ``queryset`` is ignored and we operate on
        the loaded config directly via ``digest_email.send_digest``.
        """
        result = digest_email.send_digest(force=True)
        level = (
            messages.SUCCESS
            if result.status == WeeklyDigestConfig.Status.SUCCESS
            else messages.WARNING
            if result.status.startswith("skipped_")
            else messages.ERROR
        )
        self.message_user(
            request,
            f"Digest {result.status}: {result.log[:300]}",
            level=level,
        )


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
