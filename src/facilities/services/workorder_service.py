# facilities/services/workorder_service.py
"""Business logic for the Work Order lifecycle (M3.B).

Owns transitions, role gates, list/detail/kanban/calendar reads, bulk
actions, attachment + permit linking, and the AR-to-WO escalation. Views
remain thin: they parse request input, call the service, and render.

The state machine is encoded in :data:`_TRANSITIONS` — a single dict keyed
by ``(from_status, transition_name)``. :meth:`_apply_transition` validates
and writes a :class:`WorkOrderStatusEvent`; the side-effect dict gives the
specific transition a place to stamp ``actual_start`` / ``verified_at`` /
``closed_at`` and similar timestamps.

**Per spec §7.3 WO writes do NOT emit FMDelta rows** — only closed-and-
verified WOs surface in IFC export as ``LastMaintenanceDate`` summaries,
and that surfacing is M3+ scope. The :class:`facilities.models.FMDelta`
import is intentionally absent.

The :meth:`_broadcast` method is a no-op stub here. It is wired up in M3.D
(``WorkOrderConsumer``) where transitions fan out to a per-project channel
group.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from environments.models import Project, ProjectRole
from facilities.models import (
    ActionRequest,
    Permit,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderStatus,
    WorkOrderStatusEvent,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ── Errors ────────────────────────────────────────────────────────────────


class WorkOrderServiceError(Exception):
    """Base for work-order service guard violations."""


class WorkOrderValidationError(WorkOrderServiceError):
    """Raised when request input cannot be converted into a mutation."""


class WorkOrderNotFoundError(WorkOrderServiceError):
    """Raised when a work-order lookup misses inside the project scope."""


class IllegalTransitionError(WorkOrderServiceError):
    """Raised when the requested transition is not allowed from the current state."""


class RoleNotAllowedError(WorkOrderServiceError):
    """Raised when the actor's functional role is not authorized for the transition."""


# ── Transition table ──────────────────────────────────────────────────────
#
# Single source of truth for the lifecycle graph. Keyed by
# ``(from_status, transition_name)`` → ``TransitionRule``. Service methods
# resolve into this table; the dispatcher view (M3.C) validates the
# ``transition`` URL kwarg against the keys.

_FM_AND_OWNER = ("facilitiesmanager", "buildingowner")
_ENGINEER_AND_VENDORS = ("maintenanceengineer", "contractor", "subcontractor")


class TransitionRule:
    """Declarative cell in the transition table.

    ``allowed_roles``: tuple of :class:`ProjectRole.Role` values. The empty
    tuple means "any role with project access" (used by ``submit``). The
    sentinel value ``"requester"`` means "the user who requested the WO";
    the sentinel ``"assignee"`` means "the user currently assigned to the
    WO, when the role is one of the engineer/vendor roles".
    """

    __slots__ = ("to_status", "allowed_roles", "requires_assignee_match")

    def __init__(
        self,
        to_status: int,
        allowed_roles: tuple[str, ...],
        requires_assignee_match: bool = False,
    ):
        self.to_status = to_status
        self.allowed_roles = allowed_roles
        self.requires_assignee_match = requires_assignee_match


# Registered transition graph. See plan §3.B for the rendered diagram.
_TRANSITIONS: dict[tuple[int, str], TransitionRule] = {
    # DRAFT
    (WorkOrderStatus.DRAFT, "submit"): TransitionRule(WorkOrderStatus.SUBMITTED, ()),
    # SUBMITTED
    (WorkOrderStatus.SUBMITTED, "triage"): TransitionRule(WorkOrderStatus.TRIAGED, _FM_AND_OWNER),
    (WorkOrderStatus.SUBMITTED, "cancel"): TransitionRule(
        WorkOrderStatus.CANCELLED,
        _FM_AND_OWNER + ("requester",),
    ),
    # TRIAGED
    (WorkOrderStatus.TRIAGED, "assign"): TransitionRule(WorkOrderStatus.ASSIGNED, _FM_AND_OWNER),
    (WorkOrderStatus.TRIAGED, "cancel"): TransitionRule(WorkOrderStatus.CANCELLED, _FM_AND_OWNER),
    # ASSIGNED
    (WorkOrderStatus.ASSIGNED, "schedule"): TransitionRule(
        WorkOrderStatus.SCHEDULED,
        ("facilitiesmanager",) + _ENGINEER_AND_VENDORS,
        requires_assignee_match=True,
    ),
    (WorkOrderStatus.ASSIGNED, "start"): TransitionRule(
        WorkOrderStatus.IN_PROGRESS, _ENGINEER_AND_VENDORS, requires_assignee_match=True
    ),
    (WorkOrderStatus.ASSIGNED, "cancel"): TransitionRule(WorkOrderStatus.CANCELLED, _FM_AND_OWNER),
    # SCHEDULED
    (WorkOrderStatus.SCHEDULED, "start"): TransitionRule(
        WorkOrderStatus.IN_PROGRESS, _ENGINEER_AND_VENDORS, requires_assignee_match=True
    ),
    (WorkOrderStatus.SCHEDULED, "reassign"): TransitionRule(
        WorkOrderStatus.ASSIGNED, _FM_AND_OWNER
    ),
    # IN_PROGRESS
    (WorkOrderStatus.IN_PROGRESS, "complete"): TransitionRule(
        WorkOrderStatus.COMPLETED, _ENGINEER_AND_VENDORS, requires_assignee_match=True
    ),
    (WorkOrderStatus.IN_PROGRESS, "pause"): TransitionRule(
        WorkOrderStatus.ASSIGNED,
        ("facilitiesmanager",) + _ENGINEER_AND_VENDORS,
        requires_assignee_match=False,
    ),
    # COMPLETED
    (WorkOrderStatus.COMPLETED, "verify"): TransitionRule(WorkOrderStatus.VERIFIED, _FM_AND_OWNER),
    (WorkOrderStatus.COMPLETED, "reject"): TransitionRule(WorkOrderStatus.ASSIGNED, _FM_AND_OWNER),
    # VERIFIED
    (WorkOrderStatus.VERIFIED, "close"): TransitionRule(WorkOrderStatus.CLOSED, _FM_AND_OWNER),
    # CLOSED
    (WorkOrderStatus.CLOSED, "reopen"): TransitionRule(
        WorkOrderStatus.ASSIGNED, _FM_AND_OWNER + ("auditor",)
    ),
}


VALID_TRANSITION_NAMES = frozenset({name for _, name in _TRANSITIONS})


# ── Service ───────────────────────────────────────────────────────────────


class WorkOrderService:
    """Facade over :class:`WorkOrder` and its companions.

    Constructor takes ``(project, user)``. Read methods eager-load the
    relations consumed by the list / kanban / calendar templates so views
    stay free of ``select_related`` / ``prefetch_related`` plumbing.
    """

    # Mutable fields per status. Anything not listed here is rejected by
    # ``update_work_order`` to keep the state machine the only path that
    # changes lifecycle-relevant fields.
    _ALWAYS_MUTABLE_FIELDS: frozenset[str] = frozenset(
        {
            "title",
            "description",
            "category",
            "priority",
            "affected_asset",
            "affected_spatial",
            "due_at",
        }
    )
    _DRAFT_EXTRA_FIELDS: frozenset[str] = frozenset({"requested_by"})
    _PRE_START_EXTRA_FIELDS: frozenset[str] = frozenset(
        {"assignee_user", "assignee_vendor", "scheduled_start", "scheduled_end"}
    )

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # ── Reads ──────────────────────────────────────────────────────────

    def get_work_order(self, pk) -> WorkOrder:
        """Return one WO inside this project, or raise ``WorkOrderNotFoundError``."""
        try:
            return (
                WorkOrder.objects.select_related(
                    "project",
                    "affected_asset",
                    "affected_asset__ifc_entity",
                    "affected_spatial",
                    "requested_by",
                    "assignee_user",
                    "verified_by",
                    "source_action_request",
                )
                .prefetch_related("status_events", "attachments", "permits")
                .get(pk=pk, project=self.project)
            )
        except WorkOrder.DoesNotExist as exc:
            raise WorkOrderNotFoundError("Work order not found in this project.") from exc

    def list_work_orders(
        self,
        *,
        q: str = "",
        status: int | None = None,
        statuses: Iterable[int] | None = None,
        priority: int | None = None,
        category: str = "",
        assignee_user_id: str | UUID | None = None,
        assignee_vendor: str = "",
        asset_id: str | UUID | None = None,
        spatial_id: str | UUID | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        scheduled_in_range: tuple[datetime | None, datetime | None] = (None, None),
        overdue_only: bool = False,
        include_closed: bool = False,
    ) -> QuerySet[WorkOrder]:
        """Return the project's WOs filtered by the given parameters.

        Any empty / missing parameter is treated as no filter on that axis.
        ``include_closed`` defaults to False so the kanban + list pages
        exclude CLOSED + CANCELLED rows by default. Returns a queryset so
        callers can paginate.
        """
        qs = (
            WorkOrder.objects.filter(project=self.project)
            .select_related(
                "project",
                "affected_asset",
                "affected_asset__ifc_entity",
                "affected_spatial",
                "requested_by",
                "assignee_user",
            )
            .prefetch_related("permits")
        )

        if not include_closed:
            qs = qs.exclude(status__in=(WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED))

        if status is not None:
            qs = qs.filter(status=status)
        if statuses:
            qs = qs.filter(status__in=tuple(statuses))
        if priority is not None:
            qs = qs.filter(priority=priority)
        if category:
            qs = qs.filter(category=category)
        if assignee_user_id:
            qs = qs.filter(assignee_user_id=assignee_user_id)
        if assignee_vendor:
            qs = qs.filter(assignee_vendor__iexact=assignee_vendor)
        if asset_id:
            qs = qs.filter(affected_asset_id=asset_id)
        if spatial_id:
            qs = qs.filter(affected_spatial_id=spatial_id)
        if due_before:
            qs = qs.filter(due_at__lt=due_before)
        if due_after:
            qs = qs.filter(due_at__gte=due_after)

        start_lo, start_hi = scheduled_in_range
        if start_lo:
            qs = qs.filter(scheduled_start__gte=start_lo)
        if start_hi:
            qs = qs.filter(scheduled_start__lt=start_hi)

        if overdue_only:
            qs = qs.filter(
                due_at__isnull=False,
                due_at__lt=timezone.now(),
                status__lt=WorkOrderStatus.COMPLETED,
            )

        if q:
            qs = qs.filter(
                Q(wo_number__icontains=q)
                | Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(assignee_vendor__icontains=q)
                | Q(affected_asset__asset_tag__icontains=q)
            )

        return qs.order_by("-priority", "due_at", "-created_at")

    def kanban_columns(self, **filters: Any) -> dict[int, list[WorkOrder]]:
        """Group the open WOs by status for the kanban board.

        Returns one list per status in :data:`KANBAN_STATUSES` (the open
        ones), keyed by status integer. CLOSED + CANCELLED are intentionally
        excluded from the board — they belong on the list/calendar views.
        Forces ``include_closed=False``.
        """
        filters["include_closed"] = False
        qs = self.list_work_orders(**filters)
        columns: dict[int, list[WorkOrder]] = {s: [] for s in KANBAN_STATUSES}
        for wo in qs:
            if wo.status in columns:
                columns[wo.status].append(wo)
        return columns

    def calendar_window(
        self,
        *,
        start: datetime,
        end: datetime,
        **filters: Any,
    ) -> QuerySet[WorkOrder]:
        """Return WOs whose ``scheduled_start`` falls inside ``[start, end)``."""
        filters.setdefault("include_closed", True)
        return (
            self.list_work_orders(**filters)
            .filter(scheduled_start__gte=start, scheduled_start__lt=end)
            .order_by("scheduled_start")
        )

    # ── Non-transition mutations ───────────────────────────────────────

    def create_work_order(self, **fields: Any) -> WorkOrder:
        """Create a new WO in DRAFT and write the initial status event.

        Forces the project scope and the requester (defaults to the calling
        user when ``requested_by`` is not supplied). Any field not listed in
        :attr:`_ALWAYS_MUTABLE_FIELDS` ∪ :attr:`_DRAFT_EXTRA_FIELDS` ∪
        :attr:`_PRE_START_EXTRA_FIELDS` raises :class:`WorkOrderValidationError`.
        """
        allowed = (
            self._ALWAYS_MUTABLE_FIELDS | self._DRAFT_EXTRA_FIELDS | self._PRE_START_EXTRA_FIELDS
        )
        unknown = set(fields) - allowed
        if unknown:
            raise WorkOrderValidationError(f"Unknown fields for create: {sorted(unknown)}")
        if not (fields.get("title") or "").strip():
            raise WorkOrderValidationError("Title is required.")

        fields.setdefault("requested_by", self.user)
        with transaction.atomic():
            wo = WorkOrder.objects.create(project=self.project, **fields)
            event = WorkOrderStatusEvent.objects.create(
                work_order=wo,
                from_status=None,
                to_status=WorkOrderStatus.DRAFT,
                actor=self.user,
                actor_role=self._actor_role_snapshot(),
                note="Created",
            )
            self._broadcast(wo, "wo.created", {"event_id": str(event.pk)})
        logger.info("WO created: project=%s wo=%s", self.project.pk, wo.wo_number)
        return wo

    def update_work_order(self, wo: WorkOrder, **fields: Any) -> WorkOrder:
        """Update a WO's mutable fields. Lifecycle-relevant ones are stage-gated.

        ``title`` / ``description`` / ``category`` / ``priority`` /
        ``affected_asset`` / ``affected_spatial`` / ``due_at`` are mutable in
        any pre-terminal stage. ``assignee_*`` / ``scheduled_*`` may only be
        changed before ``IN_PROGRESS``. ``requested_by`` may only be changed
        in ``DRAFT``. Status, timestamps, and provenance fields are NOT
        mutable here — use the transition methods.
        """
        if wo.status in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED):
            raise IllegalTransitionError("Closed / cancelled WOs cannot be edited.")

        allowed = set(self._ALWAYS_MUTABLE_FIELDS)
        if wo.status == WorkOrderStatus.DRAFT:
            allowed |= self._DRAFT_EXTRA_FIELDS
        if wo.status < WorkOrderStatus.IN_PROGRESS:
            allowed |= self._PRE_START_EXTRA_FIELDS

        unknown = set(fields) - allowed
        if unknown:
            raise WorkOrderValidationError(
                f"Fields not editable in status {WorkOrderStatus(wo.status).label}: {sorted(unknown)}"
            )

        for name, value in fields.items():
            setattr(wo, name, value)

        try:
            wo.full_clean(exclude=["wo_number"])
        except Exception as exc:
            raise WorkOrderValidationError(str(exc)) from exc

        wo.save()
        return wo

    def upload_attachment(
        self,
        wo: WorkOrder,
        *,
        file,
        caption: str = "",
        kind: str = WorkOrderAttachment.Kind.PHOTO,
    ) -> WorkOrderAttachment:
        """Attach a file to a WO."""
        return WorkOrderAttachment.objects.create(
            work_order=wo,
            file=file,
            caption=caption,
            kind=kind,
            uploaded_by=self.user,
        )

    def link_permit(self, wo: WorkOrder, permit: Permit) -> None:
        """Link a permit to a WO. No-op if already linked."""
        if permit.project_id != wo.project_id:
            raise WorkOrderValidationError("Permit and WO must belong to the same project.")
        wo.permits.add(permit)

    def unlink_permit(self, wo: WorkOrder, permit: Permit) -> None:
        """Detach a permit from a WO. No-op if not linked."""
        wo.permits.remove(permit)

    # ── Transitions ────────────────────────────────────────────────────

    def submit(self, wo: WorkOrder, *, skip_triage: bool = False, note: str = "") -> WorkOrder:
        """Move DRAFT → SUBMITTED. With ``skip_triage`` and the actor being
        the assignee + a maintenance-engineer-equivalent role, jump straight
        to ASSIGNED — the "engineer reports and self-handles" path."""
        if skip_triage:
            return self._submit_skip_triage(wo, note=note)
        return self._apply_transition(wo, "submit", note=note)

    def triage(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        return self._apply_transition(wo, "triage", note=note)

    def assign(
        self,
        wo: WorkOrder,
        *,
        assignee_user_id: str | UUID | None = None,
        assignee_vendor: str = "",
        note: str = "",
    ) -> WorkOrder:
        """Set the assignee and move TRIAGED → ASSIGNED."""
        if not assignee_user_id and not assignee_vendor:
            raise WorkOrderValidationError("Assign requires an assignee user or vendor.")
        return self._apply_transition(
            wo,
            "assign",
            note=note,
            field_updates={
                "assignee_user_id": assignee_user_id,
                "assignee_vendor": assignee_vendor or "",
            },
        )

    def schedule(
        self,
        wo: WorkOrder,
        *,
        scheduled_start: datetime,
        scheduled_end: datetime | None = None,
        note: str = "",
    ) -> WorkOrder:
        """Stamp the schedule and move ASSIGNED → SCHEDULED."""
        if scheduled_end and scheduled_end < scheduled_start:
            raise WorkOrderValidationError("scheduled_end must be on or after scheduled_start.")
        return self._apply_transition(
            wo,
            "schedule",
            note=note,
            field_updates={
                "scheduled_start": scheduled_start,
                "scheduled_end": scheduled_end,
            },
        )

    def reassign(
        self,
        wo: WorkOrder,
        *,
        assignee_user_id: str | UUID | None = None,
        assignee_vendor: str = "",
        note: str = "",
    ) -> WorkOrder:
        """Move SCHEDULED → ASSIGNED (back-edge) with a new assignee."""
        if not assignee_user_id and not assignee_vendor:
            raise WorkOrderValidationError("Reassign requires an assignee user or vendor.")
        return self._apply_transition(
            wo,
            "reassign",
            note=note,
            field_updates={
                "assignee_user_id": assignee_user_id,
                "assignee_vendor": assignee_vendor or "",
            },
        )

    def start(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Stamp ``actual_start`` and move {ASSIGNED, SCHEDULED} → IN_PROGRESS."""
        return self._apply_transition(
            wo, "start", note=note, field_updates={"actual_start": timezone.now()}
        )

    def pause(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Move IN_PROGRESS → ASSIGNED (status-only "parking lot")."""
        return self._apply_transition(wo, "pause", note=note)

    def complete(
        self,
        wo: WorkOrder,
        *,
        note: str = "",
        actual_end: datetime | None = None,
    ) -> WorkOrder:
        """Stamp ``actual_end`` and move IN_PROGRESS → COMPLETED."""
        return self._apply_transition(
            wo,
            "complete",
            note=note,
            field_updates={"actual_end": actual_end or timezone.now()},
        )

    def verify(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Stamp verifier + verified_at and move COMPLETED → VERIFIED."""
        return self._apply_transition(
            wo,
            "verify",
            note=note,
            field_updates={"verified_at": timezone.now(), "verified_by": self.user},
        )

    def reject(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Move COMPLETED → ASSIGNED (verification rejected)."""
        return self._apply_transition(wo, "reject", note=note)

    def close(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Stamp ``closed_at`` and move VERIFIED → CLOSED."""
        return self._apply_transition(
            wo, "close", note=note, field_updates={"closed_at": timezone.now()}
        )

    def reopen(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Re-open a CLOSED WO back into ASSIGNED."""
        return self._apply_transition(wo, "reopen", note=note, field_updates={"closed_at": None})

    def cancel(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Move {SUBMITTED, TRIAGED, ASSIGNED} → CANCELLED."""
        return self._apply_transition(wo, "cancel", note=note)

    # ── Bulk ───────────────────────────────────────────────────────────

    def bulk_assign(
        self,
        wo_ids: Iterable[str | UUID],
        *,
        assignee_user_id: str | UUID | None = None,
        assignee_vendor: str = "",
    ) -> int:
        """Assign multiple WOs to the same target.

        Per-row failures (illegal transitions, role-gate violations) are
        skipped — the method returns the count of successful assignments.
        """
        if not assignee_user_id and not assignee_vendor:
            raise WorkOrderValidationError("Bulk assign requires an assignee user or vendor.")
        return self._bulk_iter(
            wo_ids,
            self.assign,
            assignee_user_id=assignee_user_id,
            assignee_vendor=assignee_vendor or "",
        )

    def bulk_set_priority(self, wo_ids: Iterable[str | UUID], *, priority: int) -> int:
        """Update priority on multiple WOs without changing status."""
        return self._bulk_iter(wo_ids, self.update_work_order, priority=priority)

    def bulk_schedule(
        self,
        wo_ids: Iterable[str | UUID],
        *,
        scheduled_start: datetime,
        scheduled_end: datetime | None = None,
    ) -> int:
        """Schedule multiple WOs to the same time window."""
        return self._bulk_iter(
            wo_ids,
            self.schedule,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
        )

    def bulk_cancel(self, wo_ids: Iterable[str | UUID], *, note: str = "") -> int:
        """Cancel multiple WOs from any pre-ASSIGNED stage."""
        return self._bulk_iter(wo_ids, self.cancel, note=note)

    # ── Action Requests (cross-domain mutation lives here, not on AR svc) ──

    def escalate_action_request(self, ar: ActionRequest, **wo_fields: Any) -> WorkOrder:
        """Promote an :class:`ActionRequest` into a fresh :class:`WorkOrder`.

        Sets ``WorkOrder.source_action_request`` for provenance, copies the
        AR title/description/asset/space onto the WO when not overridden,
        and moves the AR to ``escalated`` in the same transaction.
        """
        if ar.project_id != self.project.id:
            raise WorkOrderValidationError("ActionRequest does not belong to this project.")
        if ar.status not in (ActionRequest.Status.OPEN, ActionRequest.Status.TRIAGED):
            raise WorkOrderValidationError(
                f"ActionRequest is in status {ar.get_status_display()} and cannot be escalated."
            )

        wo_fields.setdefault("title", ar.title)
        wo_fields.setdefault("description", ar.description)
        if ar.affected_asset_id:
            wo_fields.setdefault("affected_asset", ar.affected_asset)
        if ar.affected_spatial_id:
            wo_fields.setdefault("affected_spatial", ar.affected_spatial)

        with transaction.atomic():
            wo = self.create_work_order(**wo_fields)
            wo.source_action_request = ar
            wo.save(update_fields=["source_action_request", "updated_at"])
            ar.status = ActionRequest.Status.ESCALATED
            ar.save(update_fields=["status", "updated_at"])
        logger.info(
            "Action request escalated: project=%s ar=%s wo=%s",
            self.project.pk,
            ar.pk,
            wo.wo_number,
        )
        # M4.C — push the AR's status update to its submitter's portal page
        # so the occupant sees "your request became WO-00042" without a
        # refresh. AR svc owns the broadcast contract; we call into it.
        from facilities.services.action_request_service import ActionRequestService

        try:
            ActionRequestService(self.project, self.user)._broadcast(ar, "escalated")
        except Exception as exc:  # noqa: BLE001 — broadcasts must never block escalation
            logger.warning("AR escalation broadcast failed: ar=%s err=%s", ar.pk, exc)
        return wo

    # ── Internals ──────────────────────────────────────────────────────

    def _apply_transition(
        self,
        wo: WorkOrder,
        name: str,
        *,
        note: str = "",
        field_updates: dict[str, Any] | None = None,
    ) -> WorkOrder:
        """Validate + execute a single transition atomically.

        Three checks: (1) the transition is registered for the current
        ``from_status``, (2) the actor's active role is in the rule's
        ``allowed_roles``, and (3) when the rule requires assignee match,
        the actor is the WO's assignee.
        """
        if wo.project_id != self.project.id:
            raise WorkOrderValidationError("Work order does not belong to this project.")

        rule = _TRANSITIONS.get((wo.status, name))
        if rule is None:
            raise IllegalTransitionError(
                f"Cannot {name!r} from status {WorkOrderStatus(wo.status).label}."
            )

        actor_role = self._actor_role_snapshot()
        if rule.allowed_roles and not self._is_allowed(rule, wo, actor_role):
            raise RoleNotAllowedError(
                f"Role {actor_role or '(none)'} is not allowed to {name!r} this WO."
            )

        previous_status = wo.status
        with transaction.atomic():
            wo.status = rule.to_status
            if field_updates:
                for field, value in field_updates.items():
                    setattr(wo, field, value)
            wo.save()
            event = WorkOrderStatusEvent.objects.create(
                work_order=wo,
                from_status=previous_status,
                to_status=rule.to_status,
                actor=self.user,
                actor_role=actor_role,
                note=note,
            )
            self._broadcast(
                wo,
                "wo.transitioned",
                {
                    "from_status": int(previous_status),
                    "to_status": int(rule.to_status),
                    "transition": name,
                    "event_id": str(event.pk),
                },
            )
        logger.info(
            "WO transition: project=%s wo=%s %s → %s (%s)",
            self.project.pk,
            wo.wo_number,
            WorkOrderStatus(previous_status).label,
            WorkOrderStatus(rule.to_status).label,
            name,
        )
        return wo

    def _submit_skip_triage(self, wo: WorkOrder, *, note: str = "") -> WorkOrder:
        """Engineer-self-reports path: DRAFT → ASSIGNED in one shot.

        Allowed only when the actor's active role is in the engineer/vendor
        bucket AND the actor is already the WO's assignee. Avoids the
        absurdity of an engineer noticing a problem and waiting for FM to
        triage their own WO.
        """
        actor_role = self._actor_role_snapshot()
        if actor_role not in _ENGINEER_AND_VENDORS:
            raise RoleNotAllowedError(
                "skip_triage is only available to maintenance engineers / contractors."
            )
        if wo.assignee_user_id != getattr(self.user, "id", None):
            raise RoleNotAllowedError(
                "skip_triage requires the actor to be the assignee on the WO."
            )
        if wo.status != WorkOrderStatus.DRAFT:
            raise IllegalTransitionError("skip_triage only applies from DRAFT.")

        previous_status = wo.status
        with transaction.atomic():
            wo.status = WorkOrderStatus.ASSIGNED
            wo.save(update_fields=["status", "updated_at"])
            event = WorkOrderStatusEvent.objects.create(
                work_order=wo,
                from_status=previous_status,
                to_status=WorkOrderStatus.ASSIGNED,
                actor=self.user,
                actor_role=actor_role,
                note=note or "Engineer self-report — triage skipped",
            )
            self._broadcast(
                wo,
                "wo.transitioned",
                {
                    "from_status": int(previous_status),
                    "to_status": int(WorkOrderStatus.ASSIGNED),
                    "transition": "submit_skip_triage",
                    "event_id": str(event.pk),
                },
            )
        return wo

    def _is_allowed(self, rule: TransitionRule, wo: WorkOrder, actor_role: str) -> bool:
        """Check the rule's allowed_roles against the actor + assignee context.

        Sentinels:
        - ``"requester"`` → actor is the original requester.
        - ``"assignee"`` → actor is the current assignee_user.
        Real role strings are matched literally against ``actor_role``.
        """
        actor_id = getattr(self.user, "id", None)

        for token in rule.allowed_roles:
            if token == "requester":
                if actor_id and actor_id == wo.requested_by_id:
                    return True
            elif token == "assignee":
                if actor_id and actor_id == wo.assignee_user_id:
                    return True
            elif token == actor_role:
                if rule.requires_assignee_match and token in _ENGINEER_AND_VENDORS:
                    if actor_id and actor_id == wo.assignee_user_id:
                        return True
                else:
                    return True
        return False

    def _actor_role_snapshot(self) -> str:
        """Return the actor's active functional role string for audit logging.

        Reads the most recently valid :class:`ProjectRole` for this user on
        this project. If the user has none but is the project owner, returns
        the implicit ``"buildingowner"`` token (mirrors the implicit-owner
        fallback in ``views._role_context``). Returns ``""`` for users with
        neither — they will fail role gates anyway, the empty string just
        keeps the log readable.
        """
        if not getattr(self.user, "is_authenticated", False):
            return ""
        role = ProjectRole.active_for(self.user, self.project).order_by("-valid_from").first()
        if role:
            return role.role
        if self.project.owner_id == getattr(self.user, "id", None):
            return ProjectRole.Role.BUILDINGOWNER
        return ""

    def _bulk_iter(
        self,
        wo_ids: Iterable[str | UUID],
        action,
        **kwargs: Any,
    ) -> int:
        """Run ``action(wo, **kwargs)`` for each id, swallowing per-row errors.

        Each WO is loaded + mutated in its own savepoint so a single-row
        failure does not abort the batch. Logged at WARNING for visibility.
        """
        success = 0
        for raw_id in wo_ids:
            try:
                wo = self.get_work_order(raw_id)
            except WorkOrderNotFoundError:
                logger.warning(
                    "Bulk %s: WO %s not found in project %s",
                    action.__name__,
                    raw_id,
                    self.project.pk,
                )
                continue
            try:
                with transaction.atomic():
                    action(wo, **kwargs)
                success += 1
            except WorkOrderServiceError as exc:
                logger.warning("Bulk %s: WO %s skipped (%s)", action.__name__, wo.wo_number, exc)
        return success

    def _broadcast(self, wo: WorkOrder, event_type: str, payload: dict) -> None:
        """Fan transition events out to the project's WS group.

        Pre-renders the kanban-card HTML once on the server side so 200
        connected clients don't all HTMX-refetch the same card. The payload
        carries ``html`` plus the from / to status values so the client can
        decide which kanban column to move the card into.

        Channel layer absent (e.g. test settings without InMemory layer
        configured) → silent no-op. Render failure → logged at WARNING and
        the transition is NOT rolled back, since the live update is best-effort.
        """
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from django.template.loader import render_to_string

        layer = get_channel_layer()
        if layer is None:
            return
        try:
            rendered = render_to_string(
                "facilities/components/work_kanban_card.html",
                {"wo": wo, "project": self.project},
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("WO broadcast: template render failed for wo=%s", wo.pk, exc_info=True)
            return

        from facilities.consumers import fm_wo_group_name

        try:
            async_to_sync(layer.group_send)(
                fm_wo_group_name(wo.project_id),
                {
                    "type": "wo_updated",
                    "payload": {
                        "wo_id": str(wo.pk),
                        "wo_number": wo.wo_number,
                        "event": event_type,
                        "from_status": payload.get("from_status"),
                        "to_status": int(wo.status),
                        "html": rendered,
                    },
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("WO broadcast: group_send failed for wo=%s", wo.pk, exc_info=True)


# Open lifecycle stages rendered as kanban columns. CLOSED + CANCELLED stay
# off the board — they belong on the list view + history.
KANBAN_STATUSES: tuple[int, ...] = (
    WorkOrderStatus.DRAFT,
    WorkOrderStatus.SUBMITTED,
    WorkOrderStatus.TRIAGED,
    WorkOrderStatus.ASSIGNED,
    WorkOrderStatus.SCHEDULED,
    WorkOrderStatus.IN_PROGRESS,
    WorkOrderStatus.COMPLETED,
    WorkOrderStatus.VERIFIED,
)
