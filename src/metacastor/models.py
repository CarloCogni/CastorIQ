# metacastor/models.py
"""
MetaCastor models — production observability for Castor's writeback pipeline.

  - FailureRecord: captures pipeline failures with deterministic taxonomy + diagnosis
"""

from django.db import models
from pgvector.django import VectorField

from core.models import TimestampedModel
from environments.models import Project


class FailureRecord(TimestampedModel):
    """
    A record of a failed modification pipeline attempt.

    Created automatically whenever propose() or execute() raises a caught exception.
    Provides deterministic error classification, human-readable diagnosis, and
    RETRYABLE vs NON_RETRYABLE guidance. RETRYABLE failures can feed their context
    back into the next classify() call via build_failure_context().
    """

    class FailurePhase(models.TextChoices):
        VALIDATION = "VALIDATION", "Validation"
        EXECUTION = "EXECUTION", "Execution"
        SANDBOX = "SANDBOX", "Sandbox"

    class Category(models.TextChoices):
        RETRYABLE = "RETRYABLE", "Retryable"
        NON_RETRYABLE = "NON_RETRYABLE", "Non-Retryable"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="failure_records",
    )
    proposal = models.ForeignKey(
        "writeback.ModificationProposal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="failure_records",
        help_text="Null for VALIDATION-phase failures (no proposal created yet).",
    )
    query_text = models.TextField(verbose_name="Query")
    query_embedding = VectorField(
        dimensions=1024,
        null=True,
        blank=True,
        help_text="Nullable — Ollama may be unavailable at failure time.",
    )
    intent_json = models.JSONField(default=dict, verbose_name="Intent")
    tier = models.IntegerField(
        null=True,
        blank=True,
        help_text="RSAA tier at failure time. Null for early VALIDATION failures.",
    )
    failure_phase = models.CharField(
        max_length=20,
        choices=FailurePhase.choices,
        verbose_name="Phase",
    )
    error_type = models.CharField(max_length=40, verbose_name="Error Type")
    error_detail = models.TextField(verbose_name="Error Detail")
    diagnosis = models.TextField(verbose_name="Diagnosis")
    ifc_context = models.JSONField(
        default=dict,
        help_text="Extracted IFC context: {operation, ifc_type, pset, property, value}.",
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        verbose_name="Category",
    )

    class Meta:
        verbose_name = "Failure Record"
        verbose_name_plural = "Failure Records"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "category", "-created_at"]),
            models.Index(fields=["error_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.error_type}|{self.category}] {self.query_text[:60]}"
