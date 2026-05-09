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


class SiteLaunchConfig(SingletonModel):
    """
    Public-face state for the unauthenticated `/` URL (one row, ever).

    Three states:
    - ``coming_soon``: pre-launch splash, "Stay tuned. BIM is about to be redefined."
    - ``live``: the real beta landing page (hero + features + application form)
    - ``maintenance``: post-launch outage splash, "Be right back."

    Flippable from /admin/core/sitelaunchconfig/ — no deploy needed. The
    authenticated app surface (projects, Ask, Modify, admin) is unaffected by
    state; only the public face and the beta-application form are gated.
    """

    class State(models.TextChoices):
        COMING_SOON = "coming_soon", "Coming soon (pre-launch splash)"
        LIVE = "live", "Live (full landing page)"
        MAINTENANCE = "maintenance", "Under maintenance"

    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.COMING_SOON,
        verbose_name="Public-face state",
        help_text=(
            "Controls what unauthenticated visitors see at /. "
            "'live' shows the real landing page and accepts beta applications; "
            "'coming_soon' and 'maintenance' both show a Matrix-rain splash and "
            "block the application form."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Launch Configuration"
        verbose_name_plural = "Site Launch Configuration"

    def __str__(self) -> str:
        return f"State: {self.get_state_display()}"

    @classmethod
    def load(cls) -> "SiteLaunchConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_live(self) -> bool:
        return self.state == self.State.LIVE


class UserTokenBudget(models.Model):
    """
    Per-user daily LLM token cap.

    The dispatcher checks ``would_exceed(estimated)`` before each cloud call
    and increments ``used_today`` after the response lands (post-call
    reconciliation against ``response.usage_metadata`` — real numbers, not
    heuristics). The pre-paid provider balance is the *hard* cap; this is a
    *soft* second layer so one runaway user can't drain the pool.

    Reset is lazy: ``reset_if_new_day()`` zeroes ``used_today`` on the first
    call after midnight UTC. No cron required.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="token_budget",
        primary_key=True,
    )
    daily_cap = models.PositiveIntegerField(
        default=50_000,
        verbose_name="Daily Cap (tokens)",
        help_text="Total tokens (input + output) allowed per UTC day. 0 = unlimited.",
    )
    used_today = models.PositiveIntegerField(
        default=0,
        verbose_name="Used Today",
        help_text="Tokens consumed since the last reset.",
    )
    last_reset_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Reset",
    )
    hard_blocked = models.BooleanField(
        default=False,
        verbose_name="Hard Blocked",
        help_text="When set, refuse all cloud LLM calls regardless of remaining budget.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Token Budget"
        verbose_name_plural = "User Token Budgets"

    def __str__(self) -> str:
        return f"{self.user.username} — {self.used_today}/{self.daily_cap}"

    @classmethod
    def load(cls, user) -> "UserTokenBudget":
        """Get-or-create the budget row for a user."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def reset_if_new_day(self) -> bool:
        """Zero ``used_today`` if we've crossed midnight UTC. Returns True on reset."""
        from django.utils import timezone

        now = timezone.now()
        if self.last_reset_at is None or self.last_reset_at.date() != now.date():
            self.used_today = 0
            self.last_reset_at = now
            self.save(update_fields=["used_today", "last_reset_at", "updated_at"])
            return True
        return False

    def would_exceed(self, estimated_tokens: int) -> bool:
        """True if recording ``estimated_tokens`` would push past the daily cap."""
        if self.hard_blocked:
            return True
        if self.daily_cap == 0:
            return False
        return self.used_today + estimated_tokens > self.daily_cap

    def record_usage(self, tokens: int) -> None:
        """Increment ``used_today`` after a real LLM response lands."""
        if tokens <= 0:
            return
        self.used_today = (self.used_today or 0) + tokens
        self.save(update_fields=["used_today", "updated_at"])

    @property
    def remaining(self) -> int:
        if self.daily_cap == 0:
            return 0
        return max(0, self.daily_cap - self.used_today)


class LLMCallLog(models.Model):
    """
    One row per LLM invocation. Pure observability — never read on the hot path.

    Lets us answer "did anyone go nuts last week?" and reconcile internal cost
    estimates against the provider dashboards. Index covers the two access
    patterns: filter by user (admin user inspector) and filter by created_at
    (range queries / weekly review).
    """

    class Purpose(models.TextChoices):
        ASK = "ask", "Ask"
        MODIFY = "modify", "Modify"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="llm_calls",
    )
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=100, blank=True)
    purpose = models.CharField(max_length=10, choices=Purpose.choices, db_index=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=0,
        help_text="Estimated USD cost — provider price table × token counts.",
    )
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    succeeded = models.BooleanField(default=True, db_index=True)
    error_type = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "LLM Call Log"
        verbose_name_plural = "LLM Call Logs"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["provider", "-created_at"]),
        ]

    def __str__(self) -> str:
        u = self.user.username if self.user else "(anon)"
        return f"{u} · {self.provider}/{self.model} · {self.tokens_in}+{self.tokens_out}"

    @property
    def total_tokens(self) -> int:
        return (self.tokens_in or 0) + (self.tokens_out or 0)
