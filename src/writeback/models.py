"""Writeback models - IFC modification and Git tracking."""

from django.conf import settings
from django.db import models

from chat.models import Message
from core.models import UUIDModel
from ifc_processor.models import IFCFile


class ModificationProposal(UUIDModel):
    """A proposed modification to an IFC file."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        SUPERSEDED = "superseded", "Superseded"

    class Tier(models.IntegerChoices):
        GREEN = 1, "Tier 1 – Certified Ops (GREEN)"
        ORANGE = 2, "Tier 2 – Operation Planner (ORANGE)"
        RED = 3, "Tier 3 – IfcOpenShell Direct (RED)"

    # Link to the message that triggered this
    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="proposal",
        verbose_name="Source Message",
    )
    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="proposals",
        verbose_name="IFC File",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_proposals",
        verbose_name="Created By",
    )
    # The natural language request
    request_text = models.TextField(
        verbose_name="Request",
        help_text="Original natural language modification request",
    )
    # AI explanation of changes
    explanation = models.TextField(
        blank=True,
        verbose_name="Explanation",
        help_text="The blind explanation; empty when the explainer failed (the card shows a placeholder)",
    )
    # ── V3: the proposal row is the journal ────────────────
    code = models.TextField(
        blank=True,
        default="",
        verbose_name="Generated Code",
        help_text="The select()/modify() block that produced the diff; runs once, at proposal time",
    )
    target_global_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Target GlobalIds",
        help_text="What select() returned; the only entities modify() may touch",
    )
    diff = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Diff",
        help_text="IfcDiff.as_dict(): what the code did to the scratch copy, measured by the harness",
    )
    base_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Base Fingerprint",
        help_text="SHA-256 of the original file when proposed; approval refuses on mismatch",
    )
    scratch_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Scratch Path",
        help_text="The reviewed copy that replaces the original on approval",
    )
    explainer_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Explainer Model",
        help_text="The model that wrote the blind explanation",
    )
    guardian_skipped = models.BooleanField(
        default=False,
        verbose_name="Guardian Skipped",
        help_text="The user switched the document check off for this request",
    )
    flags_acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Flags Acknowledged At",
        help_text="When the user ticked every flagged diff row in the approve request",
    )
    # ── V2 columns, kept nullable for history rows; never reused ──
    changes = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Changes (V2)",
        help_text="V2 mutation journal. Null on V3 proposals",
    )
    diff_preview = models.TextField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Diff Preview (V2)",
        help_text="V2 rendered diff rows as JSON text. Null on V3 proposals",
    )
    # Affected entity count
    affected_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Affected Entities",
    )
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    # Review info
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_proposals",
        verbose_name="Reviewed By",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Reviewed At",
    )
    rejection_reason = models.TextField(
        blank=True,
        verbose_name="Rejection Reason",
    )
    # Git commit reference (after applying)
    git_commit = models.OneToOneField(
        "GitCommit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proposal",
        verbose_name="Git Commit",
    )
    # ── V2 RSAA classification, kept nullable for history rows ──
    tier = models.IntegerField(
        choices=Tier.choices,
        null=True,
        blank=True,
        verbose_name="RSAA Tier (V2)",
        help_text="V2 tier. Null on V3 proposals",
    )
    operation = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        default=None,
        verbose_name="Operation (V2)",
        help_text="V2 operation name. Null on V3 proposals",
    )
    intent_json = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Intent JSON (V2)",
        help_text="V2 parsed intent. Null on V3 proposals",
    )
    filter_spec = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Filter Spec (V2)",
        help_text="V2 entity filter. Null on V3 proposals",
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Confidence (V2)",
        help_text="V2 classification confidence. Null on V3 proposals",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
        help_text="Error details if status is 'failed'",
    )
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Applied At",
    )
    linked_conflict_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Linked Conflict IDs",
        help_text="List of Conflict UUIDs (as strings) linked when this proposal was created via 'Fix in Modify'",
    )

    # Castor Guardian fields
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Checking..."
        VERIFIED = "verified", "Verified"
        CONFLICT = "conflict", "Conflict Detected"
        UNKNOWN = "unknown", "No Info Found"
        FAILED = "failed", "Check Failed"

    # Guardian Fields
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        db_index=True,
    )
    verification_result = models.TextField(
        blank=True, help_text="LLM explanation of the verification check."
    )
    verification_source = models.CharField(
        max_length=255, blank=True, help_text="Citation (e.g., 'Fire Strategy.pdf, p.14')"
    )

    # V2 code-review acknowledgement, kept for history rows. V3 gates on
    # flagged diff rows instead (``flags_acknowledged_at``).
    code_review_acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Code Review Acknowledged At (V2)",
    )
    code_review_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="code_acknowledged_proposals",
        verbose_name="Code Review Acknowledged By (V2)",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Modification Proposal"
        verbose_name_plural = "Modification Proposals"
        indexes = [
            models.Index(fields=["ifc_file", "status"]),
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Proposal: {self.request_text[:50]}..."

    @property
    def is_v3(self) -> bool:
        """True for proposals that carry code, targets and a measured diff."""
        return bool(self.code) and isinstance(self.diff, dict)

    @property
    def flagged_keys(self) -> set[str]:
        """Keys of the diff rows the one flag rule flags for this request."""
        from writeback.services.verifier import flagged_keys

        return flagged_keys(self.diff or {}, self.request_text or "")

    @property
    def has_flagged_rows(self) -> bool:
        """True when approval needs every flagged row ticked."""
        return bool(self.flagged_keys)


class GitCommit(UUIDModel):
    """A Git commit representing an IFC modification."""

    ifc_file = models.ForeignKey(
        IFCFile,
        on_delete=models.CASCADE,
        related_name="commits",
        verbose_name="IFC File",
    )

    # Git data
    commit_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name="Commit Hash",
    )
    parent_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="Parent Hash",
    )

    # Commit metadata
    message = models.TextField(
        verbose_name="Commit Message",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="git_commits",
        verbose_name="Author",
    )

    # Change summary
    entities_modified = models.PositiveIntegerField(
        default=0,
        verbose_name="Entities Modified",
    )
    entities_added = models.PositiveIntegerField(
        default=0,
        verbose_name="Entities Added",
    )
    entities_removed = models.PositiveIntegerField(
        default=0,
        verbose_name="Entities Removed",
    )

    # Diff storage
    diff_data = models.JSONField(
        default=dict,
        verbose_name="Diff Data",
        help_text="Detailed diff information as JSON",
    )
    rolled_back = models.BooleanField(
        default=False,
        verbose_name="Rolled Back",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Git Commit"
        verbose_name_plural = "Git Commits"
        indexes = [
            models.Index(fields=["ifc_file", "-created_at"]),
            models.Index(fields=["author", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.commit_hash[:8]}: {self.message[:50]}"


class ScanRun(UUIDModel):
    """Audit record for a single conflict scan execution."""

    class ScanType(models.TextChoices):
        FULL = "full", "Full Scan"
        TARGETED_DOC = "targeted_doc", "Targeted (Document)"
        TARGETED_IFC = "targeted_ifc", "Targeted (IFC Entity)"
        POST_MODIFY = "post_modify", "Post-Modification"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="scan_runs",
        verbose_name="Project",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_runs",
        verbose_name="Triggered By",
    )
    scan_type = models.CharField(
        max_length=20,
        choices=ScanType.choices,
        default=ScanType.FULL,
        verbose_name="Scan Type",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    entities_scanned = models.PositiveIntegerField(default=0, verbose_name="Entities Scanned")
    chunks_compared = models.PositiveIntegerField(default=0, verbose_name="Chunks Compared")
    conflicts_found = models.PositiveIntegerField(default=0, verbose_name="Conflicts Found")
    llm_model_used = models.CharField(max_length=100, blank=True, verbose_name="LLM Model Used")
    error_message = models.TextField(blank=True, verbose_name="Error Message")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Scan Run"
        verbose_name_plural = "Scan Runs"
        indexes = [
            models.Index(fields=["project", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"ScanRun {self.scan_type} [{self.status}] @ {self.created_at:%Y-%m-%d %H:%M}"


class Conflict(UUIDModel):
    """A detected conflict between IFC and documents."""

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"
        DISMISSED = "dismissed", "Dismissed"

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="conflicts",
        verbose_name="Project",
    )
    # What's conflicting
    ifc_entity = models.ForeignKey(
        "ifc_processor.IFCEntity",
        on_delete=models.CASCADE,
        related_name="conflicts",
        null=True,
        blank=True,
        verbose_name="IFC Entity",
    )
    document_chunk = models.ForeignKey(
        "documents.DocumentChunk",
        on_delete=models.CASCADE,
        related_name="conflicts",
        null=True,
        blank=True,
        verbose_name="Document Chunk",
    )
    # Conflict details
    title = models.CharField(
        max_length=255,
        verbose_name="Title",
    )
    description = models.TextField(
        verbose_name="Description",
    )
    # What the IFC says vs what the document says
    ifc_value = models.TextField(
        verbose_name="IFC Value",
        help_text="Value found in the IFC model",
    )
    document_value = models.TextField(
        verbose_name="Document Value",
        help_text="Value found in the document",
    )
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
        verbose_name="Severity",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name="Status",
    )
    # Resolution
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_conflicts",
        verbose_name="Resolved By",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )
    resolution_note = models.TextField(
        blank=True,
        verbose_name="Resolution Note",
    )
    suggested_fix = models.TextField(
        blank=True,
        verbose_name="Suggested Fix",
        help_text="LLM-generated modify prompt to resolve this conflict",
    )
    # LLM confidence score (0.0–1.0) — used for false-positive filtering
    confidence = models.FloatField(
        default=0.0,
        verbose_name="Confidence",
        help_text="LLM confidence in this finding (0.0–1.0)",
    )
    # Property that conflicts (e.g. 'FireRating') — for deduplication and display
    property_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Property Name",
        help_text="IFC property involved in the conflict",
    )
    # Stable deduplication key: SHA-256 of entity.id + chunk.id + property_name
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="Content Hash",
        help_text="Deduplication key — SHA-256(entity_id:chunk_id:property_name)",
    )
    # Scan run that detected this conflict
    scan_run = models.ForeignKey(
        ScanRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conflicts",
        verbose_name="Scan Run",
    )

    class Meta:
        ordering = ["-severity", "-created_at"]
        verbose_name = "Conflict"
        verbose_name_plural = "Conflicts"
        indexes = [
            models.Index(fields=["project", "status", "-severity"]),
            models.Index(fields=["project", "status", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash"],
                condition=models.Q(status="open") & ~models.Q(content_hash=""),
                name="unique_open_conflict_hash",
            )
        ]

    def __str__(self):
        return self.title
