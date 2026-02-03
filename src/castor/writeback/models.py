"""Writeback models."""

from django.contrib.auth.models import User
from django.db import models

from core.models import TimestampedModel
from ifc_processor.models import IfcFile


class ModificationProposal(TimestampedModel):
    """A proposed modification to an IFC file."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        APPLIED = "applied", "Applied"

    ifc_file = models.ForeignKey(
        IfcFile, on_delete=models.CASCADE, related_name="modification_proposals"
    )
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    # The natural language request that triggered this
    original_request = models.TextField()

    # The proposed changes
    changes = models.JSONField()  # List of entity modifications

    # Human-readable diff
    diff_preview = models.TextField()

    # Status tracking
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Git commit reference (after applying)
    git_commit_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Modification: {self.original_request[:50]}..."
