"""IFC Processor models - IFC file and entity management."""

import hashlib

from django.db import models
from pgvector.django import VectorField

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.services.validators import IFCFileValidator


def ifc_upload_path(instance, filename):
    """Generate upload path for IFC files."""
    return f"projects/{instance.project_id}/ifc/{filename}"


class IFCFile(UUIDModel):
    """An uploaded IFC file within a project."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="ifc_files",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="File Name",
    )
    file = models.FileField(
        upload_to=ifc_upload_path,
        verbose_name="File",
        max_length=2_000,
        help_text="IFC file (.ifc format)",
        validators=[IFCFileValidator()],
    )
    file_hash = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="File Hash",
        help_text="SHA-256 hash for detecting changes",
    )

    # Metadata extracted from IFC
    schema_version = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="IFC Schema Version",
        help_text="e.g., IFC2X3, IFC4",
    )
    project_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="IFC Project Name",
        help_text="Project name extracted from IFC header",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )

    # Processing status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Processed At",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
        help_text="Error details if processing failed",
    )

    # Statistics
    entity_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Entity Count",
        help_text="Number of IFC entities extracted",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "IFC File"
        verbose_name_plural = "IFC Files"
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self):
        return self.name

    def calculate_hash(self):
        """Calculate SHA-256 hash of the file."""
        sha256 = hashlib.sha256()
        for chunk in self.file.chunks():
            sha256.update(chunk)
        return sha256.hexdigest()

    @property
    def needs_conversion(self) -> bool:
        """True if this file has a legacy schema requiring conversion to IFC4 before processing."""
        return bool(self.schema_version) and not self.schema_version.upper().startswith("IFC4")


class IFCEntity(UUIDModel):
    """An extracted entity from an IFC file."""

    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="entities",
        verbose_name="IFC File",
    )

    # IFC identifiers
    global_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Global ID",
        help_text="Unique IFC GUID",
    )
    ifc_type = models.CharField(
        max_length=2_000,
        db_index=True,
        verbose_name="IFC Type",
        help_text="e.g., IfcDoor, IfcWall, IfcWindow",
    )
    name = models.CharField(
        max_length=2_000,
        blank=True,
        db_index=True,
        verbose_name="Name",
    )

    # Spatial hierarchy
    building = models.CharField(
        max_length=2_000,
        blank=True,
        verbose_name="Building",
    )
    building_storey = models.CharField(
        max_length=2_000,
        blank=True,
        db_index=True,
        verbose_name="Building Storey",
        help_text="Floor/level where this entity is located",
    )
    space = models.CharField(
        max_length=2_000,
        blank=True,
        verbose_name="Space",
        help_text="Room or space containing this entity",
    )

    # Properties stored as JSON
    properties = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Properties",
        help_text="IFC properties and property sets as JSON",
    )

    # Semantic description for RAG
    description = models.TextField(
        blank=True,
        verbose_name="Description",
        help_text="AI-generated semantic description for search",
    )

    # Vector embedding for similarity search
    embedding = VectorField(
        dimensions=1024,  # mxbai-embed-large
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["ifc_type", "name"]
        verbose_name = "IFC Entity"
        verbose_name_plural = "IFC Entities"
        unique_together = ["ifc_file", "global_id"]
        indexes = [
            models.Index(fields=["ifc_file", "ifc_type"]),
            models.Index(fields=["ifc_file", "global_id"]),
            models.Index(fields=["ifc_file", "building_storey"]),
            models.Index(fields=["ifc_type", "name"]),
        ]

    def __str__(self):
        return f"{self.ifc_type}: {self.name or self.global_id[:8]}"


class IFCDataIssue(UUIDModel):
    """Stores information about invalid or duplicate data found in an IFC file."""

    class IssueType(models.TextChoices):
        DUPLICATE_GUID = "duplicate_guid", "Duplicate GlobalID"
        INVALID_GEOMETRY = "invalid_geometry", "Invalid Geometry"
        MISSING_PROPERTY = "missing_property", "Missing Required Property"
        ORPHANED_ELEMENT = "orphaned_element", "Orphaned Element (No Spatial Placement)"

    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="data_issues",
    )

    issue_type = models.CharField(max_length=50, choices=IssueType.choices)
    global_id = models.CharField(max_length=64)
    ifc_type = models.CharField(max_length=100)

    # Store the "rejected" data as JSON so the user can see what was skipped
    raw_data = models.JSONField(help_text="The properties and metadata of the conflicting element.")

    description = models.TextField()
    is_resolved = models.BooleanField(default=False)

    class Meta:
        verbose_name = "IFC Data Issue"
        verbose_name_plural = "IFC Data Issues"
