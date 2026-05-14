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
