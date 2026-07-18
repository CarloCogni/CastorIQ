# scheduling/resource_foundation_models.py
"""Canonical Resource / ResourceAssignment domain (DF-E1).

Planner-agnostic foundation for 5D resource/cost data. ``P6ResourceAssignment``
remains the legacy P6-specific store until DF-E2/E3 population and EVM cutover.

DF-E1 does not change EVM, cashflow, ML, or DCMA behaviour.

Null-vs-zero policy: missing planned/actual/remaining/at-completion cost and
unit fields MUST be NULL (absent). Explicit zero means a recorded zero value.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import UUIDModel
from environments.models import Project


class Resource(UUIDModel):
    """Project-scoped resource dictionary entry (labor, material, etc.)."""

    class ResourceType(models.TextChoices):
        LABOR = "labor", "Labor"
        MATERIAL = "material", "Material"
        EQUIPMENT = "equipment", "Equipment"
        SUBCONTRACT = "subcontract", "Subcontract"
        COST = "cost", "Cost"
        OTHER = "other", "Other"
        UNKNOWN = "unknown", "Unknown"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="resources",
        verbose_name="Project",
    )
    resource_code = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name="Resource Code",
        help_text="Optional project-scoped code; unique per project when non-blank.",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    resource_type = models.CharField(
        max_length=20,
        choices=ResourceType.choices,
        default=ResourceType.UNKNOWN,
        db_index=True,
        verbose_name="Resource Type",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        verbose_name="Status",
    )
    source_system = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Source System",
        help_text="Importer hint: p6xml, xer, msp, manual, etc.",
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="External ID",
    )
    unit_of_measure = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Unit of Measure",
    )
    currency = models.CharField(
        max_length=16,
        blank=True,
        default="",
        verbose_name="Currency",
        help_text="ISO-like currency code when known; blank if unknown.",
    )
    default_rate = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Default Rate",
        help_text="Optional reference rate — not used by EVM in DF-E1.",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Resource"
        verbose_name_plural = "Resources"
        ordering = ["name", "resource_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "resource_code"],
                condition=Q(resource_code__gt=""),
                name="castor_scheduling_unique_resource_code_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "resource_type"]),
            models.Index(fields=["project", "external_id"]),
        ]

    def __str__(self) -> str:
        code = self.resource_code or "—"
        return f"{self.name} [{code}] ({self.project_id})"


class ResourceAssignment(UUIDModel):
    """Task-linked resource assignment — planner-agnostic cost/unit record.

    Cost and unit quantity fields use NULL for missing values. Do not default
    them to zero. ``P6ResourceAssignment`` is unchanged by DF-E1.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="resource_assignments",
        verbose_name="Project",
    )
    task = models.ForeignKey(
        "Task",
        on_delete=models.CASCADE,
        related_name="canonical_resource_assignments",
        verbose_name="Task",
    )
    resource = models.ForeignKey(
        Resource,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="Resource",
    )
    schedule_activity = models.ForeignKey(
        "ScheduleActivity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resource_assignments",
        verbose_name="Schedule Activity",
    )
    source_version = models.ForeignKey(
        "ScheduleSourceVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resource_assignments",
        verbose_name="Source Version",
    )
    schedule_source = models.ForeignKey(
        "ScheduleSource",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="canonical_resource_assignments",
        verbose_name="Legacy Schedule Source",
    )
    source_system = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Source System",
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="External Assignment ID",
        help_text="Stable assignment identity from the source planner when available.",
    )
    p6_resource_object_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="P6 Resource ObjectId",
    )
    p6_assignment_object_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="P6 Assignment ObjectId",
        help_text="Future-proof P6 ResourceAssignment ObjectId when available.",
    )
    planned_units = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Planned Units",
    )
    planned_cost = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Planned Cost",
    )
    actual_units = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Actual Units",
    )
    actual_cost = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Actual Cost",
    )
    remaining_units = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Remaining Units",
    )
    remaining_cost = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Remaining Cost",
    )
    at_completion_units = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="At Completion Units",
    )
    at_completion_cost = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="At Completion Cost",
    )
    unit_of_measure = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Unit of Measure",
    )
    currency = models.CharField(
        max_length=16,
        blank=True,
        default="",
        verbose_name="Currency",
    )
    is_pending = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Pending",
        help_text="True until import/population is confirmed (DF-E2).",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Resource Assignment"
        verbose_name_plural = "Resource Assignments"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "source_version", "source_system", "external_id"],
                condition=Q(external_id__gt="") & Q(source_version__isnull=False),
                name="castor_scheduling_unique_ra_source_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "task"]),
            models.Index(fields=["project", "resource"]),
            models.Index(fields=["project", "source_version"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "is_pending"]),
        ]

    def __str__(self) -> str:
        return f"RA {self.resource_id} → task {self.task_id} ({self.project_id})"

    def clean(self) -> None:
        """Enforce project consistency across task and resource FKs."""
        super().clean()
        errors: dict[str, str] = {}
        if self.task_id and self.project_id and self.task.project_id != self.project_id:
            errors["task"] = "Task project must match ResourceAssignment.project."
        if self.resource_id and self.project_id and self.resource.project_id != self.project_id:
            errors["resource"] = "Resource project must match ResourceAssignment.project."
        if (
            self.schedule_activity_id
            and self.project_id
            and self.schedule_activity.project_id != self.project_id
        ):
            errors["schedule_activity"] = (
                "ScheduleActivity project must match ResourceAssignment.project."
            )
        if (
            self.source_version_id
            and self.project_id
            and self.source_version.project_id != self.project_id
        ):
            errors["source_version"] = (
                "SourceVersion project must match ResourceAssignment.project."
            )
        if errors:
            raise ValidationError(errors)
