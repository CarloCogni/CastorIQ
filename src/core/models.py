"""Core models - base classes and utilities."""

import uuid

from django.conf import settings
from django.db import models


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
    Per-user LLM preference.

    Each user can choose their own Ollama model.
    When blank, get_llm() falls back to settings.OLLAMA_MODEL.
    Future: enables per-model billing based on user's choice.
    """

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
