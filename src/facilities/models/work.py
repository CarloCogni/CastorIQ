# facilities/models/work.py
"""Work-order domain models for 7D Facility Management (M3).

This module introduces the CMMS core — the lifecycle from "noticed something
wrong" to "signed off" without leaving Castor. Five models cooperate:

``ActionRequest``
    A lightweight report from any role with project access (typically
    OCCUPANT / TENANT). Carries the bare minimum to triage: title, description,
    optional asset / spatial anchor, severity. May be escalated to a
    :class:`WorkOrder` (the WO carries ``source_action_request`` back as
    provenance).

``WorkOrder``
    The hero entity. A unit of operational work tied to a physical asset or
    space, with a lifecycle of its own. Status flows through ten states
    (``DRAFT`` → … → ``CLOSED`` plus ``CANCELLED`` as a parallel terminal).
    Transitions are enforced by :class:`facilities.services.workorder_service.WorkOrderService`,
    not by the model itself — the model is a passive data carrier.

``WorkOrderStatusEvent``
    Append-only audit log. One row per transition; immutable once created.
    Captures actor + actor's functional role at transition time so the trail
    survives later role changes.

``WorkOrderAttachment``
    Photos and documents attached to a WO (before / during / after, sign-off
    sheets, exception evidence). FileField-only in M3 — linking to the future
    Documents app (M6) is out of scope.

``Permit``
    A regulatory or safety instrument required by one or more WOs (hot-work,
    confined-space, electrical isolation, working-at-height, …). Lives as a
    project-scoped peer to WOs with an M2M relationship. Expiry is tracked
    via ``valid_until``; the dashboard surfaces anything within 90 days.

All models use :class:`core.models.UUIDModel` (UUID PK + created/updated
timestamps via :class:`core.models.TimestampedModel`).

Per the M3 spec (and `docs/specs/7D-facility-management.md` §7.3) WO data is
**DB-only** in M3 — no ``FMDelta`` rows are emitted on creation or transition.
Only closed-and-verified WOs surface in IFC export as ``LastMaintenanceDate``
summaries, and that surfacing is M3+ scope.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.models import IFCSpatialElement

from .assets import FacilityAsset


class ImmutableError(Exception):
    """Raised by :class:`WorkOrderStatusEvent.save` when an existing row is updated.

    Status events are append-only by design. The service layer is trusted to
    take the right path (insert, never update); this exception is the noisy
    backstop for bugs that try to mutate history.
    """


# ── Action Requests ────────────────────────────────────────────────────────


class ActionRequest(UUIDModel):
    """Lightweight issue report — typically from an occupant or tenant.

    A request is the entry point for non-FM users to report something. The FM
    triages it, optionally enriching the description, and either dismisses it
    or escalates it to a :class:`WorkOrder`. When escalated, the resulting WO
    carries a back-reference via ``WorkOrder.source_action_request`` and the
    request status moves to ``escalated``.
    """

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        TRIAGED = "triaged", "Triaged"
        ESCALATED = "escalated", "Escalated"
        DISMISSED = "dismissed", "Dismissed"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="action_requests",
        verbose_name="Project",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_action_requests",
        verbose_name="Submitted By",
    )
    title = models.CharField(
        max_length=255,
        verbose_name="Title",
        help_text="Short summary, e.g. 'Meeting room 3-B is cold'.",
    )
    description = models.TextField(blank=True, verbose_name="Description")

    affected_asset = models.ForeignKey(
        FacilityAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_requests",
        verbose_name="Affected Asset",
    )
    affected_spatial = models.ForeignKey(
        IFCSpatialElement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_requests",
        verbose_name="Affected Space",
    )

    severity = models.CharField(
        max_length=8,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
        verbose_name="Severity",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name="Status",
    )
    triage_note = models.TextField(
        blank=True,
        verbose_name="Triage Note",
        help_text="FM-side notes from triage / dismissal.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Action Request"
        verbose_name_plural = "Action Requests"
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["project", "severity"]),
        ]

    def __str__(self) -> str:
        return f"AR: {self.title[:60]}"

    @property
    def is_open(self) -> bool:
        """True while the request still needs FM attention."""
        return self.status == self.Status.OPEN


# ── Work Orders ────────────────────────────────────────────────────────────


class WorkOrderStatus(models.IntegerChoices):
    """Lifecycle stages for a :class:`WorkOrder`.

    Values are spaced (``0, 10, 20, …``) so future intermediate stages can
    slot in without reshuffling. Ordinal order matters for ``status__lt`` /
    ``status__gte`` queries (e.g. ``status__lt=COMPLETED`` = "still open").

    ``CANCELLED`` (90) is a parallel terminal alongside ``CLOSED`` (80) — not
    in the spec's nine-stage list but real-world unavoidable. A WO that was
    raised in error and cancelled before any work happened is functionally
    different from one that was completed and closed.
    """

    DRAFT = 0, "Draft"
    SUBMITTED = 10, "Submitted"
    TRIAGED = 20, "Triaged"
    ASSIGNED = 30, "Assigned"
    SCHEDULED = 40, "Scheduled"
    IN_PROGRESS = 50, "In progress"
    COMPLETED = 60, "Completed"
    VERIFIED = 70, "Verified"
    CLOSED = 80, "Closed"
    CANCELLED = 90, "Cancelled"


class WorkOrderCategory(models.TextChoices):
    """High-level work-order classification."""

    CORRECTIVE = "corrective", "Corrective"
    PREVENTIVE = "preventive", "Preventive"
    INSPECTION = "inspection", "Inspection"
    INSTALLATION = "installation", "Installation"
    DECOMMISSION = "decommission", "Decommission"


class WorkOrderPriority(models.IntegerChoices):
    """Priority bands. 1 = most urgent."""

    P1 = 1, "1 — Critical"
    P2 = 2, "2 — High"
    P3 = 3, "3 — Normal"
    P4 = 4, "4 — Low"
    P5 = 5, "5 — Backlog"


class WorkOrder(UUIDModel):
    """A single unit of operational work in the CMMS lifecycle.

    A WO ties together the *what* (asset / space), the *who* (requester +
    assignee, role-gated), the *when* (scheduled vs. actual + due date), and
    the *how it went* (status timeline, attachments, linked permits). The
    nine-stage lifecycle is enforced by
    :class:`facilities.services.workorder_service.WorkOrderService`; the model
    itself is a passive carrier.

    **SLA clock.** V1 only stores ``due_at`` and computes ``is_overdue``
    against it. No pause/resume tracking — that's a future-milestone concern.

    **Asset / space anchor.** Two nullable FKs (``affected_asset`` +
    ``affected_spatial``). At least one is expected in practice but neither
    is required at the schema level — a freshly drafted WO may have neither
    while the FM still gathers detail. Validation lives on the service.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="work_orders",
        verbose_name="Project",
    )
    wo_number = models.CharField(
        max_length=32,
        verbose_name="Work Order Number",
        help_text="Human-readable code, auto-generated as WO-NNNNN per project.",
    )
    title = models.CharField(
        max_length=255,
        verbose_name="Title",
    )
    description = models.TextField(blank=True, verbose_name="Description")

    affected_asset = models.ForeignKey(
        FacilityAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name="Affected Asset",
    )
    affected_spatial = models.ForeignKey(
        IFCSpatialElement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name="Affected Space",
        help_text="Space-level anchor when no specific asset applies.",
    )

    priority = models.PositiveSmallIntegerField(
        choices=WorkOrderPriority.choices,
        default=WorkOrderPriority.P3,
        db_index=True,
        verbose_name="Priority",
    )
    category = models.CharField(
        max_length=32,
        choices=WorkOrderCategory.choices,
        default=WorkOrderCategory.CORRECTIVE,
        db_index=True,
        verbose_name="Category",
    )
    status = models.PositiveSmallIntegerField(
        choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.DRAFT,
        db_index=True,
        verbose_name="Status",
    )

    # People
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_work_orders",
        verbose_name="Requested By",
    )
    assignee_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_orders",
        verbose_name="Assignee (User)",
    )
    assignee_vendor = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Assignee (Vendor)",
        help_text="Free-text vendor name in V1 — becomes a Vendor FK in a later milestone.",
    )

    # Schedule
    scheduled_start = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Scheduled Start",
    )
    scheduled_end = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Scheduled End",
    )
    actual_start = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Actual Start",
        help_text="Stamped when the WO transitions to In Progress.",
    )
    actual_end = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Actual End",
        help_text="Stamped when the WO transitions to Completed.",
    )
    due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Due At",
    )

    # Verification / closure
    verified_at = models.DateTimeField(null=True, blank=True, verbose_name="Verified At")
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_work_orders",
        verbose_name="Verified By",
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Closed At")

    # Provenance
    source_action_request = models.ForeignKey(
        ActionRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name="Source Action Request",
        help_text="The AR this WO was escalated from (when applicable).",
    )
    source_intent_batch_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Source Intent Batch",
        help_text=(
            "When set, groups WOs that were created together by an Intent-to-WO batch. "
            "Mirrors the FMIntentProposal primary key (M3.E)."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Work Order"
        verbose_name_plural = "Work Orders"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "wo_number"],
                name="uniq_work_order_number_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "due_at"]),
            models.Index(fields=["project", "scheduled_start"]),
            models.Index(fields=["project", "assignee_user", "status"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.wo_number}: {self.title[:60]}"

    def save(self, *args, **kwargs) -> None:
        """Auto-assign ``wo_number`` on first save inside a transaction.

        Generates ``WO-NNNNN`` numbered per project. The unique constraint
        backstops races; the service layer can retry on ``IntegrityError`` if
        two creates collide on the same project.
        """
        if not self.wo_number:
            with transaction.atomic():
                # Sequence by counting existing WOs in the project. Acceptable
                # for V1 traffic; can promote to a sequence table later.
                last = (
                    WorkOrder.objects.select_for_update()
                    .filter(project=self.project)
                    .order_by("-wo_number")
                    .first()
                )
                next_seq = self._next_seq_from(last.wo_number if last else None)
                self.wo_number = f"WO-{next_seq:05d}"
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @staticmethod
    def _next_seq_from(prev: str | None) -> int:
        """Parse ``WO-NNNNN`` and return the next sequence number.

        Accepts ``None`` (no prior WO) → 1. Tolerates oddly-shaped legacy
        numbers (manual imports, future PRs) by falling back to scanning the
        digit suffix and returning ``1`` when no digits are present.
        """
        if not prev:
            return 1
        digits = "".join(ch for ch in prev if ch.isdigit())
        if not digits:
            return 1
        return int(digits) + 1

    @property
    def is_open(self) -> bool:
        """True while the WO has not reached a terminal state."""
        return self.status not in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED)

    @property
    def is_overdue(self) -> bool:
        """True when ``due_at`` has passed and the WO is not yet completed."""
        if not self.due_at:
            return False
        if self.status >= WorkOrderStatus.COMPLETED:
            return False
        return timezone.now() > self.due_at

    def clean(self) -> None:
        """Light shape validation; service-layer guards do the heavy lifting."""
        super().clean()
        if (
            self.scheduled_start
            and self.scheduled_end
            and self.scheduled_end < self.scheduled_start
        ):
            raise ValidationError(
                {"scheduled_end": "Scheduled end must be on or after scheduled start."}
            )


class WorkOrderStatusEvent(UUIDModel):
    """One row per WO state transition. Append-only audit trail.

    Captures the actor and a snapshot of their functional role at transition
    time so the trail remains coherent even if the actor's role assignments
    change later. ``from_status`` is null for the initial create event.
    """

    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="status_events",
        verbose_name="Work Order",
    )
    from_status = models.PositiveSmallIntegerField(
        choices=WorkOrderStatus.choices,
        null=True,
        blank=True,
        verbose_name="From Status",
        help_text="Null on the initial creation event.",
    )
    to_status = models.PositiveSmallIntegerField(
        choices=WorkOrderStatus.choices,
        verbose_name="To Status",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_status_events",
        verbose_name="Actor",
    )
    actor_role = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Actor Role",
        help_text="ProjectRole.role snapshot at transition time (e.g. 'facilitiesmanager').",
    )
    note = models.TextField(blank=True, verbose_name="Note")

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Work Order Status Event"
        verbose_name_plural = "Work Order Status Events"
        indexes = [
            models.Index(fields=["work_order", "created_at"]),
        ]

    def __str__(self) -> str:
        from_label = (
            WorkOrderStatus(self.from_status).label if self.from_status is not None else "—"
        )
        to_label = WorkOrderStatus(self.to_status).label
        return f"{self.work_order.wo_number}: {from_label} → {to_label}"

    def save(self, *args, **kwargs) -> None:
        """Reject updates — status events are immutable once created."""
        if self.pk and WorkOrderStatusEvent.objects.filter(pk=self.pk).exists():
            raise ImmutableError(
                "WorkOrderStatusEvent rows are append-only and cannot be modified."
            )
        super().save(*args, **kwargs)


class WorkOrderAttachment(UUIDModel):
    """A photo, document, or sign-off file attached to a :class:`WorkOrder`.

    File-only in M3 — linkage to the M6 Documents app is out of scope.
    """

    class Kind(models.TextChoices):
        PHOTO = "photo", "Photo"
        DOCUMENT = "document", "Document"
        SIGNOFF = "signoff", "Sign-off"

    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="Work Order",
    )
    file = models.FileField(
        upload_to="fm/work_orders/%Y/%m/",
        verbose_name="File",
    )
    caption = models.CharField(max_length=255, blank=True, verbose_name="Caption")
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.PHOTO,
        verbose_name="Kind",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_work_order_attachments",
        verbose_name="Uploaded By",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Work Order Attachment"
        verbose_name_plural = "Work Order Attachments"
        indexes = [
            models.Index(fields=["work_order", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.caption or f"{self.kind} on {self.work_order.wo_number}"


# ── Permits ────────────────────────────────────────────────────────────────


class Permit(UUIDModel):
    """A regulatory or safety permit required by one or more work orders.

    Permits are project-scoped peers to WOs (linked via M2M) — a hot-work
    permit might cover three roof-deck WOs scheduled the same week. Expiry is
    tracked via ``valid_until`` and surfaced on the dashboard via
    :attr:`is_expiring_soon`.
    """

    class Kind(models.TextChoices):
        HOT_WORK = "hot_work", "Hot work"
        CONFINED_SPACE = "confined_space", "Confined space"
        ELECTRICAL = "electrical", "Electrical isolation"
        WORKING_AT_HEIGHT = "working_at_height", "Working at height"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="permits",
        verbose_name="Project",
    )
    permit_number = models.CharField(
        max_length=64,
        verbose_name="Permit Number",
        help_text="Issuing authority's reference (free text). Unique per project.",
    )
    kind = models.CharField(
        max_length=32,
        choices=Kind.choices,
        default=Kind.OTHER,
        db_index=True,
        verbose_name="Kind",
    )
    title = models.CharField(max_length=255, verbose_name="Title")
    issued_to = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Issued To",
        help_text="The contractor / responsible party named on the permit.",
    )
    valid_from = models.DateTimeField(null=True, blank=True, verbose_name="Valid From")
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Valid Until",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")

    work_orders = models.ManyToManyField(
        WorkOrder,
        blank=True,
        related_name="permits",
        verbose_name="Work Orders",
    )

    class Meta:
        ordering = ["-valid_until", "-created_at"]
        verbose_name = "Permit"
        verbose_name_plural = "Permits"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "permit_number"],
                name="uniq_permit_number_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "valid_until"]),
            models.Index(fields=["project", "kind"]),
        ]

    def __str__(self) -> str:
        return f"{self.permit_number} — {self.title[:60]}"

    @property
    def is_expiring_soon(self) -> bool:
        """True when expiry is within the next 90 days (and not yet expired)."""
        if not self.valid_until:
            return False
        delta = self.valid_until - timezone.now()
        return 0 < delta.days <= 90

    @property
    def is_expired(self) -> bool:
        """True when ``valid_until`` is in the past."""
        if not self.valid_until:
            return False
        return timezone.now() > self.valid_until


# ── Intent-to-WO (M3.E) ────────────────────────────────────────────────────


class FMIntentProposal(UUIDModel):
    """Audit trail for an LLM-generated batch of work-order drafts.

    The user types one sentence ("schedule filter replacement on all rooftop
    AHUs next Tuesday, ACME HVAC, priority 2"); the LLM expands it into a
    list of WO drafts; the user reviews and confirms; the service materializes
    each draft as a real WO with full transition history. This row records
    the input + LLM output + the WOs created so an auditor can later trace
    why eight WOs landed on the kanban from a single sentence.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="fm_intent_proposals",
        verbose_name="Project",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fm_intent_proposals",
        verbose_name="User",
    )
    user_message = models.TextField(verbose_name="User Message")
    plan_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Plan JSON",
        help_text="Parsed LLM output: {explanation, confidence, work_orders: [...]}.",
    )
    confidence = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Confidence",
        help_text="0–100. The planner's self-rating.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name="Applied At")
    created_wo_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Created WO IDs",
        help_text="UUID strings of the WorkOrder rows materialized by confirm().",
    )
    error = models.TextField(
        blank=True,
        verbose_name="Error",
        help_text="Failure reason — LLM timeout, parse failure, or apply error.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "FM Intent Proposal"
        verbose_name_plural = "FM Intent Proposals"
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"Intent {self.pk.hex[:8]}: {self.user_message[:60]}"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def work_order_drafts(self) -> list[dict]:
        """The ``work_orders`` array from the parsed LLM output (or empty list)."""
        return list((self.plan_json or {}).get("work_orders") or [])
