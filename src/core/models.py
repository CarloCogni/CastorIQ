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

    Each user can choose their own Ollama model, UI theme, and — via BYOK —
    paste their own Anthropic / Groq API key with a curated model selection.
    When ``active_model`` is blank, get_llm() falls back to settings.OLLAMA_MODEL.
    When ``{ask,modify}_provider_override`` is blank, get_llm() reads from
    SiteLLMConfig (the developer-paid managed pool).

    BYOK API keys are stored as Fernet ciphertext in ``*_api_key_ciphertext``
    fields. The plaintext is accessed via property-style getters/setters that
    route through ``core.crypto`` so plaintext never lives on the instance.
    """

    class Theme(models.TextChoices):
        DARK = "dark", "Dark"
        LIGHT = "light", "Light"

    class ProviderOverride(models.TextChoices):
        SITE_DEFAULT = "", "Use site default"
        OLLAMA = "ollama", "Local Ollama"
        ANTHROPIC = "anthropic", "Anthropic (my key)"
        GROQ = "groq", "Groq (my key)"

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

    # ── BYOK (Bring Your Own Key) ─────────────────────────────────────────
    # Ciphertext is Fernet-encrypted; never stored or logged in plaintext.
    # Hint is the masked display form (first 7 + … + last 5), safe to render.
    anthropic_api_key_ciphertext = models.TextField(
        blank=True,
        default="",
        verbose_name="Anthropic API key (encrypted)",
    )
    anthropic_api_key_hint = models.CharField(
        max_length=24,
        blank=True,
        default="",
        verbose_name="Anthropic key hint",
        help_text="Masked display form, e.g. 'sk-ant-…12345'.",
    )
    groq_api_key_ciphertext = models.TextField(
        blank=True,
        default="",
        verbose_name="Groq API key (encrypted)",
    )
    groq_api_key_hint = models.CharField(
        max_length=24,
        blank=True,
        default="",
        verbose_name="Groq key hint",
    )

    ask_provider_override = models.CharField(
        max_length=20,
        choices=ProviderOverride.choices,
        blank=True,
        default="",
        verbose_name="Ask provider override",
        help_text="Per-purpose override. Blank = follow SiteLLMConfig.",
    )
    modify_provider_override = models.CharField(
        max_length=20,
        choices=ProviderOverride.choices,
        blank=True,
        default="",
        verbose_name="Modify provider override",
    )
    anthropic_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Anthropic model",
        help_text="Curated Anthropic model identifier (see core.llm_catalog).",
    )
    groq_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Groq model",
        help_text="Curated Groq model identifier (see core.llm_catalog).",
    )
    keys_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Keys last updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User LLM Configuration"
        verbose_name_plural = "User LLM Configurations"

    def __str__(self):
        # NEVER reference ciphertext or hint fields here — __str__ is logged.
        model_display = self.active_model or "(default)"
        return f"{self.user.username} → {model_display}"

    @classmethod
    def load(cls, user):
        """Get or create config for a given user."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    # ── BYOK plaintext accessors ──────────────────────────────────────────
    # Properties route through core.crypto so plaintext lives only on the
    # caller's stack frame; it is never assigned to ``self``.

    @property
    def anthropic_api_key(self) -> str | None:
        """Decrypted Anthropic API key, or None if not set / unreadable."""
        from core.crypto import decrypt

        return decrypt(self.anthropic_api_key_ciphertext)

    @property
    def groq_api_key(self) -> str | None:
        """Decrypted Groq API key, or None if not set / unreadable."""
        from core.crypto import decrypt

        return decrypt(self.groq_api_key_ciphertext)

    def has_byok_key(self, provider: str) -> bool:
        """True if the user has stored a non-empty ciphertext for ``provider``."""
        if provider == "anthropic":
            return bool(self.anthropic_api_key_ciphertext)
        if provider == "groq":
            return bool(self.groq_api_key_ciphertext)
        return False


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
    notify_operator_on_application = models.BooleanField(
        default=True,
        verbose_name="Email me on each new beta application",
        help_text=(
            "When on, OPERATOR_NOTIFICATION_EMAIL receives a one-line ping for every "
            "submission with a deep link to the admin row. Turn off to silence the "
            "operator copy without redeploying — applicants always receive their "
            "confirmation regardless of this setting."
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


class WeeklyDigestConfig(SingletonModel):
    """
    Admin-controlled config for the weekly operator digest email.

    The dashboard family (``/staff/dashboard/*``) is reactive — the operator
    has to remember to open it. The digest closes the loop: a Monday-morning
    summary composed from the same ``core/services/usage_analytics.py``
    helpers, sent to an admin-managed recipient list. All knobs live here so
    nothing about the schedule or recipient list requires a redeploy.

    The crontab on the VPS fires the command daily at 08:00 UTC; the command
    itself checks ``send_day_of_week`` and skips on other days. That split
    means the operator changes the schedule from this admin page rather than
    SSH-ing into the server to edit crontab.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED_DISABLED = "skipped_disabled", "Skipped — disabled"
        SKIPPED_WRONG_DAY = "skipped_wrong_day", "Skipped — wrong day"
        SKIPPED_NO_RECIPIENTS = "skipped_no_recipients", "Skipped — no recipients"
        SKIPPED_QUOTA = "skipped_quota", "Skipped — Brevo daily cap"

    DAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    enabled = models.BooleanField(
        default=False,
        verbose_name="Send the weekly digest",
        help_text=(
            "Master kill-switch. When off, the cron job runs but the command "
            "returns 'skipped_disabled' without sending anything."
        ),
    )
    recipients = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Recipient emails",
        help_text=(
            "JSON list of email addresses, e.g. "
            '["you@castoriq.io", "cofounder@castoriq.io"]. '
            "The first address goes in To:, the rest are Bcc'd. "
            "Invalid addresses are rejected at save time."
        ),
    )
    send_day_of_week = models.IntegerField(
        default=0,
        choices=DAY_CHOICES,
        verbose_name="Send day",
        help_text="Day of week the command actually sends. Monday is the default.",
    )
    include_investor_kpis = models.BooleanField(
        default=True,
        verbose_name="Section · Investor KPIs",
        help_text="MAU, W4 retention, entities processed, Ollama-local share.",
    )
    include_cost_summary = models.BooleanField(
        default=True,
        verbose_name="Section · Cost summary",
        help_text="7-day cost USD, top user, Ollama vs paid mix.",
    )
    include_reliability_summary = models.BooleanField(
        default=True,
        verbose_name="Section · Reliability summary",
        help_text="Success %, p95 latency Ask/Modify, open ErrorLog count.",
    )
    include_engagement_summary = models.BooleanField(
        default=True,
        verbose_name="Section · Engagement summary",
        help_text="DAU/WAU/MAU, cohort W4 retention, proposal-generator share.",
    )
    include_top_users_table = models.BooleanField(
        default=False,
        verbose_name="Section · Top users table",
        help_text=(
            "Verbose top-10 by cost. Off by default — keep the digest scannable. "
            "Drill into /staff/dashboard/overview/ when you actually need the list."
        ),
    )

    # Audit trail — readonly in admin.
    last_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last send attempt",
    )
    last_send_status = models.CharField(
        max_length=30,
        choices=Status.choices,
        blank=True,
        default="",
        verbose_name="Last status",
    )
    last_send_log = models.TextField(
        blank=True,
        default="",
        verbose_name="Last send log",
        help_text="Truncated stdout / error from the most recent send attempt.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Weekly Digest Configuration"
        verbose_name_plural = "Weekly Digest Configuration"

    def __str__(self) -> str:
        state = "ON" if self.enabled else "OFF"
        return f"Weekly digest: {state} ({len(self.recipients or [])} recipients)"

    @classmethod
    def load(cls) -> "WeeklyDigestConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def clean(self) -> None:
        """Reject the save if any recipient isn't a syntactically valid email.

        Per-entry validation surfaces typos at save time rather than
        silently dropping them at send time. ``validate_email`` raises
        ``ValidationError`` which Django's admin renders inline next to
        the field.
        """
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        super().clean()
        recipients = self.recipients or []
        if not isinstance(recipients, list):
            raise ValidationError({"recipients": "Must be a JSON list of email strings."})
        bad: list[str] = []
        for entry in recipients:
            if not isinstance(entry, str):
                bad.append(repr(entry))
                continue
            try:
                validate_email(entry)
            except ValidationError:
                bad.append(entry)
        if bad:
            raise ValidationError({"recipients": f"Invalid email address(es): {', '.join(bad)}"})


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


class SiteStorageConfig(SingletonModel):
    """
    Site-wide storage caps (one row, ever).

    Two knobs the operator tunes from the admin without a redeploy:

    * ``default_per_user_quota_bytes`` — fallback cap applied when a
      :class:`UserStorageQuota` row has ``quota_bytes == 0``. The default
      (1 GB) is sized for the Barcelona beta on the Hetzner CCX13 +
      ~100 GB Cloud Volume (see ``docs/business/VPS-extra-storage-setup.md``).
    * ``per_file_cap_bytes`` — hard ceiling on any single upload (IFC or
      document). Replaces the previously hardcoded 100 MB IFC limit.

    Numbers stored as bytes so the admin form shows a single integer field;
    the dashboard formats it for humans.
    """

    default_per_user_quota_bytes = models.PositiveBigIntegerField(
        default=1 * 1024**3,  # 1 GB
        verbose_name="Default per-user quota (bytes)",
        help_text=(
            "Total bytes a user can occupy across all their projects (files + "
            "per-project Git history). Applied when the user's row has "
            "quota_bytes=0. 1 GB by default."
        ),
    )
    per_file_cap_bytes = models.PositiveBigIntegerField(
        default=200 * 1024**2,  # 200 MB
        verbose_name="Per-file upload cap (bytes)",
        help_text=(
            "Maximum size of any single uploaded file (IFC or document). "
            "Enforced at upload time before the per-user quota check. "
            "200 MB by default."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Storage Configuration"
        verbose_name_plural = "Site Storage Configuration"

    def __str__(self) -> str:
        return (
            f"per-user={self.default_per_user_quota_bytes} B · per-file={self.per_file_cap_bytes} B"
        )

    @classmethod
    def load(cls) -> "SiteStorageConfig":
        """Get-or-create the singleton with default caps on first read."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class UserStorageQuota(models.Model):
    """
    Per-user storage cap (files + per-project Git history).

    Mirrors :class:`UserTokenBudget` minus the daily-reset: storage is
    cumulative, not periodic. Computation is on-demand via
    ``core.services.storage_usage.compute_user_storage`` — we walk
    ``MEDIA_ROOT`` rather than tracking incremental deltas, because the
    per-project Git repo grows on every Modify approval without the upload
    path knowing about it.

    ``quota_bytes=0`` is a sentinel meaning "use the site default" so the
    operator can raise an individual user's cap without rewriting the
    singleton, and reverting is a single field clear.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="storage_quota",
        primary_key=True,
    )
    quota_bytes = models.PositiveBigIntegerField(
        default=0,
        verbose_name="Quota override (bytes)",
        help_text="0 = use SiteStorageConfig.default_per_user_quota_bytes.",
    )
    cached_used_bytes = models.PositiveBigIntegerField(
        default=0,
        verbose_name="Used (cached bytes)",
        help_text="Last computed total. Recalculated on upload and on the projects page.",
    )
    hard_blocked = models.BooleanField(
        default=False,
        verbose_name="Hard Blocked",
        help_text="When set, refuse all uploads regardless of remaining quota.",
    )
    last_recalculated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last recalculated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Storage Quota"
        verbose_name_plural = "User Storage Quotas"

    def __str__(self) -> str:
        return f"{self.user.username} — {self.cached_used_bytes}/{self.effective_quota()} B"

    @classmethod
    def load(cls, user) -> "UserStorageQuota":
        """Get-or-create the storage quota row for a user."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def effective_quota(self) -> int:
        """Per-user override when set, otherwise the site default."""
        if self.quota_bytes:
            return self.quota_bytes
        return SiteStorageConfig.load().default_per_user_quota_bytes

    def would_exceed(self, estimated_bytes: int) -> bool:
        """True if adding ``estimated_bytes`` would push past the effective quota."""
        if self.hard_blocked:
            return True
        cap = self.effective_quota()
        if cap == 0:
            return False
        return self.cached_used_bytes + max(0, estimated_bytes) > cap

    def recalculate(self) -> int:
        """Walk MEDIA_ROOT for this user's projects and refresh ``cached_used_bytes``.

        Returns the new total in bytes.
        """
        from django.utils import timezone

        from core.services.storage_usage import compute_user_storage

        breakdown = compute_user_storage(self.user)
        self.cached_used_bytes = breakdown.total_bytes
        self.last_recalculated_at = timezone.now()
        self.save(update_fields=["cached_used_bytes", "last_recalculated_at", "updated_at"])
        return breakdown.total_bytes

    def record_upload(self, bytes_added: int) -> None:
        """Cheap incremental bump after a successful upload.

        Drift is corrected by the next ``recalculate()`` call on the projects
        page or admin action — incremental tracking is just to avoid a full
        disk walk on every quota check inside a single request.
        """
        if bytes_added <= 0:
            return
        self.cached_used_bytes = (self.cached_used_bytes or 0) + bytes_added
        self.save(update_fields=["cached_used_bytes", "updated_at"])

    @property
    def remaining_bytes(self) -> int:
        cap = self.effective_quota()
        if cap == 0:
            return 0
        return max(0, cap - self.cached_used_bytes)

    @property
    def pct_used(self) -> int:
        cap = self.effective_quota()
        if cap == 0:
            return 0
        return min(100, int((self.cached_used_bytes / cap) * 100))


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
    byok = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="BYOK",
        help_text="True if this call used the user's own API key (excluded from managed-pool spend).",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "LLM Call Log"
        verbose_name_plural = "LLM Call Logs"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["provider", "-created_at"]),
            models.Index(fields=["byok", "-created_at"]),
        ]

    def __str__(self) -> str:
        u = self.user.username if self.user else "(anon)"
        return f"{u} · {self.provider}/{self.model} · {self.tokens_in}+{self.tokens_out}"

    @property
    def total_tokens(self) -> int:
        return (self.tokens_in or 0) + (self.tokens_out or 0)
