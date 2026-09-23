# scheduling/governed_mapping_models.py
"""Governed analytical mapping domain models (DF-D1)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import UUIDModel
from environments.models import Project


class AnalyticalDimension(UUIDModel):
    """Governed analytical dimension revision — stable identity via dimension_key."""

    class DimensionType(models.TextChoices):
        TRADE = "trade", "Trade"
        PACKAGE = "package", "Package"
        DISCIPLINE = "discipline", "Discipline"
        LOCATION = "location", "Location"
        ZONE = "zone", "Zone"
        SYSTEM = "system", "System"
        CUSTOM = "custom", "Custom"

    class StructureType(models.TextChoices):
        FLAT = "flat", "Flat"
        HIERARCHICAL = "hierarchical", "Hierarchical"

    class Cardinality(models.TextChoices):
        SINGLE = "single", "Single-valued"
        MULTIPLE = "multiple", "Multi-valued"

    class AuthorityPolicy(models.TextChoices):
        MANUAL_APPROVAL = "manual_approval", "Manual approval"
        IMPORTED_AUTHORITATIVE = "imported_authoritative", "Imported authoritative"
        GOVERNED_SOURCE = "governed_source", "Governed source"
        MIXED = "mixed", "Mixed"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analytical_dimensions",
        verbose_name="Project",
    )
    dimension_key = models.CharField(max_length=64, db_index=True, verbose_name="Dimension Key")
    name = models.CharField(max_length=255, verbose_name="Name")
    description = models.TextField(blank=True, verbose_name="Description")
    dimension_type = models.CharField(
        max_length=20,
        choices=DimensionType.choices,
        db_index=True,
        verbose_name="Dimension Type",
    )
    structure_type = models.CharField(
        max_length=16,
        choices=StructureType.choices,
        default=StructureType.FLAT,
        verbose_name="Structure Type",
    )
    cardinality = models.CharField(
        max_length=16,
        choices=Cardinality.choices,
        default=Cardinality.SINGLE,
        verbose_name="Cardinality",
    )
    authority_policy = models.CharField(
        max_length=32,
        choices=AuthorityPolicy.choices,
        default=AuthorityPolicy.MANUAL_APPROVAL,
        verbose_name="Authority Policy",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    revision_number = models.PositiveIntegerField(default=1, verbose_name="Revision")
    parent_dimension = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_revisions",
        verbose_name="Parent Dimension Revision",
    )
    is_selected_for_analysis = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Selected for Analysis",
    )
    source_metadata = models.JSONField(default=dict, blank=True, verbose_name="Source Metadata")
    governance_metadata = models.JSONField(
        default=dict, blank=True, verbose_name="Governance Metadata"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_dimensions_created",
        verbose_name="Created By",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_dimensions_activated",
        verbose_name="Activated By",
    )
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Activated At")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="Superseded At")

    class Meta:
        verbose_name = "Analytical Dimension"
        verbose_name_plural = "Analytical Dimensions"
        ordering = ["-created_at", "-revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "dimension_key", "revision_number"],
                name="castor_scheduling_unique_dimension_revision",
            ),
            models.UniqueConstraint(
                fields=["project", "dimension_key"],
                condition=models.Q(is_selected_for_analysis=True),
                name="castor_scheduling_unique_selected_dimension_key",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "dimension_key", "status"]),
            models.Index(fields=["project", "dimension_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.dimension_key} r{self.revision_number} ({self.status})"

    @property
    def is_immutable(self) -> bool:
        return self.status in {self.Status.ACTIVE, self.Status.SUPERSEDED, self.Status.ARCHIVED}


class AnalyticalDimensionValue(UUIDModel):
    """Value within a governed dimension revision."""

    class IdentityStatus(models.TextChoices):
        RESOLVED = "resolved", "Resolved"
        GENERATED = "generated", "Generated"
        UNRESOLVED = "unresolved", "Unresolved"
        RETIRED = "retired", "Retired"

    class ValueAuthority(models.TextChoices):
        SOURCE = "source", "Source"
        MANUAL = "manual", "Manual"
        GOVERNED = "governed", "Governed"
        INFERRED = "inferred", "Inferred"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        RETIRED = "retired", "Retired"

    dimension = models.ForeignKey(
        AnalyticalDimension,
        on_delete=models.CASCADE,
        related_name="values",
        verbose_name="Dimension",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        verbose_name="Parent Value",
    )
    code = models.CharField(max_length=64, blank=True, verbose_name="Code")
    name = models.CharField(max_length=255, verbose_name="Name")
    description = models.TextField(blank=True, verbose_name="Description")
    sequence = models.PositiveIntegerField(default=0, verbose_name="Sequence")
    path = models.CharField(max_length=512, blank=True, db_index=True, verbose_name="Path")
    depth = models.PositiveSmallIntegerField(default=0, verbose_name="Depth")
    external_id = models.CharField(
        max_length=128, blank=True, db_index=True, verbose_name="External ID"
    )
    identity_status = models.CharField(
        max_length=16,
        choices=IdentityStatus.choices,
        default=IdentityStatus.RESOLVED,
        verbose_name="Identity Status",
    )
    authority = models.CharField(
        max_length=16,
        choices=ValueAuthority.choices,
        default=ValueAuthority.MANUAL,
        verbose_name="Authority",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        verbose_name="Status",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Analytical Dimension Value"
        verbose_name_plural = "Analytical Dimension Values"
        ordering = ["depth", "sequence", "name"]
        indexes = [
            models.Index(fields=["dimension", "status"]),
            models.Index(fields=["dimension", "path"]),
        ]

    def __str__(self) -> str:
        return self.name or self.code or str(self.pk)


class AnalyticalMappingSet(UUIDModel):
    """Versioned governed mapping set for one dimension revision."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analytical_mapping_sets",
        verbose_name="Project",
    )
    dimension = models.ForeignKey(
        AnalyticalDimension,
        on_delete=models.CASCADE,
        related_name="mapping_sets",
        verbose_name="Dimension",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    source_version = models.ForeignKey(
        "ScheduleSourceVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="analytical_mapping_sets",
        verbose_name="Source Version",
    )
    baseline_version = models.ForeignKey(
        "BaselineVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="analytical_mapping_sets",
        verbose_name="Baseline Version",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    revision = models.PositiveIntegerField(default=1, verbose_name="Revision")
    effective_from = models.DateField(null=True, blank=True, verbose_name="Effective From")
    effective_to = models.DateField(null=True, blank=True, verbose_name="Effective To")
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by_sets",
        verbose_name="Supersedes",
    )
    validation_summary = models.JSONField(
        default=dict, blank=True, verbose_name="Validation Summary"
    )
    coverage_summary = models.JSONField(default=dict, blank=True, verbose_name="Coverage Summary")
    conflict_summary = models.JSONField(default=dict, blank=True, verbose_name="Conflict Summary")
    is_selected_for_analysis = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Selected for Analysis",
    )
    inherit_wbs_to_tasks = models.BooleanField(
        default=True,
        verbose_name="Inherit WBS mappings to descendant Tasks",
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_sets_created",
        verbose_name="Created By",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_sets_submitted",
        verbose_name="Submitted By",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_sets_approved",
        verbose_name="Approved By",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_sets_rejected",
        verbose_name="Rejected By",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_sets_activated",
        verbose_name="Activated By",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="Submitted At")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Approved At")
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="Rejected At")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Activated At")
    superseded_at = models.DateTimeField(null=True, blank=True, verbose_name="Superseded At")

    class Meta:
        verbose_name = "Analytical Mapping Set"
        verbose_name_plural = "Analytical Mapping Sets"
        ordering = ["-created_at", "-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["dimension", "revision"],
                name="castor_scheduling_unique_mapping_set_revision",
            ),
            models.UniqueConstraint(
                fields=["dimension"],
                condition=models.Q(is_selected_for_analysis=True),
                name="castor_scheduling_unique_selected_mapping_set",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["dimension", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} r{self.revision} ({self.status})"

    @property
    def is_immutable(self) -> bool:
        return self.status in {
            self.Status.APPROVED,
            self.Status.ACTIVE,
            self.Status.SUPERSEDED,
            self.Status.ARCHIVED,
        }


class AnalyticalMappingAssignment(UUIDModel):
    """One governed mapping from a dimension value to a single analytical target."""

    class TargetType(models.TextChoices):
        TASK = "task", "Task"
        WBS_NODE = "wbs_node", "WBS Node"
        IFC_ENTITY = "ifc_entity", "IFC Entity"
        SCHEDULE_ACTIVITY = "schedule_activity", "Schedule Activity"

    class MappingMethod(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORTED = "imported", "Imported"
        APPROVED_SUGGESTION = "approved_suggestion", "Approved suggestion"
        GOVERNED_RULE = "governed_rule", "Governed rule"
        INHERITED = "inherited", "Inherited"
        SYSTEM = "system", "System"

    class MappingAuthority(models.TextChoices):
        AUTHORITATIVE = "authoritative", "Authoritative"
        APPROVED = "approved", "Approved"
        SUGGESTED = "suggested", "Suggested"
        PROXY = "proxy", "Proxy"
        UNAVAILABLE = "unavailable", "Unavailable"

    class GovernanceStatus(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"

    mapping_set = models.ForeignKey(
        AnalyticalMappingSet,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="Mapping Set",
    )
    dimension_value = models.ForeignKey(
        AnalyticalDimensionValue,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="Dimension Value",
    )
    target_type = models.CharField(
        max_length=20,
        choices=TargetType.choices,
        db_index=True,
        verbose_name="Target Type",
    )
    task = models.ForeignKey(
        "Task",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="analytical_mapping_assignments",
        verbose_name="Task",
    )
    wbs_node = models.ForeignKey(
        "WBSNode",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="analytical_mapping_assignments",
        verbose_name="WBS Node",
    )
    entity_global_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="IFC Entity GlobalId",
    )
    ifc_file = models.ForeignKey(
        "ifc_processor.IFCFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytical_mapping_assignments",
        verbose_name="IFC File",
    )
    schedule_activity = models.ForeignKey(
        "ScheduleActivity",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="analytical_mapping_assignments",
        verbose_name="Schedule Activity",
    )
    mapping_method = models.CharField(
        max_length=24,
        choices=MappingMethod.choices,
        default=MappingMethod.MANUAL,
        verbose_name="Mapping Method",
    )
    authority = models.CharField(
        max_length=16,
        choices=MappingAuthority.choices,
        default=MappingAuthority.APPROVED,
        verbose_name="Authority",
    )
    governance_status = models.CharField(
        max_length=16,
        choices=GovernanceStatus.choices,
        default=GovernanceStatus.PROPOSED,
        db_index=True,
        verbose_name="Governance Status",
    )
    confidence = models.FloatField(null=True, blank=True, verbose_name="Confidence")
    evidence = models.JSONField(default=dict, blank=True, verbose_name="Evidence")
    provenance = models.JSONField(default=dict, blank=True, verbose_name="Provenance")
    source_assignment = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derived_assignments",
        verbose_name="Source Assignment",
    )
    inherited_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inherited_assignments",
        verbose_name="Inherited From",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_assignments_proposed",
        verbose_name="Proposed By",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_assignments_reviewed",
        verbose_name="Reviewed By",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_assignments_approved",
        verbose_name="Approved By",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_assignments_rejected",
        verbose_name="Rejected By",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="Reviewed At")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Approved At")
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="Rejected At")
    rejection_reason = models.TextField(blank=True, verbose_name="Rejection Reason")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Analytical Mapping Assignment"
        verbose_name_plural = "Analytical Mapping Assignments"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        target_type="task",
                        task_id__isnull=False,
                        wbs_node_id__isnull=True,
                        schedule_activity_id__isnull=True,
                        entity_global_id="",
                    )
                    | Q(
                        target_type="wbs_node",
                        wbs_node_id__isnull=False,
                        task_id__isnull=True,
                        schedule_activity_id__isnull=True,
                        entity_global_id="",
                    )
                    | Q(
                        target_type="ifc_entity",
                        entity_global_id__gt="",
                        task_id__isnull=True,
                        wbs_node_id__isnull=True,
                        schedule_activity_id__isnull=True,
                    )
                    | Q(
                        target_type="schedule_activity",
                        schedule_activity_id__isnull=False,
                        task_id__isnull=True,
                        wbs_node_id__isnull=True,
                        entity_global_id="",
                    )
                ),
                name="castor_scheduling_mapping_assignment_one_target",
            ),
        ]
        indexes = [
            models.Index(fields=["mapping_set", "governance_status"]),
            models.Index(fields=["task", "governance_status"]),
            models.Index(fields=["wbs_node", "governance_status"]),
            models.Index(fields=["schedule_activity", "governance_status"]),
            models.Index(fields=["entity_global_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.target_type} → {self.dimension_value_id} ({self.governance_status})"

    @property
    def is_effective(self) -> bool:
        return self.governance_status == self.GovernanceStatus.APPROVED


class MappingGovernanceEvent(UUIDModel):
    """Append-only governed mapping audit event."""

    class EventType(models.TextChoices):
        DIMENSION_CREATED = "dimension_created", "Dimension created"
        DIMENSION_ACTIVATED = "dimension_activated", "Dimension activated"
        DIMENSION_SUPERSEDED = "dimension_superseded", "Dimension superseded"
        VALUE_CREATED = "value_created", "Value created"
        VALUE_RETIRED = "value_retired", "Value retired"
        MAPPING_SET_CREATED = "mapping_set_created", "Mapping set created"
        MAPPING_SET_SUBMITTED = "mapping_set_submitted", "Mapping set submitted"
        MAPPING_SET_APPROVED = "mapping_set_approved", "Mapping set approved"
        MAPPING_SET_ACTIVATED = "mapping_set_activated", "Mapping set activated"
        MAPPING_SET_REJECTED = "mapping_set_rejected", "Mapping set rejected"
        ASSIGNMENT_PROPOSED = "assignment_proposed", "Assignment proposed"
        ASSIGNMENT_APPROVED = "assignment_approved", "Assignment approved"
        ASSIGNMENT_REJECTED = "assignment_rejected", "Assignment rejected"
        CONFLICT_DETECTED = "conflict_detected", "Conflict detected"
        REVISION_SUPERSEDED = "revision_superseded", "Revision superseded"
        POPULATION_STARTED = "mapping_population_started", "Population started"
        POPULATION_COMPLETED = "mapping_population_completed", "Population completed"
        POPULATION_FAILED = "mapping_population_failed", "Population failed"
        PROPOSAL_ADOPTED = "proposal_adopted", "Proposal adopted"
        PROPOSAL_DUPLICATE_SKIPPED = "proposal_duplicate_skipped", "Proposal duplicate skipped"
        SET_ACTIVATION_FAILED = "mapping_set_activation_failed", "Mapping set activation failed"
        CROSS_VERSION_RESOLVED = "cross_version_mapping_resolved", "Cross-version resolved"
        CROSS_VERSION_BLOCKED = "cross_version_mapping_blocked", "Cross-version blocked"
        ADOPTION_DRY_RUN = "adoption_dry_run", "Adoption dry run"
        AUTHORITATIVE_IMPORTED = "authoritative_mapping_imported", "Authoritative imported"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="mapping_governance_events",
        verbose_name="Project",
    )
    dimension = models.ForeignKey(
        AnalyticalDimension,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_events",
        verbose_name="Dimension",
    )
    mapping_set = models.ForeignKey(
        AnalyticalMappingSet,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_events",
        verbose_name="Mapping Set",
    )
    assignment = models.ForeignKey(
        AnalyticalMappingAssignment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_events",
        verbose_name="Assignment",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    previous_state = models.CharField(max_length=32, blank=True)
    resulting_state = models.CharField(max_length=32, blank=True)
    target_type = models.CharField(max_length=20, blank=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    reason_code = models.CharField(max_length=64, blank=True)
    reason_text = models.TextField(blank=True)
    evidence_summary = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_governance_events",
        verbose_name="Actor",
    )

    class Meta:
        verbose_name = "Mapping Governance Event"
        verbose_name_plural = "Mapping Governance Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "event_type"]),
            models.Index(fields=["project", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.project_id})"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ValueError(
                "MappingGovernanceEvent records are append-only and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise ValueError("MappingGovernanceEvent records are append-only and cannot be deleted.")
