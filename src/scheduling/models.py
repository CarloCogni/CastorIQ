# castor/scheduling/models.py
"""4D scheduling models — Task and its M2M link to IFC entities."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import models
from pgvector.django import VectorField

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

    class Stage(models.TextChoices):
        SUBSTRUCTURE = "substructure", "Substructure"
        STRUCTURE = "structure", "Structure"
        ENVELOPE = "envelope", "Envelope"
        MEP = "mep", "MEP"
        FINISHES = "finishes", "Finishes"
        EXTERNAL = "external", "External Works"

    class SubStage(models.TextChoices):
        # Substructure
        EXCAVATION = "excavation", "Excavation"
        BLINDING = "blinding", "Blinding"
        WATERPROOFING = "waterproofing", "Waterproofing"
        BACKFILL = "backfill", "Backfill"
        PILING = "piling", "Piling"
        # Structure
        FORMWORK = "formwork", "Formwork"
        REBAR = "rebar", "Rebar"
        CONCRETE = "concrete", "Concrete"
        STRIPPING = "stripping", "Stripping"
        STEEL_ERECTION = "steel_erection", "Steel Erection"
        PRECAST = "precast", "Precast"
        # Envelope
        BLOCKWORK = "blockwork", "Blockwork"
        CLADDING = "cladding", "Cladding"
        GLAZING = "glazing", "Glazing"
        ROOFING = "roofing", "Roofing"
        INSULATION = "insulation", "Insulation"
        # MEP
        ELECTRICAL = "electrical", "Electrical"
        PLUMBING = "plumbing", "Plumbing"
        HVAC = "hvac", "HVAC"
        FIREFIGHTING = "firefighting", "Firefighting"
        LV_SYSTEMS = "lv_systems", "LV Systems"
        # Finishes
        PLASTER = "plaster", "Plaster"
        PAINTING = "painting", "Painting"
        FLOORING = "flooring", "Flooring"
        TILING = "tiling", "Tiling"
        CEILING = "ceiling", "Ceiling"
        JOINERY = "joinery", "Joinery"
        # External
        LANDSCAPING = "landscaping", "Landscaping"
        PAVING = "paving", "Paving"
        FENCING = "fencing", "Fencing"
        HARDSCAPE = "hardscape", "Hardscape"

    class Source(models.TextChoices):
        EXCEL = "excel", "Excel (.xlsx)"
        CSV = "csv", "CSV (.csv)"
        XER = "xer", "Primavera P6 (.xer)"
        MSP = "msp", "MS Project (.xml)"
        P6XML = "p6xml", "Primavera P6 XML (.xml)"
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
    actual_start = models.DateField(
        null=True,
        blank=True,
        verbose_name="Actual Start",
    )
    actual_end = models.DateField(
        null=True,
        blank=True,
        verbose_name="Actual End",
    )
    is_non_physical = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Non-Physical",
        help_text="Excluded from IFC linking — pure schedule activity with no physical element.",
    )
    non_physical_locked = models.BooleanField(
        default=False,
        verbose_name="Non-Physical Locked",
        help_text="True when is_non_physical was set manually by the user; prevents Layer 0 from overriding it.",
    )
    activity_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Activity Type",
        help_text="Raw activity type from the schedule file (e.g. WBS Summary, Milestone, Task Dependent).",
    )
    cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Cost",
        help_text="Task cost from schedule. Overrides IFC element cost when set.",
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
    stage = models.CharField(
        max_length=20,
        blank=True,
        choices=Stage.choices,
        db_index=True,
        verbose_name="Stage",
        help_text="Construction stage auto-detected from task name keywords. Blank = unassigned.",
    )
    sub_stage = models.CharField(
        max_length=30,
        blank=True,
        choices=SubStage.choices,
        db_index=True,
        verbose_name="Sub-Stage",
        help_text="Trade-level detail within the parent stage. Auto-detected; also sets parent stage.",
    )
    weight = models.FloatField(
        default=1.0,
        verbose_name="Custom Weight",
        help_text="Used by the Custom Weight progress mode. Defaults to 1.0 for equal weighting.",
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
    schedule_source = models.ForeignKey(
        "ScheduleSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
        verbose_name="Schedule Source",
        help_text="Import event that last created or updated this task.",
    )
    schedule_activity = models.ForeignKey(
        "ScheduleActivity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
        verbose_name="Schedule Activity",
        help_text="Logical activity identity across source versions (nullable until linked).",
    )
    source_version = models.ForeignKey(
        "ScheduleSourceVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
        verbose_name="Source Version",
        help_text="Accepted schedule source version that last touched this task (nullable).",
    )
    wbs_node = models.ForeignKey(
        "WBSNode",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        verbose_name="WBS Node",
        help_text="Canonical WBS assignment (nullable until DF-C2 population or manual assign).",
    )

    # ------------------------------------------------------------------
    # CPM fields — populated by compute_critical_path()
    # ------------------------------------------------------------------
    early_start = models.DateField(null=True, blank=True, verbose_name="Early Start")
    early_finish = models.DateField(null=True, blank=True, verbose_name="Early Finish")
    late_start = models.DateField(null=True, blank=True, verbose_name="Late Start")
    late_finish = models.DateField(null=True, blank=True, verbose_name="Late Finish")
    total_float = models.IntegerField(null=True, blank=True, verbose_name="Total Float (days)")
    is_critical = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Critical",
        help_text="True when total float == 0 (on the critical path).",
    )

    # ------------------------------------------------------------------
    # P6 scheduling metadata — populated from P6 XML import
    # ------------------------------------------------------------------
    calendar_object_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="P6 Calendar ObjectId",
        help_text="P6 calendar assigned to this activity; used for working-day CPM.",
    )
    constraint_type = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Constraint Type",
        help_text="P6 scheduling constraint: 'Start On or After', 'Mandatory Finish', etc.",
    )
    constraint_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Constraint Date",
    )

    # ------------------------------------------------------------------
    # P6 progress tracking — populated from P6 XML import; used for EV
    # ------------------------------------------------------------------
    physical_percent_complete = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Physical % Complete",
        help_text="Planner-entered physical progress (0–1). From P6 PhysicalPercentComplete.",
    )
    duration_percent_complete = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Duration % Complete",
        help_text="Duration-based progress (0–1). Computed by P6 from actual/remaining duration.",
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
        """'non_physical' | 'linked' | 'unlinked'."""
        if self.is_non_physical:
            return "non_physical"
        # Use all() so prefetch_related cache is honoured — .count() bypasses it.
        count = len(self.ifc_entities.all())
        if count == 0:
            return "unlinked"
        return "linked"


class TaskDependency(UUIDModel):
    """Finish-to-Start (and other) dependency between two schedule tasks."""

    class DepType(models.TextChoices):
        FS = "FS", "Finish-to-Start"
        SS = "SS", "Start-to-Start"
        FF = "FF", "Finish-to-Finish"
        SF = "SF", "Start-to-Finish"

    predecessor = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="successors",
        verbose_name="Predecessor",
    )
    successor = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="predecessors",
        verbose_name="Successor",
    )
    dep_type = models.CharField(
        max_length=2,
        choices=DepType.choices,
        default=DepType.FS,
        verbose_name="Dependency Type",
    )
    lag_days = models.IntegerField(
        default=0,
        verbose_name="Lag (days)",
        help_text="Positive = lag, negative = lead.",
    )

    class Meta:
        verbose_name = "Task Dependency"
        verbose_name_plural = "Task Dependencies"
        ordering = ["predecessor__start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor", "dep_type"],
                name="unique_task_dependency",
            )
        ]

    def __str__(self) -> str:
        return f"{self.predecessor.name} →[{self.dep_type}+{self.lag_days}d]→ {self.successor.name}"


class ProgressMode(UUIDModel):
    """Per-project schedule progress calculation mode for the Insights dashboard ring."""

    class Mode(models.TextChoices):
        BY_COUNT = "count", "By Task Count"
        BY_COST = "cost", "By Cost"
        BY_DURATION = "duration", "By Duration"
        BY_WEIGHT = "weight", "Custom Weight"

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="progress_mode",
        verbose_name="Project",
    )
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.BY_COUNT,
        verbose_name="Progress Mode",
    )

    class Meta:
        verbose_name = "Progress Mode"
        verbose_name_plural = "Progress Modes"

    def __str__(self) -> str:
        return f"{self.project.name} — {self.get_mode_display()}"


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


class ScheduleActivity(UUIDModel):
    """Stable logical schedule activity identity within one project."""

    class Origin(models.TextChoices):
        IMPORTED = "imported", "Imported"
        MANUAL = "manual", "Manual"
        SYSTEM = "system", "System"

    class IdentityStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        UNRESOLVED = "unresolved", "Unresolved"
        SUPERSEDED = "superseded", "Superseded"
        RETIRED = "retired", "Retired"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_activities",
        verbose_name="Project",
    )
    canonical_activity_key = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Canonical Activity Key",
        help_text="Project-scoped stable key — never derived from name or dates alone.",
    )
    external_activity_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="External Activity ID",
    )
    activity_code = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Activity Code",
    )
    display_name = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Display Name",
    )
    source_identity_hint = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Source Identity Hint",
        help_text="Importer hint: p6xml, xer, msp, column, manual, etc.",
    )
    origin = models.CharField(
        max_length=16,
        choices=Origin.choices,
        default=Origin.IMPORTED,
        db_index=True,
        verbose_name="Origin",
    )
    identity_status = models.CharField(
        max_length=16,
        choices=IdentityStatus.choices,
        default=IdentityStatus.ACTIVE,
        db_index=True,
        verbose_name="Identity Status",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Schedule Activity"
        verbose_name_plural = "Schedule Activities"
        ordering = ["canonical_activity_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "canonical_activity_key"],
                name="castor_scheduling_unique_activity_key_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "identity_status"]),
            models.Index(fields=["project", "external_activity_id"]),
            models.Index(fields=["project", "activity_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.canonical_activity_key} ({self.project_id})"


class ScheduleSourceVersion(UUIDModel):
    """One accepted schedule source artifact/version for a project."""

    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        CURRENT = "current", "Current"
        SUPERSEDED = "superseded", "Superseded"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_source_versions",
        verbose_name="Project",
    )
    schedule_source = models.ForeignKey(
        "ScheduleSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_versions",
        verbose_name="Legacy Schedule Source",
        help_text="Optional link to legacy import audit row during transition.",
    )
    version_number = models.PositiveIntegerField(
        default=1,
        verbose_name="Version Number",
    )
    source_type = models.CharField(
        max_length=20,
        choices=Task.Source.choices,
        default=Task.Source.MANUAL,
        verbose_name="Source Type",
    )
    source_filename = models.CharField(max_length=500, verbose_name="Source Filename")
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Content Hash",
    )
    external_project_id = models.CharField(
        max_length=255, blank=True, verbose_name="External Project ID"
    )
    external_schedule_id = models.CharField(
        max_length=255, blank=True, verbose_name="External Schedule ID"
    )
    external_revision = models.CharField(
        max_length=255, blank=True, verbose_name="External Revision"
    )
    data_date = models.DateField(null=True, blank=True, verbose_name="Data Date")
    imported_at = models.DateTimeField(verbose_name="Imported At")
    importer_version = models.CharField(max_length=64, blank=True, verbose_name="Importer Version")
    parser_version = models.CharField(max_length=64, blank=True, verbose_name="Parser Version")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CANDIDATE,
        db_index=True,
        verbose_name="Status",
    )
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by_versions",
        verbose_name="Supersedes",
    )
    source_metadata = models.JSONField(default=dict, blank=True, verbose_name="Source Metadata")
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedule_source_versions_created",
        verbose_name="Created By",
    )

    class Meta:
        verbose_name = "Schedule Source Version"
        verbose_name_plural = "Schedule Source Versions"
        ordering = ["-imported_at", "-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status="current"),
                name="castor_scheduling_unique_current_ssv_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "imported_at"]),
            models.Index(fields=["project", "version_number"]),
        ]

    def __str__(self) -> str:
        return f"v{self.version_number} {self.source_filename} ({self.status})"


class ScheduleImportRun(UUIDModel):
    """Audit record for one schedule import attempt."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        APPEND = "append", "Append"
        UPDATE = "update", "Update"
        REPLACE = "replace", "Replace"
        PREVIEW = "preview", "Preview"
        UNKNOWN = "unknown", "Unknown"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_import_runs",
        verbose_name="Project",
    )
    schedule_source = models.ForeignKey(
        "ScheduleSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_runs",
        verbose_name="Legacy Schedule Source",
    )
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_runs",
        verbose_name="Accepted Source Version",
    )
    source_type = models.CharField(
        max_length=20,
        choices=Task.Source.choices,
        default=Task.Source.MANUAL,
        verbose_name="Source Type",
    )
    source_filename = models.CharField(max_length=500, verbose_name="Source Filename")
    content_hash = models.CharField(max_length=64, blank=True, verbose_name="Content Hash")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.UNKNOWN,
        verbose_name="Import Mode",
    )
    started_at = models.DateTimeField(verbose_name="Started At")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Completed At")
    importer_version = models.CharField(max_length=64, blank=True, verbose_name="Importer Version")
    parser_version = models.CharField(max_length=64, blank=True, verbose_name="Parser Version")
    task_count = models.IntegerField(default=0, verbose_name="Task Count")
    dependency_count = models.IntegerField(default=0, verbose_name="Dependency Count")
    skipped_count = models.IntegerField(default=0, verbose_name="Skipped Count")
    warning_count = models.IntegerField(default=0, verbose_name="Warning Count")
    error_count = models.IntegerField(default=0, verbose_name="Error Count")
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )
    error_summary = models.TextField(blank=True, verbose_name="Error Summary")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedule_import_runs_requested",
        verbose_name="Requested By",
    )

    class Meta:
        verbose_name = "Schedule Import Run"
        verbose_name_plural = "Schedule Import Runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_filename} ({self.status})"


class BaselineVersion(UUIDModel):
    """Named baseline definition — separate from operational schedule and source version."""

    class BaselineType(models.TextChoices):
        IMPORTED_REFERENCE = "imported_reference", "Imported reference"
        WORKING = "working", "Working"
        APPROVED = "approved", "Approved"
        COMPARISON_ONLY = "comparison_only", "Comparison only"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="baseline_versions",
        verbose_name="Project",
    )
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="baseline_versions",
        verbose_name="Source Version",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    code = models.CharField(max_length=64, blank=True, verbose_name="Code / Reference")
    baseline_type = models.CharField(
        max_length=32,
        choices=BaselineType.choices,
        db_index=True,
        verbose_name="Baseline Type",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    data_date = models.DateField(null=True, blank=True, verbose_name="Data Date")
    effective_date = models.DateField(null=True, blank=True, verbose_name="Effective Date")
    parent_baseline = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_revisions",
        verbose_name="Parent Baseline",
    )
    revision_number = models.PositiveIntegerField(default=1, verbose_name="Revision")
    currency = models.CharField(max_length=8, blank=True, verbose_name="Currency")
    methodology_version = models.CharField(
        max_length=64, blank=True, verbose_name="Methodology Version"
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    is_selected_for_analysis = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Selected for Analysis",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="baseline_versions_created",
        verbose_name="Created By",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="baseline_versions_published",
        verbose_name="Published By",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="baseline_versions_approved",
        verbose_name="Approved By",
    )
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Published At")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Approved At")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="Superseded At")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )

    class Meta:
        verbose_name = "Baseline Version"
        verbose_name_plural = "Baseline Versions"
        ordering = ["-created_at", "-revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "revision_number"],
                name="castor_scheduling_unique_baseline_revision_per_project",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_selected_for_analysis=True),
                name="castor_scheduling_unique_selected_baseline_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "baseline_type"]),
            models.Index(fields=["project", "is_selected_for_analysis"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} rev{self.revision_number} ({self.baseline_type}/{self.status})"

    @property
    def is_immutable(self) -> bool:
        """Published baselines cannot mutate authoritative fields."""
        return self.status == self.Status.PUBLISHED

    def save(self, *args, **kwargs) -> None:
        """Block mutation of authoritative fields on published baselines."""
        if not self._state.adding and self.is_immutable:
            if self.pk:
                prior = BaselineVersion.objects.filter(pk=self.pk).first()
                if prior and prior.is_immutable:
                    for field in (
                        "source_version_id",
                        "baseline_type",
                        "data_date",
                        "effective_date",
                        "currency",
                        "methodology_version",
                        "revision_number",
                        "parent_baseline_id",
                        "name",
                        "code",
                    ):
                        if getattr(self, field) != getattr(prior, field):
                            raise ValueError(f"Cannot modify {field} on published baseline.")
        super().save(*args, **kwargs)


class BaselineTaskState(UUIDModel):
    """Immutable task-level snapshot belonging to one BaselineVersion."""

    baseline_version = models.ForeignKey(
        BaselineVersion,
        on_delete=models.CASCADE,
        related_name="task_states",
        verbose_name="Baseline Version",
    )
    schedule_activity = models.ForeignKey(
        ScheduleActivity,
        on_delete=models.PROTECT,
        related_name="baseline_task_states",
        verbose_name="Schedule Activity",
    )
    source_task_id = models.CharField(
        max_length=36, blank=True, verbose_name="Source Task ID Reference"
    )
    activity_code = models.CharField(max_length=255, blank=True, verbose_name="Activity Code")
    name_snapshot = models.CharField(max_length=500, verbose_name="Name Snapshot")
    planned_start = models.DateField(null=True, blank=True, verbose_name="Planned Start")
    planned_finish = models.DateField(null=True, blank=True, verbose_name="Planned Finish")
    duration_days = models.IntegerField(null=True, blank=True, verbose_name="Duration (days)")
    calendar_reference = models.CharField(
        max_length=128, blank=True, verbose_name="Calendar Reference"
    )
    baseline_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Baseline Cost / BAC",
    )
    planned_resource_units = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Planned Resource Units",
    )
    progress_basis = models.CharField(max_length=64, blank=True, verbose_name="Progress Basis")
    activity_type = models.CharField(max_length=100, blank=True, verbose_name="Activity Type")
    source_metadata = models.JSONField(default=dict, blank=True, verbose_name="Source Metadata")
    field_provenance = models.JSONField(default=dict, blank=True, verbose_name="Field Provenance")

    class Meta:
        verbose_name = "Baseline Task State"
        verbose_name_plural = "Baseline Task States"
        constraints = [
            models.UniqueConstraint(
                fields=["baseline_version", "schedule_activity"],
                name="castor_scheduling_unique_baseline_task_per_activity",
            ),
        ]
        indexes = [
            models.Index(fields=["baseline_version", "schedule_activity"]),
            models.Index(fields=["baseline_version", "activity_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.name_snapshot} @ {self.baseline_version_id}"

    def save(self, *args, **kwargs) -> None:
        """Block mutation when parent baseline is published."""
        if not self._state.adding and self.baseline_version.is_immutable:
            raise ValueError("BaselineTaskState is immutable after baseline publication.")
        super().save(*args, **kwargs)


class BaselineAuditEvent(UUIDModel):
    """Append-only baseline lifecycle audit event."""

    class EventType(models.TextChoices):
        BASELINE_CREATED = "baseline_created", "Baseline created"
        BASELINE_POPULATION_COMPLETED = "baseline_population_completed", "Population completed"
        BASELINE_PUBLISHED = "baseline_published", "Baseline published"
        BASELINE_APPROVED = "baseline_approved", "Baseline approved"
        BASELINE_SELECTED = "baseline_selected", "Baseline selected"
        BASELINE_SUPERSEDED = "baseline_superseded", "Baseline superseded"
        BASELINE_ARCHIVED = "baseline_archived", "Baseline archived"
        BASELINE_REJECTED = "baseline_rejected", "Baseline rejected"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="baseline_audit_events",
        verbose_name="Project",
    )
    baseline_version = models.ForeignKey(
        BaselineVersion,
        on_delete=models.CASCADE,
        related_name="audit_events",
        verbose_name="Baseline Version",
    )
    event_type = models.CharField(max_length=48, choices=EventType.choices, db_index=True)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="baseline_audit_events",
        verbose_name="Actor",
    )
    reason = models.TextField(blank=True, verbose_name="Reason / Reference")
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="baseline_audit_events",
        verbose_name="Source Version",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Baseline Audit Event"
        verbose_name_plural = "Baseline Audit Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "event_type"]),
            models.Index(fields=["baseline_version", "created_at"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ValueError("BaselineAuditEvent records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        raise ValueError("BaselineAuditEvent records are append-only.")


class WBSVersion(UUIDModel):
    """Planner-neutral canonical WBS hierarchy revision for a project."""

    class Origin(models.TextChoices):
        P6_XML = "p6_xml", "Primavera P6 XML"
        P6_XER = "p6_xer", "Primavera P6 XER"
        MSP_XML = "msp_xml", "MS Project XML"
        COLUMN_MAPPING = "column_mapping", "Column mapping"
        MANUAL = "manual", "Manual"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="wbs_versions",
        verbose_name="Project",
    )
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wbs_versions",
        verbose_name="Source Version",
        help_text="Nullable for manual WBS hierarchies.",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    code = models.CharField(max_length=64, blank=True, verbose_name="Code / Reference")
    origin = models.CharField(
        max_length=20,
        choices=Origin.choices,
        default=Origin.MANUAL,
        db_index=True,
        verbose_name="Origin",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    revision_number = models.PositiveIntegerField(default=1, verbose_name="Revision")
    data_date = models.DateField(null=True, blank=True, verbose_name="Data Date")
    parent_version = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_revisions",
        verbose_name="Parent Version",
    )
    is_selected_for_analysis = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Selected for Analysis",
    )
    source_metadata = models.JSONField(default=dict, blank=True, verbose_name="Source Metadata")
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wbs_versions_created",
        verbose_name="Created By",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wbs_versions_activated",
        verbose_name="Activated By",
    )
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Activated At")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="Superseded At")

    class Meta:
        verbose_name = "WBS Version"
        verbose_name_plural = "WBS Versions"
        ordering = ["-created_at", "-revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "revision_number"],
                name="castor_scheduling_unique_wbs_revision_per_project",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_selected_for_analysis=True),
                name="castor_scheduling_unique_selected_wbs_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "revision_number"]),
            models.Index(fields=["project", "is_selected_for_analysis"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} r{self.revision_number} ({self.status})"

    @property
    def is_hierarchy_immutable(self) -> bool:
        """Active hierarchy identity must not be silently mutated."""
        return self.status in {self.Status.ACTIVE, self.Status.SUPERSEDED}


class WBSNode(UUIDModel):
    """One node in a canonical WBS hierarchy revision."""

    class NodeType(models.TextChoices):
        ROOT = "root", "Root"
        SUMMARY = "summary", "Summary"
        WORK_PACKAGE = "work_package", "Work package"
        LEAF = "leaf", "Leaf"
        UNKNOWN = "unknown", "Unknown"

    class IdentityStatus(models.TextChoices):
        RESOLVED = "resolved", "Resolved"
        UNRESOLVED = "unresolved", "Unresolved"
        GENERATED = "generated", "Generated"
        RETIRED = "retired", "Retired"

    class Authority(models.TextChoices):
        SOURCE = "source", "Source"
        MANUAL = "manual", "Manual"
        GOVERNED = "governed", "Governed"
        INFERRED = "inferred", "Inferred"

    wbs_version = models.ForeignKey(
        WBSVersion,
        on_delete=models.CASCADE,
        related_name="nodes",
        verbose_name="WBS Version",
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="External ID",
        help_text="Planner-native object identity when known.",
    )
    external_parent_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="External Parent ID",
    )
    code = models.CharField(max_length=100, blank=True, verbose_name="WBS Code")
    name = models.CharField(max_length=500, verbose_name="WBS Name")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name="Parent Node",
    )
    path = models.CharField(
        max_length=2048,
        blank=True,
        db_index=True,
        verbose_name="Materialized Path",
        help_text="Stable ancestor chain using node UUIDs — not display names.",
    )
    depth = models.PositiveSmallIntegerField(default=0, db_index=True, verbose_name="Depth")
    sequence = models.PositiveIntegerField(default=0, verbose_name="Sequence")
    node_type = models.CharField(
        max_length=20,
        choices=NodeType.choices,
        default=NodeType.UNKNOWN,
        db_index=True,
        verbose_name="Node Type",
    )
    identity_status = models.CharField(
        max_length=16,
        choices=IdentityStatus.choices,
        default=IdentityStatus.RESOLVED,
        verbose_name="Identity Status",
    )
    authority = models.CharField(
        max_length=16,
        choices=Authority.choices,
        default=Authority.MANUAL,
        verbose_name="Authority",
    )
    source_metadata = models.JSONField(default=dict, blank=True, verbose_name="Source Metadata")

    class Meta:
        verbose_name = "WBS Node"
        verbose_name_plural = "WBS Nodes"
        ordering = ["sequence", "code", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["wbs_version", "external_id"],
                condition=~models.Q(external_id=""),
                name="castor_scheduling_unique_wbs_external_id_per_version",
            ),
        ]
        indexes = [
            models.Index(fields=["wbs_version", "parent"]),
            models.Index(fields=["wbs_version", "path"]),
            models.Index(fields=["wbs_version", "code"]),
            models.Index(fields=["wbs_version", "depth"]),
        ]

    def __str__(self) -> str:
        label = self.code or self.name
        return f"{label} (d{self.depth})"

    @property
    def project_id(self):
        return self.wbs_version.project_id


class AnalyticalSnapshot(UUIDModel):
    """Canonical analytical checkpoint manifest — identity and provenance, not KPI series."""

    class SnapshotType(models.TextChoices):
        IMPORT_SNAPSHOT = "import_snapshot", "Import snapshot"
        MANUAL_CHECKPOINT = "manual_checkpoint", "Manual checkpoint"
        SCHEDULED_CHECKPOINT = "scheduled_checkpoint", "Scheduled checkpoint"
        REPORT_FREEZE = "report_freeze", "Report freeze"
        PRE_IMPORT_SAFETY = "pre_import_safety", "Pre-import safety"
        RELEASE_CHECKPOINT = "release_checkpoint", "Release checkpoint"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CALCULATING = "calculating", "Calculating"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"
        CANCELLED = "cancelled", "Cancelled"

    class RepeatabilityStatus(models.TextChoices):
        FULLY_REPEATABLE = "fully_repeatable", "Fully repeatable"
        SOURCE_REPEATABLE = "source_repeatable", "Source repeatable"
        PARTIALLY_REPEATABLE = "partially_repeatable", "Partially repeatable"
        MANIFEST_ONLY = "manifest_only", "Manifest only"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analytical_snapshots",
        verbose_name="Project",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    snapshot_type = models.CharField(
        max_length=32,
        choices=SnapshotType.choices,
        db_index=True,
        verbose_name="Snapshot Type",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
        verbose_name="Status",
    )
    sequence_number = models.PositiveIntegerField(default=1, verbose_name="Sequence")
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="analytical_snapshots",
        verbose_name="Source Version",
    )
    baseline_version = models.ForeignKey(
        BaselineVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="analytical_snapshots",
        verbose_name="Baseline Version",
    )
    data_date = models.DateField(null=True, blank=True, verbose_name="Data Date")
    as_of_date = models.DateField(verbose_name="As-Of Date")
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name="Requested At")
    calculation_started_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Calculation Started At"
    )
    calculation_completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Calculation Completed At"
    )
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Published At")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="Superseded At")
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name="Archived At")
    methodology_version = models.CharField(max_length=64, verbose_name="Methodology Version")
    capability_profile_version = models.CharField(
        max_length=64, verbose_name="Capability Profile Version"
    )
    trust_policy_version = models.CharField(max_length=64, verbose_name="Trust Policy Version")
    calculation_engine_version = models.CharField(
        max_length=64, blank=True, verbose_name="Calculation Engine Version"
    )
    source_content_hash = models.CharField(
        max_length=128, blank=True, verbose_name="Source Content Hash"
    )
    input_fingerprint = models.CharField(
        max_length=64, db_index=True, verbose_name="Input Fingerprint"
    )
    scope_fingerprint = models.CharField(
        max_length=64, db_index=True, verbose_name="Scope Fingerprint"
    )
    repeatability_status = models.CharField(
        max_length=32,
        choices=RepeatabilityStatus.choices,
        default=RepeatabilityStatus.MANIFEST_ONLY,
        verbose_name="Repeatability Status",
    )
    filter_context = models.JSONField(default=dict, blank=True, verbose_name="Filter Context")
    input_manifest = models.JSONField(default=dict, blank=True, verbose_name="Input Manifest")
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )
    coverage_summary = models.JSONField(default=dict, blank=True, verbose_name="Coverage Summary")
    caveats = models.JSONField(default=list, blank=True, verbose_name="Caveats")
    failure_summary = models.TextField(blank=True, verbose_name="Failure Summary")
    artifact_manifest = models.JSONField(default=dict, blank=True, verbose_name="Artifact Manifest")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_snapshots_requested",
        verbose_name="Requested By",
    )
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_snapshots_calculated",
        verbose_name="Calculated By",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_snapshots_published",
        verbose_name="Published By",
    )
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_snapshots_archived",
        verbose_name="Archived By",
    )
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by",
        verbose_name="Supersedes",
    )

    class Meta:
        verbose_name = "Analytical Snapshot"
        verbose_name_plural = "Analytical Snapshots"
        ordering = ["-requested_at", "-sequence_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "sequence_number"],
                name="castor_scheduling_unique_snapshot_sequence_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "snapshot_type"]),
            models.Index(fields=["project", "data_date"]),
            models.Index(fields=["project", "input_fingerprint"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.snapshot_type}/{self.status})"

    @property
    def is_provenance_immutable(self) -> bool:
        """Completed or published snapshots cannot mutate provenance fields."""
        return self.status in (
            self.Status.COMPLETED,
            self.Status.PUBLISHED,
            self.Status.SUPERSEDED,
            self.Status.ARCHIVED,
        )

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding and self.is_provenance_immutable and self.pk:
            prior = AnalyticalSnapshot.objects.filter(pk=self.pk).first()
            if prior and prior.is_provenance_immutable:
                immutable = (
                    "source_version_id",
                    "baseline_version_id",
                    "data_date",
                    "as_of_date",
                    "methodology_version",
                    "capability_profile_version",
                    "trust_policy_version",
                    "input_fingerprint",
                    "scope_fingerprint",
                    "input_manifest",
                    "source_content_hash",
                )
                for field in immutable:
                    if getattr(self, field) != getattr(prior, field):
                        raise ValueError(f"Cannot modify {field} on completed/published snapshot.")
        super().save(*args, **kwargs)


class AnalyticalSnapshotAuditEvent(UUIDModel):
    """Append-only analytical snapshot lifecycle audit event."""

    class EventType(models.TextChoices):
        SNAPSHOT_REQUESTED = "snapshot_requested", "Snapshot requested"
        SNAPSHOT_CALCULATION_STARTED = "snapshot_calculation_started", "Calculation started"
        SNAPSHOT_COMPLETED = "snapshot_completed", "Snapshot completed"
        SNAPSHOT_FAILED = "snapshot_failed", "Snapshot failed"
        SNAPSHOT_PUBLISHED = "snapshot_published", "Snapshot published"
        SNAPSHOT_SUPERSEDED = "snapshot_superseded", "Snapshot superseded"
        SNAPSHOT_ARCHIVED = "snapshot_archived", "Snapshot archived"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analytical_snapshot_audit_events",
        verbose_name="Project",
    )
    snapshot = models.ForeignKey(
        AnalyticalSnapshot,
        on_delete=models.CASCADE,
        related_name="audit_events",
        verbose_name="Snapshot",
    )
    event_type = models.CharField(max_length=48, choices=EventType.choices, db_index=True)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_snapshot_audit_events",
        verbose_name="Actor",
    )
    reason = models.TextField(blank=True, verbose_name="Reason")
    source_version = models.ForeignKey(
        ScheduleSourceVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="snapshot_audit_events",
        verbose_name="Source Version",
    )
    baseline_version = models.ForeignKey(
        BaselineVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="snapshot_audit_events",
        verbose_name="Baseline Version",
    )
    methodology_version = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Analytical Snapshot Audit Event"
        verbose_name_plural = "Analytical Snapshot Audit Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "event_type"]),
            models.Index(fields=["snapshot", "created_at"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ValueError("AnalyticalSnapshotAuditEvent records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        raise ValueError("AnalyticalSnapshotAuditEvent records are append-only.")


class AnalyticalSnapshotResult(UUIDModel):
    """Immutable persisted analytical summary for one completed snapshot (DF-B2)."""

    SCHEMA_VERSION = "snapshot-result-v1"

    snapshot = models.OneToOneField(
        AnalyticalSnapshot,
        on_delete=models.CASCADE,
        related_name="result",
        verbose_name="Snapshot",
    )
    schema_version = models.CharField(max_length=32, default=SCHEMA_VERSION)
    methodology_mode = models.CharField(max_length=64, blank=True)
    currency = models.CharField(max_length=16, blank=True)
    historical_authority = models.BooleanField(default=False)
    series_authority = models.CharField(max_length=32, blank=True)
    baseline_authority = models.CharField(max_length=32, blank=True)
    source_authority = models.CharField(max_length=32, blank=True)
    model_scope_authority = models.CharField(max_length=32, blank=True)
    pv = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    ev = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    ac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    bac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    spi = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    cpi = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    eac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    etc = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    vac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    tcpi = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    schedule_summary = models.JSONField(default=dict, blank=True)
    delay_summary = models.JSONField(default=dict, blank=True)
    model_impact_summary = models.JSONField(default=dict, blank=True)
    coverage_summary = models.JSONField(default=dict, blank=True)
    exclusion_summary = models.JSONField(default=dict, blank=True)
    caveats = models.JSONField(default=list, blank=True)
    kpi_payload = models.JSONField(default=dict, blank=True)
    calculation_started_at = models.DateTimeField(null=True, blank=True)
    calculation_completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    engine_metadata = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        verbose_name = "Analytical Snapshot Result"
        verbose_name_plural = "Analytical Snapshot Results"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Result for {self.snapshot_id}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding and self.pk:
            raise ValueError("AnalyticalSnapshotResult is immutable after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        raise ValueError("AnalyticalSnapshotResult deletion is restricted.")


class AnalyticalSnapshotSeriesPoint(UUIDModel):
    """Normalized persisted series point for a snapshot (DF-B2)."""

    class SeriesType(models.TextChoices):
        PLANNED_VALUE = "planned_value", "Planned value"
        EARNED_VALUE = "earned_value", "Earned value"
        ACTUAL_COST = "actual_cost", "Actual cost"
        PLANNED_PROGRESS = "planned_progress", "Planned progress"
        EARNED_PROGRESS = "earned_progress", "Earned progress"

    snapshot = models.ForeignKey(
        AnalyticalSnapshot,
        on_delete=models.CASCADE,
        related_name="series_points",
        verbose_name="Snapshot",
    )
    series_type = models.CharField(max_length=32, choices=SeriesType.choices, db_index=True)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    cumulative_value = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=16, blank=True)
    authority = models.CharField(max_length=32, blank=True)
    sequence = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Analytical Snapshot Series Point"
        verbose_name_plural = "Analytical Snapshot Series Points"
        ordering = ["series_type", "sequence", "period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "series_type", "period_start", "sequence"],
                name="castor_scheduling_unique_snapshot_series_point",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "series_type", "period_start"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding and self.pk:
            raise ValueError("AnalyticalSnapshotSeriesPoint is immutable after creation.")
        super().save(*args, **kwargs)


class AnalyticalSnapshotPeriod(UUIDModel):
    """Persisted period-table row for snapshot EVM (DF-B2)."""

    snapshot = models.ForeignKey(
        AnalyticalSnapshot,
        on_delete=models.CASCADE,
        related_name="periods",
        verbose_name="Snapshot",
    )
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(null=True, blank=True)
    pv = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    ev = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    ac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    period_pv = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    period_ev = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    period_ac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    spi = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    cpi = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    eac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    vac = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    authority = models.CharField(max_length=32, blank=True)
    coverage = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Analytical Snapshot Period"
        verbose_name_plural = "Analytical Snapshot Periods"
        ordering = ["sequence", "period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "period_start", "sequence"],
                name="castor_scheduling_unique_snapshot_period",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "period_start"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding and self.pk:
            raise ValueError("AnalyticalSnapshotPeriod is immutable after creation.")
        super().save(*args, **kwargs)


class ScheduleSource(UUIDModel):
    """Audit record of each schedule file imported into a project.

    Created by TaskSaveView after a successful import.  Used by the
    Data Sources tab to show the user which files have been imported and
    when — without requiring a FK from every Task back to its source file.

    Transitional role (DF-A1): legacy import audit chip.  New provenance
    authority lives on :class:`ScheduleSourceVersion` and :class:`ScheduleImportRun`.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_sources",
        verbose_name="Project",
    )
    filename = models.CharField(max_length=500, verbose_name="Filename")
    source_format = models.CharField(
        max_length=20,
        choices=Task.Source.choices,
        default=Task.Source.EXCEL,
        verbose_name="Format",
    )
    task_count = models.IntegerField(default=0, verbose_name="Tasks in file")
    imported_at = models.DateTimeField(auto_now_add=True)
    data_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="P6 Data Date",
        help_text="DataDate from P6 XML — the date through which progress was recorded. "
        "Absent for CSV/Excel imports. All EVM metrics are computed as-of this date.",
    )

    class Meta:
        verbose_name = "Schedule Source"
        verbose_name_plural = "Schedule Sources"
        ordering = ["-imported_at"]
        indexes = [models.Index(fields=["project", "imported_at"])]

    def __str__(self) -> str:
        return f"{self.filename} ({self.task_count} tasks)"


class ColumnMappingLookup(UUIDModel):
    """Fingerprint-keyed cache of confirmed column mappings.

    When a user confirms an AI-detected mapping, it is saved here keyed by a
    hash of the file's sorted column headers.  On the next upload whose headers
    produce the same hash, the mapping is auto-applied — skipping the AI call
    entirely and showing "Using saved mapping".
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="column_mapping_lookups",
        verbose_name="Project",
    )
    filename_pattern = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Filename Pattern",
        help_text="Base filename (no extension) of the file that produced this mapping",
    )
    column_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Column Fingerprint",
        help_text="SHA-1 of sorted, lowercased header names — used as the lookup key",
    )
    mapping = models.JSONField(
        verbose_name="Column Mapping",
        help_text="Maps canonical Task fields to original header strings, e.g. {name: 'Task Name'}",
    )
    hit_count = models.IntegerField(default=1, verbose_name="Hit Count")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Column Mapping Lookup"
        verbose_name_plural = "Column Mapping Lookups"
        ordering = ["-updated_at"]
        unique_together = [("project", "column_fingerprint")]
        indexes = [models.Index(fields=["project", "column_fingerprint"])]

    def __str__(self) -> str:
        return f"{self.filename_pattern or 'unknown'} ({self.hit_count} uses)"


class TaskEntityBinding(UUIDModel):
    """Explicit scored binding between a schedule Task and an IFC entity global_id.

    Created by the auto-link algorithm. Separate from the M2M ifc_entities field
    so confidence, method, and review status are preserved alongside the link.
    """

    class LinkMethod(models.TextChoices):
        EXACT = "exact", "Exact match"
        NORMALIZED = "normalized", "Normalized match"
        HEURISTIC = "heuristic", "Type heuristic"
        EMBEDDING = "embedding", "Embedding similarity"
        MANUAL = "manual", "Manual selection"

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="entity_bindings",
        verbose_name="Task",
    )
    entity_global_id = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="IFC Entity GlobalId",
    )
    confidence = models.FloatField(
        default=1.0,
        verbose_name="Confidence",
        help_text="0.0–1.0 score assigned by the linking algorithm",
    )
    link_method = models.CharField(
        max_length=20,
        choices=LinkMethod.choices,
        default=LinkMethod.EXACT,
        verbose_name="Link Method",
    )
    needs_review = models.BooleanField(
        default=False,
        verbose_name="Needs Review",
        help_text="True when confidence is below the auto-accept threshold (0.95)",
    )

    class GovernanceStatus(models.TextChoices):
        """E2-E lifecycle state — complements needs_review for audit transitions."""

        ACTIVE_REVIEW = "active_review", "Active review"
        TRUSTED = "trusted", "Trusted"
        REJECTED = "rejected", "Rejected"
        REVERSED = "reversed", "Reversed"
        SUPERSEDED = "superseded", "Superseded"

    governance_status = models.CharField(
        max_length=20,
        choices=GovernanceStatus.choices,
        default=GovernanceStatus.ACTIVE_REVIEW,
        db_index=True,
        verbose_name="Governance Status",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Active",
        help_text="False when rejected, reversed, or superseded.",
    )
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supersedes",
        verbose_name="Superseded By",
    )
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="Rejected At")
    reversed_at = models.DateTimeField(null=True, blank=True, verbose_name="Reversed At")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Task Entity Binding"
        verbose_name_plural = "Task Entity Bindings"
        ordering = ["-confidence", "created_at"]
        indexes = [
            models.Index(fields=["task", "needs_review"]),
            models.Index(fields=["entity_global_id"]),
            models.Index(fields=["governance_status", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "entity_global_id"],
                name="unique_task_entity_binding",
            )
        ]

    def __str__(self) -> str:
        return f"{self.task.name} → {self.entity_global_id} ({self.link_method}, {self.confidence:.2f})"

    def save(self, *args, **kwargs) -> None:
        """Align lifecycle defaults on create from needs_review (migration-era compatibility)."""
        if self._state.adding and self.governance_status == self.GovernanceStatus.ACTIVE_REVIEW:
            if not self.needs_review:
                self.governance_status = self.GovernanceStatus.TRUSTED
        super().save(*args, **kwargs)


class BindingGovernanceEvent(UUIDModel):
    """Append-only immutable governance decision event (E2-E)."""

    class EventType(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REAFFIRMED = "reaffirmed", "Reaffirmed"
        REVERSED = "reversed", "Reversed"
        SUPERSEDED = "superseded", "Superseded"
        SUPERSEDING_ACCEPTANCE = "superseding_acceptance", "Superseding acceptance"
        M2M_ADDED = "m2m_added", "M2M added"
        M2M_REMOVED = "m2m_removed", "M2M removed"
        PARITY_REPAIRED = "parity_repaired", "Parity repaired"
        CONFLICT_ACKNOWLEDGED = "conflict_acknowledged", "Conflict acknowledged"
        MIGRATION_INITIALIZED = "migration_initialized", "Migration initialized"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="binding_governance_events",
        verbose_name="Project",
    )
    binding = models.ForeignKey(
        TaskEntityBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_events",
        verbose_name="Binding",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="binding_governance_events",
        verbose_name="Task",
    )
    entity_global_id = models.CharField(
        max_length=64, db_index=True, verbose_name="Entity GlobalId"
    )
    ifc_file = models.ForeignKey(
        "ifc_processor.IFCFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="binding_governance_events",
        verbose_name="IFC File",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    previous_state = models.CharField(max_length=32)
    resulting_state = models.CharField(max_length=32)
    reason_code = models.CharField(max_length=64)
    reason_text = models.TextField(blank=True)
    policy_id = models.CharField(max_length=64, default="trusted-binding-v1")
    decision_reference_id = models.CharField(max_length=64, db_index=True)
    batch_fingerprint = models.CharField(max_length=64, blank=True, default="")
    parent_event = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_events",
        verbose_name="Parent Event",
    )
    related_event = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_events",
        verbose_name="Related Event",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="binding_governance_events",
        verbose_name="Actor",
    )
    request_source = models.CharField(max_length=64, blank=True, default="")
    trusted_before = models.BooleanField(null=True)
    trusted_after = models.BooleanField(null=True)
    m2m_before = models.BooleanField(null=True)
    m2m_after = models.BooleanField(null=True)
    replacement_binding = models.ForeignKey(
        TaskEntityBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replacement_events",
        verbose_name="Replacement Binding",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Binding Governance Event"
        verbose_name_plural = "Binding Governance Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["project", "event_type", "created_at"]),
            models.Index(fields=["binding", "created_at"]),
            models.Index(fields=["task", "created_at"]),
            models.Index(fields=["entity_global_id", "created_at"]),
            models.Index(fields=["decision_reference_id"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ValueError(
                "BindingGovernanceEvent records are append-only and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise ValueError("BindingGovernanceEvent records are append-only and cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.event_type} {self.entity_global_id} @ {self.created_at}"


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


class ProjectComprehension(UUIDModel):
    """Semantic understanding of a project schedule, built by the Comprehension Engine.

    Stores both statistical profile (fast, no LLM) and LLM-derived semantic
    understanding (project type, activity code meanings, naming conventions).
    Updated by build_comprehension() after each import.
    """

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="schedule_comprehension",
        verbose_name="Project",
    )

    # WBS / Hierarchy
    wbs_levels = models.IntegerField(default=0, verbose_name="WBS Levels")
    wbs_structure = models.JSONField(
        default=dict,
        verbose_name="WBS Structure",
        help_text='Hierarchy by level, e.g. {"L1": ["Structure", "MEP"]}',
    )

    # Activity Code Pattern
    code_pattern = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Code Pattern",
        help_text='Detected pattern, e.g. "[ALPHA][N]-[N].[N]"',
    )
    code_segments = models.JSONField(
        default=dict,
        verbose_name="Code Segments",
        help_text="Most-common values per positional segment",
    )

    # Counts
    total_activities = models.IntegerField(default=0, verbose_name="Total Activities")
    physical_activities = models.IntegerField(default=0, verbose_name="Physical Activities")
    non_physical_activities = models.IntegerField(default=0, verbose_name="Non-Physical Activities")
    critical_activities = models.IntegerField(default=0, verbose_name="Critical Activities")

    # Date Profile
    project_start = models.DateField(null=True, blank=True, verbose_name="Project Start")
    project_finish = models.DateField(null=True, blank=True, verbose_name="Project Finish")
    avg_duration_days = models.FloatField(default=0.0, verbose_name="Avg Duration (days)")

    # Distributions
    type_distribution = models.JSONField(
        default=dict,
        verbose_name="Type Distribution",
        help_text="Stage or activity-type counts",
    )

    # Naming Conventions — populated from LLM code_prefix_meanings
    naming_conventions = models.JSONField(
        default=dict,
        verbose_name="Naming Conventions",
        help_text='Code prefix meanings, e.g. {"GENDA": "Admin approval"}',
    )

    # Phases & Milestones
    phases = models.JSONField(default=list, verbose_name="Phases")
    milestones = models.JSONField(default=list, verbose_name="Milestones")

    # LLM Output
    ai_summary = models.TextField(blank=True, verbose_name="AI Summary")
    confidence_score = models.FloatField(default=0.0, verbose_name="Confidence Score")

    class Meta:
        verbose_name = "Project Comprehension"
        verbose_name_plural = "Project Comprehensions"

    def __str__(self) -> str:
        return f"Comprehension({self.project.name}, {self.total_activities} tasks)"


class TaskEmbedding(UUIDModel):
    """Cached embedding vector for a schedule task — used by the Intelligence tab."""

    task = models.OneToOneField(
        Task,
        on_delete=models.CASCADE,
        related_name="embedding",
        verbose_name="Task",
    )
    vector = VectorField(
        dimensions=settings.PGVECTOR_DIMENSIONS,
        verbose_name="Embedding Vector",
    )
    embedded_text = models.TextField(
        verbose_name="Embedded Text",
        help_text="The text that was embedded — used to detect staleness.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "Task Embedding"
        verbose_name_plural = "Task Embeddings"


class P6WBSNode(UUIDModel):
    """WBS hierarchy node from a Primavera P6 XML import.

    Persisted by p6_save.save_p6_pending_data() at upload time and confirmed
    (linked to a ScheduleSource) by p6_save.finalise_p6_data() on import commit.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="p6_wbs_nodes",
        verbose_name="Project",
    )
    schedule_source = models.ForeignKey(
        ScheduleSource,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="p6_wbs_nodes",
        verbose_name="Schedule Source",
    )
    p6_object_id = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="P6 ObjectId",
    )
    p6_parent_object_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="P6 Parent ObjectId",
    )
    code = models.CharField(max_length=100, blank=True, verbose_name="WBS Code")
    name = models.CharField(max_length=500, verbose_name="WBS Name")
    original_budget = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Original Budget",
    )
    sequence_number = models.IntegerField(null=True, blank=True, verbose_name="Sequence Number")
    is_pending = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Pending",
        help_text="True until the user confirms the import; False once linked to a ScheduleSource.",
    )

    class Meta:
        verbose_name = "P6 WBS Node"
        verbose_name_plural = "P6 WBS Nodes"
        ordering = ["sequence_number", "code"]
        indexes = [
            models.Index(fields=["project", "is_pending"]),
            models.Index(fields=["project", "p6_object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class P6ResourceAssignment(UUIDModel):
    """Resource assignment (cost record) from a Primavera P6 XML import.

    Each row corresponds to one <ResourceAssignment> element in the P6 XML.
    Planned/actual costs here are the authoritative EVM cost data — Task.cost
    stores the per-activity sum, while these rows let the EVM screen aggregate
    by WBS, resource type, or date range.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="p6_resource_assignments",
        verbose_name="Project",
    )
    schedule_source = models.ForeignKey(
        ScheduleSource,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="p6_resource_assignments",
        verbose_name="Schedule Source",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="p6_resource_assignments",
        verbose_name="Task",
        help_text="Resolved after import by matching p6_activity_object_id → Task._p6_obj_id.",
    )
    p6_activity_object_id = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="P6 Activity ObjectId",
    )
    p6_resource_object_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="P6 Resource ObjectId",
    )
    resource_type = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Resource Type",
        help_text="Material, Labor, or Equipment.",
    )
    planned_cost = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, verbose_name="Planned Cost"
    )
    actual_cost = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, verbose_name="Actual Cost"
    )
    remaining_cost = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, verbose_name="Remaining Cost"
    )
    at_completion_cost = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, verbose_name="At Completion Cost"
    )
    planned_units = models.DecimalField(
        max_digits=16, decimal_places=4, default=0, verbose_name="Planned Units"
    )
    actual_units = models.DecimalField(
        max_digits=16, decimal_places=4, default=0, verbose_name="Actual Units"
    )
    is_pending = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Pending",
        help_text="True until the user confirms the import; False once linked to a ScheduleSource.",
    )

    class Meta:
        verbose_name = "P6 Resource Assignment"
        verbose_name_plural = "P6 Resource Assignments"
        indexes = [
            models.Index(fields=["project", "is_pending"]),
            models.Index(fields=["task"]),
            models.Index(fields=["p6_activity_object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.p6_activity_object_id} → {self.resource_type} (planned={self.planned_cost})"


class P6Calendar(UUIDModel):
    """P6 calendar definition — working day schedule + holiday exceptions.

    Used by the CPM engine for working-day duration and date arithmetic.
    Persisted alongside WBS/ResourceAssignment via the same pending→confirmed flow.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="p6_calendars",
        verbose_name="Project",
    )
    schedule_source = models.ForeignKey(
        ScheduleSource,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="p6_calendars",
        verbose_name="Schedule Source",
    )
    p6_calendar_id = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="P6 Calendar ObjectId",
    )
    name = models.CharField(max_length=200, verbose_name="Name")
    hours_per_day = models.FloatField(default=8.0, verbose_name="Hours per Day")
    working_days = models.JSONField(
        default=list,
        verbose_name="Working Day Names",
        help_text='List of working day names, e.g. ["Sunday", "Monday", ...]',
    )
    holidays = models.JSONField(
        default=list,
        verbose_name="Holiday Exceptions",
        help_text="ISO-date strings of non-working exception days.",
    )
    is_pending = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Pending",
    )

    class Meta:
        verbose_name = "P6 Calendar"
        verbose_name_plural = "P6 Calendars"
        indexes = [
            models.Index(fields=["project", "is_pending"]),
            models.Index(fields=["project", "p6_calendar_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} (id={self.p6_calendar_id})"


from scheduling.governed_mapping_models import *  # noqa: E402, F403
from scheduling.resource_foundation_models import *  # noqa: E402, F403
