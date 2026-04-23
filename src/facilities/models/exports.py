# facilities/models/exports.py
"""Export-reconciliation domain models for 7D Facility Management (M2).

FM writes accumulate as :class:`FMDelta` rows against the Castor DB. An
explicit **Export IFC** action then replays all pending deltas through the
existing Tier 1 / Tier 2 IFC writers, produces a single Git commit for the
whole batch, and stamps each delta with the resulting commit hash.

Three models shape the flow:

``FMDelta``
    The pending-change queue. One row per atomic write (entity, operation,
    target). Dedup and last-write-wins collapse happen at plan time, not
    at insert.

``ExportJob``
    A run log for one export attempt. Tracks the delta/entity/conflict
    counts snapshotted at plan time, the final commit hash on success, and
    the error reason on failure.

``ExportProfile``
    Per-project tuning knobs for the export (allowed operations, pset
    allowlist). V1 ships a single opinionated default per project; full
    per-project tuning is a later-milestone concern.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.models import IFCFile

from .assets import FacilityAsset

# Default allowlist for a freshly-seeded ExportProfile.
DEFAULT_ENABLED_OPERATIONS = ["SET_PROPERTY", "ADD_PSET", "SET_CLASSIFICATION", "SET_ATTRIBUTE"]
DEFAULT_ENABLED_PSETS = ["Pset_ManufacturerOccurrence", "Pset_Warranty", "Pset_AssetCommon"]


class ExportProfile(UUIDModel):
    """Per-project tuning for what FM ops may reach the IFC at export time.

    V1: one default row per project, seeded lazily by the service on first
    export. No CRUD UI. Model exists so that later milestones can open it up
    without a schema migration.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="fm_export_profiles",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=64,
        default="default",
        verbose_name="Name",
    )
    is_default = models.BooleanField(
        default=True,
        verbose_name="Is Default",
        help_text="The profile used when Export IFC is triggered without an explicit choice.",
    )
    enabled_operations = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Enabled Operations",
        help_text="Subset of SET_PROPERTY / ADD_PSET / SET_CLASSIFICATION / SET_ATTRIBUTE.",
    )
    enabled_pset_allowlist = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Enabled Pset Allowlist",
        help_text="Psets that may be written or merged by the export. Empty list = no allowlist (all).",
    )

    class Meta:
        ordering = ["project", "name"]
        verbose_name = "Export Profile"
        verbose_name_plural = "Export Profiles"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="uniq_export_profile_per_project",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(is_default=True),
                name="uniq_default_export_profile_per_project",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project})"


class ExportJob(UUIDModel):
    """One Export IFC attempt. Written at plan time, updated through apply."""

    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="fm_export_jobs",
        verbose_name="Project",
    )
    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fm_export_jobs",
        verbose_name="IFC File",
        help_text=(
            "The IFC file targeted by this export. Goes NULL if the file "
            "or its parent project is later deleted; the job is kept for audit "
            "(commit_hash and counts remain)."
        ),
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_fm_export_jobs",
        verbose_name="Initiated By",
    )
    export_profile = models.ForeignKey(
        ExportProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
        verbose_name="Export Profile",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PLANNING,
        db_index=True,
        verbose_name="Status",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Started At",
        help_text="Set when the apply phase begins.",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Completed At",
    )

    # Plan-phase snapshot, stored so the audit log survives even if
    # the delta rows are later pruned or the plan was discarded.
    delta_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Delta Count",
    )
    entity_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Entity Count",
    )
    conflict_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Conflict Count",
        help_text="Deltas superseded by a later delta on the same target.",
    )

    commit_hash = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        verbose_name="Commit Hash",
        help_text="Git commit produced by the export (populated on success).",
    )
    error_reason = models.TextField(
        blank=True,
        verbose_name="Error Reason",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Export Job"
        verbose_name_plural = "Export Jobs"
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"Export {self.pk.hex[:8]} — {self.status} ({self.delta_count} deltas)"


class FMDelta(UUIDModel):
    """A pending FM write that will be replayed into the IFC on export.

    One row per atomic change. ``update_asset`` that touches three pset-bound
    fields produces three rows; ``bulk_classify`` on twenty assets produces
    twenty rows. Dedup and last-write-wins collapse happen at plan time in
    :class:`facilities.services.export_reconciliation_service.ExportReconciliationService`,
    not at insert — the DB keeps the full history for audit.

    ``entity_guid`` is denormalized from ``asset.ifc_entity.global_id`` at
    insert and is immutable thereafter: it survives asset deletion (the
    ``asset`` FK goes ``SET_NULL``) so the delta can still be replayed by
    GUID alone.
    """

    class Operation(models.TextChoices):
        SET_PROPERTY = "SET_PROPERTY", "Set Property"
        ADD_PSET = "ADD_PSET", "Add/Merge Property Set"
        SET_CLASSIFICATION = "SET_CLASSIFICATION", "Set Classification"
        SET_ATTRIBUTE = "SET_ATTRIBUTE", "Set Attribute"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="fm_deltas",
        verbose_name="Project",
    )
    asset = models.ForeignKey(
        FacilityAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fm_deltas",
        verbose_name="Facility Asset",
        help_text="The asset whose mutation produced this delta. May be null if the asset has been deleted after the delta was queued.",
    )
    entity_guid = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Entity GlobalId",
        help_text="IFC GlobalId of the target entity. Denormalized from the asset at insert; immutable.",
    )

    operation = models.CharField(
        max_length=32,
        choices=Operation.choices,
        db_index=True,
        verbose_name="Operation",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Payload",
        help_text=(
            "Operation-specific payload. Shapes: "
            "SET_PROPERTY / ADD_PSET → {pset, property, value, old_value}; "
            "SET_CLASSIFICATION → {action, system, code, name}; "
            "SET_ATTRIBUTE → {attribute, value, old_value}."
        ),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_fm_deltas",
        verbose_name="Created By",
    )

    applied_to_ifc_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Applied To IFC At",
        help_text="Set when the export that carried this delta commits successfully.",
    )
    superseded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Superseded At",
        help_text=(
            "Set when an export's plan phase collapses this delta under a later delta "
            "on the same (entity_guid, operation, target) tuple. Kept for audit; not replayed."
        ),
    )
    export_job = models.ForeignKey(
        ExportJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deltas",
        verbose_name="Export Job",
        help_text="The export run that applied or superseded this delta.",
    )
    ifc_commit_hash = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        verbose_name="IFC Commit Hash",
        help_text="Mirror of export_job.commit_hash, denormalized for fast audit queries.",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "FM Delta"
        verbose_name_plural = "FM Deltas"
        indexes = [
            # Pending-delta count for the project header banner — partial
            # index so the query stays O(pending) regardless of audit volume.
            models.Index(
                fields=["project"],
                name="fm_delta_pending_idx",
                condition=Q(applied_to_ifc_at__isnull=True) & Q(superseded_at__isnull=True),
            ),
            # Plan-phase grouping: walk by (project, entity_guid) to collapse
            # last-write-wins per target.
            models.Index(fields=["project", "entity_guid"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.operation} on {self.entity_guid[:8]} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def is_pending(self) -> bool:
        """True while the delta is still waiting to be exported."""
        return self.applied_to_ifc_at is None and self.superseded_at is None

    @property
    def natural_key(self) -> tuple:
        """Target tuple used for conflict collapse at plan time.

        Two pending deltas with the same ``natural_key`` target the same IFC
        write; the plan phase keeps only the most recent one and marks the
        earlier ones as ``superseded_at``.
        """
        op = self.operation
        p = self.payload or {}
        if op in (self.Operation.SET_PROPERTY, self.Operation.ADD_PSET):
            return (self.entity_guid, op, p.get("pset", ""), p.get("property", ""))
        if op == self.Operation.SET_ATTRIBUTE:
            return (self.entity_guid, op, p.get("attribute", ""))
        if op == self.Operation.SET_CLASSIFICATION:
            # An add and a remove on the same (system, code) are the same target.
            return (self.entity_guid, op, p.get("system", ""), p.get("code", ""))
        return (self.entity_guid, op)
