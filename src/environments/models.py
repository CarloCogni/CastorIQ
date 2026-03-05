"""Project models - workspace management."""

from django.conf import settings
from django.db import models

from core.models import UUIDModel


class Project(UUIDModel):
    """
    A Project is a workspace containing IFC files and related documents.
    Each project has its own Git repository for version control.
    """

    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Project Name",
        help_text="A descriptive name for the project",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
        help_text="Brief description of the project scope and purpose",
    )

    # Owner and collaborators
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_projects",
        verbose_name="Owner",
        help_text="The user who created and owns this project",
    )
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="collaborated_projects",
        blank=True,
        verbose_name="Collaborators",
        help_text="Users who can access and work on this project",
    )

    # Git repository path for IFC versioning
    git_repo_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Git Repository Path",
        help_text="Local path to the Git repository for version control",
    )

    # Project status
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Archived",
        help_text="Archived projects are hidden from the main list",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["is_archived", "-created_at"]),
        ]

    def __str__(self):
        return self.name

    def user_has_access(self, user):
        """Check if user has access to this project."""
        return self.owner == user or self.collaborators.filter(pk=user.pk).exists()


class ProjectMembership(UUIDModel):
    """Track project membership with roles."""

    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        EDITOR = "editor", "Editor"
        ADMIN = "admin", "Admin"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Project",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
        verbose_name="User",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        verbose_name="Role",
        help_text="Permission level for this user in the project",
    )

    class Meta:
        verbose_name = "Project Membership"
        verbose_name_plural = "Project Memberships"
        unique_together = ["project", "user"]
        indexes = [
            models.Index(fields=["user", "role"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.project.name} ({self.role})"
