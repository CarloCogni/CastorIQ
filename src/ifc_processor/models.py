"""IFC Processor models - IFC file and entity management."""

import hashlib

from django.db import models
from django.utils import timezone
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
    project_units = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Project Units",
        help_text='Declared units from IfcUnitAssignment, e.g. {"LENGTHUNIT": "mm", "AREAUNIT": "m²"}',
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


class IFCElementType(UUIDModel):
    """
    A defining type object from an IFC file (IfcTypeProduct subtypes).

    Mirrors the IFC schema hierarchy where IfcTypeProduct (and its subtypes
    like IfcBeamType, IfcDoorType, IfcWallType, etc.) define shared
    characteristics for multiple element occurrences.

    Each occurrence (IFCEntity) links back here via a FK, enabling
    type-grouped schedules and future write-back on type definitions.
    """

    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="element_types",
        verbose_name="IFC File",
    )

    # IFC identifiers
    global_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Global ID",
        help_text="Unique IFC GUID of the type object",
    )
    ifc_type = models.CharField(
        max_length=2_000,
        db_index=True,
        verbose_name="IFC Type",
        help_text="e.g., IfcDoorType, IfcBeamType, IfcWallType",
    )
    name = models.CharField(
        max_length=2_000,
        blank=True,
        db_index=True,
        verbose_name="Name",
        help_text="Type name, e.g. 'Door-Single-Panel', 'Standard Footing 300x400'",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )

    # IFC attributes on IfcTypeProduct
    applicable_occurrence = models.CharField(
        max_length=2_000,
        blank=True,
        verbose_name="Applicable Occurrence",
        help_text="IFC ApplicableOccurrence attribute — subtype filter string",
    )
    tag = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Tag",
        help_text="IFC Tag attribute — user-defined identifier",
    )

    # Type-level property sets as JSON
    properties = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Properties",
        help_text="Type-level property sets extracted from the IFC type object",
    )

    class Meta:
        ordering = ["ifc_type", "name"]
        verbose_name = "IFC Element Type"
        verbose_name_plural = "IFC Element Types"
        unique_together = ["ifc_file", "global_id"]
        indexes = [
            models.Index(fields=["ifc_file", "ifc_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.ifc_type}: {self.name or self.global_id[:8]}"


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

    # Link to defining type object (IfcTypeProduct)
    element_type = models.ForeignKey(
        IFCElementType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences",
        verbose_name="Element Type",
        help_text="Defining type object (e.g. IfcDoorType) via IfcRelDefinesByType",
    )

    # Spatial containment — points to the IFCSpatialElement this entity sits inside.
    # For a wall on "Ground Floor", this FK points to the IfcBuildingStorey node.
    # Full hierarchy (building, site) is reachable via spatial_container.parent chain.
    spatial_container = models.ForeignKey(
        "IFCSpatialElement",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contained_entities",
        verbose_name="Spatial Container",
        help_text="Direct spatial container (IfcRelContainedInSpatialStructure)",
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
            models.Index(fields=["ifc_file", "spatial_container"]),
            models.Index(fields=["ifc_type", "name"]),
            models.Index(fields=["ifc_file", "ifc_type", "element_type"]),
        ]

    def __str__(self):
        return f"{self.ifc_type}: {self.name or self.global_id[:8]}"


class IFCSpatialElement(UUIDModel):
    """
    Lightweight tree overlay for the IFC spatial decomposition hierarchy.

    Maps to IfcSpatialStructureElement subtypes (IfcSite, IfcBuilding,
    IfcBuildingStorey, IfcSpace, and IFC4.3 IfcFacility/IfcFacilityPart).
    Linked via self-referencing parent FK to form the spatial tree
    (mirrors IfcRelAggregates in the IFC schema).

    All entity data (properties, embeddings, name, global_id) lives on the
    associated IFCEntity record via the OneToOne FK — this model holds ONLY
    the tree structure and spatial-specific attributes.
    """

    class SpatialType(models.TextChoices):
        SITE = "site", "Site"
        BUILDING = "building", "Building"
        BUILDING_STOREY = "building_storey", "Building Storey"
        SPACE = "space", "Space"
        # IFC4.3 infrastructure
        FACILITY = "facility", "Facility"
        FACILITY_PART = "facility_part", "Facility Part"

    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="spatial_elements",
        verbose_name="IFC File",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        verbose_name="Parent",
        help_text="Parent spatial element (IfcRelAggregates)",
    )
    spatial_type = models.CharField(
        max_length=20,
        choices=SpatialType.choices,
        db_index=True,
        verbose_name="Spatial Type",
    )

    # OneToOne back to IFCEntity — the entity holds ALL data
    entity = models.OneToOneField(
        IFCEntity,
        on_delete=models.CASCADE,
        related_name="spatial_node",
        verbose_name="Entity",
        help_text="The IFCEntity record holding this spatial element's data",
    )

    # Extra attributes specific to spatial elements (not on IFCEntity)
    long_name = models.CharField(
        max_length=2_000,
        blank=True,
        verbose_name="Long Name",
        help_text="IFC LongName attribute",
    )
    composition_type = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Composition Type",
        help_text="ELEMENT, COMPLEX, or PARTIAL",
    )
    elevation = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Elevation",
        help_text="Elevation value (IfcBuildingStorey only)",
    )

    class Meta:
        ordering = ["spatial_type", "entity__name"]
        verbose_name = "IFC Spatial Element"
        verbose_name_plural = "IFC Spatial Elements"
        constraints = [
            models.UniqueConstraint(
                fields=["ifc_file", "entity"],
                name="unique_spatial_element_per_entity",
            ),
        ]
        indexes = [
            models.Index(fields=["ifc_file", "spatial_type"]),
            models.Index(fields=["ifc_file", "parent"]),
        ]

    def __str__(self) -> str:
        name = self.entity.name if self.entity_id else self.pk
        return f"{self.get_spatial_type_display()}: {name}"


class IFCDataIssue(UUIDModel):
    """Stores information about invalid or duplicate data found in an IFC file.

    Dedup model mirrors writeback.Conflict: a stable content_hash identifies the
    same problem across reparses, so a user's DISMISSED state survives reparse.
    """

    class IssueType(models.TextChoices):
        DUPLICATE_GUID = "duplicate_guid", "Duplicate GlobalID"
        INVALID_GEOMETRY = "invalid_geometry", "Invalid Geometry"
        MISSING_PROPERTY = "missing_property", "Missing Required Property"
        ORPHANED_ELEMENT = "orphaned_element", "Orphaned Element (No Spatial Placement)"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DISMISSED = "dismissed", "Dismissed"

    # Default severity per issue_type. DUPLICATE_GUID / INVALID_GEOMETRY break
    # downstream queries; MISSING_PROPERTY is mostly cosmetic.
    SEVERITY_MAP = {
        IssueType.DUPLICATE_GUID: Severity.HIGH,
        IssueType.INVALID_GEOMETRY: Severity.HIGH,
        IssueType.ORPHANED_ELEMENT: Severity.MEDIUM,
        IssueType.MISSING_PROPERTY: Severity.LOW,
    }

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

    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    # Stable deduplication key: SHA-256(ifc_file_id : global_id : issue_type).
    # Survives reparse: dismissed rows are preserved, open rows refreshed in place.
    content_hash = models.CharField(max_length=64, db_index=True)

    # The parser sets both timestamps to the same `now` on create; subsequent
    # reparses only advance last_seen_at. When they are equal the issue was
    # created in the most recent parse — drives the "New" badge in the UI.
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "IFC Data Issue"
        verbose_name_plural = "IFC Data Issues"
        ordering = ["-severity", "ifc_file", "issue_type"]
        indexes = [
            models.Index(fields=["ifc_file", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["ifc_file", "content_hash"],
                name="unique_data_issue_content_hash",
            ),
        ]

    @classmethod
    def compute_hash(cls, ifc_file_id, global_id: str, issue_type: str) -> str:
        """Stable identity for upsert-by-hash on reparse."""
        return hashlib.sha256(f"{ifc_file_id}:{global_id}:{issue_type}".encode()).hexdigest()
