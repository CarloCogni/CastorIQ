"""Environment models."""

from django.contrib.auth.models import User
from django.db import models

from core.models import TimestampedModel


class Environment(TimestampedModel):
    """
    An Environment is a workspace containing IFC files and related documents.
    Users can create multiple environments for different projects.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="environments")

    # Git repository path for IFC versioning
    git_repo_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.owner.username})"
