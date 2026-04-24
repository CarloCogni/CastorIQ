# facilities/services/action_request_service.py
"""ActionRequest CRUD + light triage workflow (M3.B).

ActionRequests are the entry point for non-FM users (typically OCCUPANT /
TENANT) to flag an issue. The lifecycle is intentionally simple:

    open → triaged → escalated   (the productive path; escalation creates a WO)
    open → dismissed             (the discard path)

Cross-domain escalation (AR → WorkOrder) lives on
:class:`facilities.services.workorder_service.WorkOrderService.escalate_action_request`,
not here, because the new WO needs to be created by the WO service in the
same transaction. This module owns the AR-side lifecycle only.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q, QuerySet

from environments.models import Project
from facilities.models import ActionRequest

logger = logging.getLogger(__name__)


class ActionRequestServiceError(Exception):
    """Base for AR-service guard violations."""


class ActionRequestValidationError(ActionRequestServiceError):
    """Raised when request input cannot be turned into a valid AR mutation."""


class ActionRequestNotFoundError(ActionRequestServiceError):
    """Raised when an AR lookup misses inside the project scope."""


# Fields the service accepts on create / update. Anything else is rejected.
_CREATE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "affected_asset",
        "affected_spatial",
        "severity",
    }
)
_UPDATE_FIELDS: frozenset[str] = _CREATE_FIELDS | frozenset({"triage_note"})


class ActionRequestService:
    """Facade over :class:`ActionRequest` for the Occupant + FM triage views."""

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # ── Reads ──────────────────────────────────────────────────────────

    def get_action_request(self, pk) -> ActionRequest:
        """Return one AR inside this project, or raise."""
        try:
            return (
                ActionRequest.objects.select_related(
                    "submitted_by", "affected_asset", "affected_spatial"
                )
                .prefetch_related("work_orders")
                .get(pk=pk, project=self.project)
            )
        except ActionRequest.DoesNotExist as exc:
            raise ActionRequestNotFoundError("Action request not found in this project.") from exc

    def list_action_requests(
        self,
        *,
        q: str = "",
        status: str = "",
        severity: str = "",
        submitted_by_id: str | None = None,
        include_closed: bool = True,
    ) -> QuerySet[ActionRequest]:
        """Return the project's ARs filtered by the given parameters."""
        qs = ActionRequest.objects.filter(project=self.project).select_related(
            "submitted_by", "affected_asset", "affected_spatial"
        )

        if not include_closed:
            qs = qs.exclude(
                status__in=(ActionRequest.Status.ESCALATED, ActionRequest.Status.DISMISSED)
            )
        if status:
            qs = qs.filter(status=status)
        if severity:
            qs = qs.filter(severity=severity)
        if submitted_by_id:
            qs = qs.filter(submitted_by_id=submitted_by_id)
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(description__icontains=q) | Q(triage_note__icontains=q)
            )

        return qs.order_by("-created_at")

    # ── Mutations ──────────────────────────────────────────────────────

    def create_action_request(self, **fields: Any) -> ActionRequest:
        """Create an AR. ``title`` is required; submitter defaults to the actor."""
        unknown = set(fields) - _CREATE_FIELDS
        if unknown:
            raise ActionRequestValidationError(f"Unknown fields for create: {sorted(unknown)}")
        if not (fields.get("title") or "").strip():
            raise ActionRequestValidationError("Title is required.")

        ar = ActionRequest.objects.create(
            project=self.project,
            submitted_by=self.user,
            **fields,
        )
        logger.info(
            "ActionRequest created: project=%s ar=%s by=%s",
            self.project.pk,
            ar.pk,
            getattr(self.user, "pk", None),
        )
        return ar

    def update_action_request(self, ar: ActionRequest, **fields: Any) -> ActionRequest:
        """Update mutable fields on an AR.

        Status is not mutable here — use :meth:`triage` / :meth:`dismiss`,
        or escalate via :class:`WorkOrderService.escalate_action_request`.
        """
        if ar.project_id != self.project.id:
            raise ActionRequestValidationError("Action request does not belong to this project.")
        unknown = set(fields) - _UPDATE_FIELDS
        if unknown:
            raise ActionRequestValidationError(f"Unknown fields for update: {sorted(unknown)}")

        for name, value in fields.items():
            setattr(ar, name, value)
        ar.save()
        return ar

    def triage(self, ar: ActionRequest, *, note: str = "") -> ActionRequest:
        """Move an OPEN AR into TRIAGED. Idempotent on TRIAGED."""
        if ar.project_id != self.project.id:
            raise ActionRequestValidationError("Action request does not belong to this project.")
        if ar.status not in (ActionRequest.Status.OPEN, ActionRequest.Status.TRIAGED):
            raise ActionRequestValidationError(
                f"Cannot triage from status {ar.get_status_display()}."
            )
        ar.status = ActionRequest.Status.TRIAGED
        if note:
            ar.triage_note = note
        ar.save(update_fields=["status", "triage_note", "updated_at"])
        logger.info("ActionRequest triaged: project=%s ar=%s", self.project.pk, ar.pk)
        return ar

    def dismiss(self, ar: ActionRequest, *, note: str = "") -> ActionRequest:
        """Move an OPEN/TRIAGED AR into DISMISSED."""
        if ar.project_id != self.project.id:
            raise ActionRequestValidationError("Action request does not belong to this project.")
        if ar.status not in (ActionRequest.Status.OPEN, ActionRequest.Status.TRIAGED):
            raise ActionRequestValidationError(
                f"Cannot dismiss from status {ar.get_status_display()}."
            )
        ar.status = ActionRequest.Status.DISMISSED
        if note:
            ar.triage_note = note
        ar.save(update_fields=["status", "triage_note", "updated_at"])
        logger.info("ActionRequest dismissed: project=%s ar=%s", self.project.pk, ar.pk)
        return ar

    def delete_action_request(self, ar: ActionRequest) -> None:
        """Hard-delete an AR. Mostly used in tests / admin recovery."""
        if ar.project_id != self.project.id:
            raise ActionRequestValidationError("Action request does not belong to this project.")
        ar.delete()
