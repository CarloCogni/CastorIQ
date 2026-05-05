# beta/models.py
"""Beta application — public funnel into Castor."""

import uuid

from django.conf import settings
from django.db import models


class BetaApplication(models.Model):
    """
    A request to join the Castor beta.

    Submitted unauthenticated from the public landing page. The operator
    reviews each row in Django admin, optionally schedules a 10-min intro
    call, then approves — at which point a User is created (M3.6) and a
    welcome email with a one-time set-password link is dispatched.

    There is no separate "invite token" model: approval triggers Django's
    built-in PasswordResetConfirmView flow, which already provides
    time-bounded single-use tokens.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        CALLED = "called", "Intro call scheduled"
        APPROVED = "approved", "Approved (account created)"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(
        unique=True,
        verbose_name="Email",
        help_text="Used as the username when an account is created on approval.",
    )
    name = models.CharField(max_length=120, verbose_name="Full name")
    job_title = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Role / job title",
    )
    description = models.TextField(
        verbose_name="Why are you interested?",
        help_text="What the applicant wants to use Castor for.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Operator notes",
        help_text="Private — call summary, vetting context, anything not for the applicant.",
    )

    # Soft anti-bot context — IP + user-agent recorded at submission to support
    # rate-limit decisions and post-hoc spam triage. Honeypot field is checked
    # at form-handler time and never persisted.
    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    submitted_user_agent = models.CharField(max_length=500, blank=True, default="")

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # If approval succeeds, hold a soft pointer to the created User so the
    # admin can jump from application to account.
    created_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="beta_application",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Beta Application"
        verbose_name_plural = "Beta Applications"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.get_status_display()})"
