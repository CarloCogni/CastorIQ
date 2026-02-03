"""IFC Processor models."""

from django.db import models
from pgvector.django import VectorField

from core.models import TimestampedModel
from environments.models import Environment


class IfcFile(TimestampedModel):
    """An uploaded IFC file."""

    environment = models.ForeignKey(
        Environment, on_delete=models.CASCADE, related_name="ifc_files"
    )
    name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_hash = models.CharField(max_length=64)  # SHA-256

    # Metadata extracted from IFC
    schema_version = models.CharField(max_length=20, blank=True)
    project_name = models.CharField(max_length=255, blank=True)

    # Processing status
    is_processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class IfcEntity(TimestampedModel):
    """An extracted entity from an IFC file."""

    ifc_file = models.ForeignKey(
        IfcFile, on_delete=models.CASCADE, related_name="entities"
    )
    global_id = models.CharField(max_length=64)  # IFC GUID
    ifc_type = models.CharField(max_length=100)  # e.g., IfcDoor, IfcWall
    name = models.CharField(max_length=255, blank=True)

    # Spatial location
    building_storey = models.CharField(max_length=255, blank=True)
    space = models.CharField(max_length=255, blank=True)

    # Properties as JSON
    properties = models.JSONField(default=dict)

    # Semantic description for embedding
    description = models.TextField(blank=True)

    # Vector embedding
    embedding = VectorField(dimensions=768, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["ifc_type"]),
            models.Index(fields=["global_id"]),
        ]

    def __str__(self):
        return f"{self.ifc_type}: {self.name or self.global_id}"
