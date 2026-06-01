# islam/scheduling/models.py
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


class IslamProgressMode(UUIDModel):
    """Per-project schedule progress calculation mode for the Insights dashboard ring."""

    class Mode(models.TextChoices):
        BY_COUNT = "count", "By Task Count"
        BY_COST = "cost", "By Cost"
        BY_DURATION = "duration", "By Duration"
        BY_WEIGHT = "weight", "Custom Weight"

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="islam_progress_mode",
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


class ScheduleSource(UUIDModel):
    """Audit record of each schedule file imported into a project.

    Created by TaskSaveView after a successful import.  Used by the
    Data Sources tab to show the user which files have been imported and
    when — without requiring a FK from every Task back to its source file.
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Task Entity Binding"
        verbose_name_plural = "Task Entity Bindings"
        ordering = ["-confidence", "created_at"]
        indexes = [
            models.Index(fields=["task", "needs_review"]),
            models.Index(fields=["entity_global_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "entity_global_id"],
                name="unique_task_entity_binding",
            )
        ]

    def __str__(self) -> str:
        return f"{self.task.name} → {self.entity_global_id} ({self.link_method}, {self.confidence:.2f})"


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


class IslamTaskEmbedding(UUIDModel):
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
