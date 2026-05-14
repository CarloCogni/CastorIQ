# islam/scheduling/models.py
"""4D scheduling models — Task and its M2M link to IFC entities."""

from __future__ import annotations

import logging

from django.db import models

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)


class Task(UUIDModel):
    """A single schedule task linked to one or more IFC entities."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETE = "complete", "Complete"
        DELAYED = "delayed", "Delayed"

    class Source(models.TextChoices):
        EXCEL = "excel", "Excel (.xlsx)"
        CSV = "csv", "CSV (.csv)"
        XER = "xer", "Primavera P6 (.xer)"
        MSP = "msp", "MS Project (.xml)"
        MANUAL = "manual", "Manual entry"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_tasks",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=500,
        db_index=True,
        verbose_name="Task Name",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )
    start_date = models.DateField(
        db_index=True,
        verbose_name="Start Date",
    )
    end_date = models.DateField(
        db_index=True,
        verbose_name="End Date",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
        db_index=True,
        verbose_name="Status",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name="Source",
    )
    activity_code = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="Activity Code",
        help_text="Used to match this task to IFC elements by parameter value",
    )
    color = models.CharField(
        max_length=20,
        default="#3b82f6",
        verbose_name="Bar Colour",
        help_text="Hex colour shown in the Gantt and 3D viewer",
    )
    ifc_entities = models.ManyToManyField(
        IFCEntity,
        blank=True,
        related_name="schedule_tasks",
        verbose_name="Linked IFC Entities",
        help_text="Read-only from the IFC perspective — set only by the TimeLiner",
    )

    class Meta:
        verbose_name = "Schedule Task"
        verbose_name_plural = "Schedule Tasks"
        ordering = ["start_date", "name"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "start_date", "end_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.start_date} – {self.end_date})"

    # ------------------------------------------------------------------
    # Computed helpers (used by Gantt template)
    # ------------------------------------------------------------------

    def entity_global_ids(self) -> list[str]:
        """Return GlobalIds of all linked IFC entities."""
        return list(self.ifc_entities.values_list("global_id", flat=True))

    @property
    def link_status(self) -> str:
        """'linked', 'partial', or 'unlinked' based on entity count."""
        count = self.ifc_entities.count()
        if count == 0:
            return "unlinked"
        # "partial" heuristic: fewer than 3 entities for a non-manual task
        if self.source != self.Source.MANUAL and count < 3:
            return "partial"
        return "linked"


class MappingProfile(UUIDModel):
    """Saved column mapping for a recurring schedule file format."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="mapping_profiles",
        verbose_name="Project",
    )
    name = models.CharField(max_length=255, verbose_name="Profile Name")
    column_mapping = models.JSONField(
        verbose_name="Column Mapping",
        help_text="Maps canonical fields (name, start_date, …) to actual column header strings",
    )
    ifc_parameter_name = models.CharField(
        max_length=255,
        default="ActivityCode",
        verbose_name="IFC Parameter Name",
        help_text="IFC property that holds the activity code for parameter-based linking",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mapping Profile"
        verbose_name_plural = "Mapping Profiles"
        ordering = ["-created_at"]
        unique_together = [("project", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.project.name})"


class LinkFeedback(UUIDModel):
    """User acceptance/rejection of an embedding-suggested task→entity link."""

    class Method(models.TextChoices):
        EMBEDDING = "embedding", "Embedding similarity"
        PARAMETER = "parameter", "Parameter match"
        MANUAL = "manual", "Manual selection"

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="link_feedback",
        verbose_name="Task",
    )
    ifc_entity = models.ForeignKey(
        IFCEntity,
        on_delete=models.CASCADE,
        related_name="link_feedback",
        verbose_name="Suggested IFC Entity",
    )
    accepted = models.BooleanField(
        null=True,
        default=None,
        verbose_name="Accepted",
        help_text="None=pending review, True=accepted, False=rejected",
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.EMBEDDING,
        verbose_name="Linking method",
    )
    confidence_at_time = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Confidence score at suggestion time",
    )
    corrected_to = models.ForeignKey(
        IFCEntity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrected_feedback",
        verbose_name="Corrected entity",
        help_text="Populated when the user selects a different entity than the suggestion",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Link Feedback"
        verbose_name_plural = "Link Feedback"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "accepted"]),
        ]

    def __str__(self) -> str:
        status = {None: "pending", True: "accepted", False: "rejected"}.get(self.accepted, "?")
        return f"{self.task.name} → {self.ifc_entity} ({status})"

    @property
    def effective_entity(self):
        """The entity the user chose — corrected_to if set, else ifc_entity."""
        return self.corrected_to or self.ifc_entity
