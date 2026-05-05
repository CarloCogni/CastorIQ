"""Core models - base classes and utilities."""

import uuid

from django.conf import settings
from django.db import models
from solo.models import SingletonModel


class TimestampedModel(models.Model):
    """Abstract base model with created/updated timestamps."""

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Created At",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    class Meta:
        abstract = True


class UUIDModel(TimestampedModel):
    """Abstract base model with UUID primary key."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="ID",
    )

    class Meta:
        abstract = True


class ErrorLog(UUIDModel):
    """Store application errors with full stacktrace for debugging"""

    class Severity(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        CRITICAL = "critical", "Critical"

    # Error details
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.ERROR,
        db_index=True,
        verbose_name="Severity",
    )
    message = models.TextField(
        verbose_name="Error Message",
        help_text="Short error description",
    )
    exception_type = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Exception Type",
        help_text="e.g., ValueError, KeyError, etc.",
    )

    # Full stacktrace
    stacktrace = models.TextField(
        verbose_name="Stack Trace",
        help_text="Full Python stacktrace",
    )  # Request context
    url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Request URL",
    )
    method = models.CharField(
        max_length=10,
        blank=True,
        verbose_name="HTTP Method",
        help_text="GET, POST, etc.",
    )
    view_name = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="View Name",
    )

    # User context
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_logs",
        verbose_name="User",
    )
    user_agent = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="User Agent",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="IP Address",
    )

    # Request data (be careful with sensitive data!)
    request_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Request Data",
        help_text="GET/POST parameters (sensitive data filtered)",
    )

    # Resolution tracking
    is_resolved = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Resolved",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_errors",
        verbose_name="Resolved By",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )
    resolution_note = models.TextField(
        blank=True,
        verbose_name="Resolution Note",
    )

    # Supabase sync tracking
    sent_to_supabase = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Sent to Supabase",
    )
    supabase_id = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        verbose_name="Supabase ID",
        help_text="UUID from Supabase to prevent duplicate imports",
    )
    original_username = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Original Username",
        help_text="Username from the original machine (for cross-machine imports)",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Error Log"
        verbose_name_plural = "Error Logs"
        indexes = [
            models.Index(fields=["-created_at", "severity"]),
            models.Index(fields=["is_resolved", "-created_at"]),
            models.Index(fields=["exception_type", "-created_at"]),
            models.Index(fields=["view_name", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.severity.upper()}: {self.message[:50]}"


class UserLLMConfig(models.Model):
    """
    Per-user UI + LLM preferences.

    Each user can choose their own Ollama model and UI theme.
    When `active_model` is blank, get_llm() falls back to settings.OLLAMA_MODEL.
    """

    class Theme(models.TextChoices):
        DARK = "dark", "Dark"
        LIGHT = "light", "Light"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llm_config",
        primary_key=True,
    )
    active_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Active Model",
        help_text="Ollama model tag (e.g. qwen3:14b). Blank = use .env default.",
    )
    theme = models.CharField(
        max_length=8,
        choices=Theme.choices,
        default=Theme.DARK,
        verbose_name="Theme",
        help_text="UI color theme.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User LLM Configuration"
        verbose_name_plural = "User LLM Configurations"

    def __str__(self):
        model_display = self.active_model or "(default)"
        return f"{self.user.username} → {model_display}"

    @classmethod
    def load(cls, user):
        """Get or create config for a given user."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


class TeamNote(UUIDModel):
    """Cross-machine team notes synced via Supabase."""

    class Category(models.TextChoices):
        BUG = "bug", "Bug"
        QUESTION = "question", "Question"
        SUGGESTION = "suggestion", "Suggestion"
        TODO = "todo", "To-Do"
        NOTE = "note", "Note"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    # Content
    title = models.CharField(
        max_length=200,
        verbose_name="Title",
    )
    body = models.TextField(
        verbose_name="Body",
        help_text="Rich-text HTML content",
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.NOTE,
        db_index=True,
        verbose_name="Category",
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
        verbose_name="Priority",
    )

    # Context — where was the user when they wrote this?
    page_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Page URL",
        help_text="Auto-captured from the browser",
    )
    browser_info = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Browser Info",
        help_text="User agent, screen size, etc.",
    )

    # Author as plain string — no FK, safe across machines
    author_username = models.CharField(
        max_length=150,
        db_index=True,
        verbose_name="Author",
        help_text="Username of the note creator",
    )

    # Resolution tracking
    is_resolved = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Resolved",
    )
    resolved_by = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Resolved By",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )
    resolution_note = models.TextField(
        blank=True,
        verbose_name="Resolution Note",
    )

    # Supabase sync tracking
    sent_to_supabase = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Sent to Supabase",
    )
    supabase_id = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        verbose_name="Supabase ID",
        help_text="UUID from Supabase to prevent duplicate imports",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Team Note"
        verbose_name_plural = "Team Notes"
        indexes = [
            models.Index(fields=["-created_at", "category"]),
            models.Index(fields=["is_resolved", "-created_at"]),
            models.Index(fields=["author_username", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.title[:50]} — {self.author_username}"


class SiteLLMConfig(SingletonModel):
    """
    Site-wide LLM provider configuration (one row, ever).

    The dispatcher in ``core.llm`` reads this singleton at call time so the
    operator can flip Ask, Modify, or both between providers from Django admin
    without a deploy. Local Ollama is always a first-class option, never just
    a fallback.

    On first read, ``load()`` seeds the row from settings env vars
    (``ASK_PROVIDER``, ``ASK_MODEL``, ``MODIFY_PROVIDER``, ``MODIFY_MODEL``).
    """

    class Provider(models.TextChoices):
        OLLAMA = "ollama", "Ollama (local)"
        ANTHROPIC = "anthropic", "Anthropic Claude"
        GROQ = "groq", "Groq"

    ask_provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.OLLAMA,
        verbose_name="Ask Provider",
        help_text="Provider used by the Ask (RAG) pipeline.",
    )
    ask_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Ask Model",
        help_text="Model identifier for the Ask provider. Blank = .env default.",
    )
    modify_provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.OLLAMA,
        verbose_name="Modify Provider",
        help_text="Provider used by the Modify (writeback) pipeline.",
    )
    modify_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Modify Model",
        help_text="Model identifier for the Modify provider. Blank = .env default.",
    )
    force_local_ollama = models.BooleanField(
        default=False,
        verbose_name="Force Local Ollama",
        help_text=(
            "Emergency override: route every LLM call to local Ollama, ignoring "
            "the per-purpose provider settings above. Useful for cost-free testing "
            "or provider-outage failover."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site LLM Configuration"
        verbose_name_plural = "Site LLM Configuration"

    def __str__(self) -> str:
        if self.force_local_ollama:
            return "Site LLM (force local Ollama)"
        return f"Ask={self.ask_provider} · Modify={self.modify_provider}"

    @classmethod
    def load(cls) -> "SiteLLMConfig":
        """Get-or-create the singleton, seeding defaults from settings on first read."""
        obj, created = cls.objects.get_or_create(pk=1)
        if created:
            obj.ask_provider = settings.ASK_PROVIDER
            obj.modify_provider = settings.MODIFY_PROVIDER
            # Cloud-provider models live in ask_model / modify_model. When the seeded
            # provider is Ollama, leave them blank so resolve() falls back to
            # settings.OLLAMA_MODEL — otherwise we'd persist a Claude/Llama name on
            # an Ollama provider, which is meaningless.
            obj.ask_model = settings.ASK_MODEL if obj.ask_provider != cls.Provider.OLLAMA else ""
            obj.modify_model = (
                settings.MODIFY_MODEL if obj.modify_provider != cls.Provider.OLLAMA else ""
            )
            obj.save()
        return obj

    def resolve(self, purpose: str) -> tuple[str, str]:
        """Return (provider, model) for a given call-site purpose ('ask' | 'modify')."""
        if self.force_local_ollama:
            return ("ollama", settings.OLLAMA_MODEL)
        if purpose == "ask":
            provider, model = self.ask_provider, self.ask_model
            cloud_default = settings.ASK_MODEL
        elif purpose == "modify":
            provider, model = self.modify_provider, self.modify_model
            cloud_default = settings.MODIFY_MODEL
        else:
            raise ValueError(f"Unknown LLM purpose: {purpose!r} (expected 'ask' | 'modify')")
        provider = str(provider)
        if provider == "ollama":
            return ("ollama", settings.OLLAMA_MODEL)
        return (provider, model or cloud_default)
