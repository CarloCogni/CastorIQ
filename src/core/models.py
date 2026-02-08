"""Core models - base classes and utilities."""

import uuid

from django.db import models
from django.conf import settings

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
    )

    # Request context
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

