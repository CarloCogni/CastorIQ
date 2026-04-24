# facilities/services/permit_service.py
"""Permit CRUD for 7D Facility Management (M3.B).

Permits are project-scoped peers to :class:`facilities.models.WorkOrder`,
linked via M2M. The service mirrors :class:`facilities.services.AssetService`
shape — constructor takes ``(project, user)``, public methods return typed
results or raise domain-specific exceptions.

In M3 the permit lifecycle is light: ``draft`` → ``active`` → ``expired`` /
``revoked``. Expiry tracking is property-driven (``Permit.is_expiring_soon``,
90-day window); a future maintenance milestone can promote it into a richer
state machine when actual workflows demand it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from environments.models import Project
from facilities.models import Permit

logger = logging.getLogger(__name__)


class PermitServiceError(Exception):
    """Base for permit-service guard violations."""


class PermitValidationError(PermitServiceError):
    """Raised when request input cannot be turned into a valid permit mutation."""


class PermitNotFoundError(PermitServiceError):
    """Raised when a permit lookup misses inside the project scope."""


# Fields the service accepts on create / update. Anything else is rejected.
_MUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "permit_number",
        "kind",
        "title",
        "issued_to",
        "valid_from",
        "valid_until",
        "status",
        "notes",
    }
)


class PermitService:
    """Facade over :class:`Permit` for the 7D Facilities Permits page."""

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # ── Reads ──────────────────────────────────────────────────────────

    def get_permit(self, pk) -> Permit:
        """Return a single permit inside this project, or raise."""
        try:
            return Permit.objects.prefetch_related("work_orders").get(pk=pk, project=self.project)
        except Permit.DoesNotExist as exc:
            raise PermitNotFoundError("Permit not found in this project.") from exc

    def list_permits(
        self,
        *,
        q: str = "",
        kind: str = "",
        status: str = "",
        expiring_within_days: int | None = None,
        include_revoked: bool = True,
    ) -> QuerySet[Permit]:
        """Return the project's permits filtered by the given parameters."""
        qs = Permit.objects.filter(project=self.project).prefetch_related("work_orders")

        if not include_revoked:
            qs = qs.exclude(status=Permit.Status.REVOKED)

        if kind:
            qs = qs.filter(kind=kind)
        if status:
            qs = qs.filter(status=status)
        if expiring_within_days is not None and expiring_within_days >= 0:
            now = timezone.now()
            from datetime import timedelta

            qs = qs.filter(
                valid_until__isnull=False,
                valid_until__gte=now,
                valid_until__lte=now + timedelta(days=expiring_within_days),
            )
        if q:
            qs = qs.filter(
                Q(permit_number__icontains=q)
                | Q(title__icontains=q)
                | Q(issued_to__icontains=q)
                | Q(notes__icontains=q)
            )

        return qs.order_by("-valid_until", "-created_at")

    # ── Mutations ──────────────────────────────────────────────────────

    def create_permit(self, **fields: Any) -> Permit:
        """Create a permit. ``permit_number`` and ``title`` are required."""
        unknown = set(fields) - _MUTABLE_FIELDS
        if unknown:
            raise PermitValidationError(f"Unknown fields for create: {sorted(unknown)}")
        if not (fields.get("permit_number") or "").strip():
            raise PermitValidationError("permit_number is required.")
        if not (fields.get("title") or "").strip():
            raise PermitValidationError("title is required.")

        permit = Permit.objects.create(project=self.project, **fields)
        logger.info(
            "Permit created: project=%s permit=%s",
            self.project.pk,
            permit.permit_number,
        )
        return permit

    def update_permit(self, permit: Permit, **fields: Any) -> Permit:
        """Update mutable fields on a permit."""
        if permit.project_id != self.project.id:
            raise PermitValidationError("Permit does not belong to this project.")
        unknown = set(fields) - _MUTABLE_FIELDS
        if unknown:
            raise PermitValidationError(f"Unknown fields for update: {sorted(unknown)}")

        for name, value in fields.items():
            setattr(permit, name, value)
        permit.save()
        return permit

    def revoke_permit(self, permit: Permit, *, note: str = "") -> Permit:
        """Mark a permit as revoked. Idempotent."""
        if permit.project_id != self.project.id:
            raise PermitValidationError("Permit does not belong to this project.")
        if permit.status == Permit.Status.REVOKED:
            return permit
        permit.status = Permit.Status.REVOKED
        if note:
            permit.notes = (
                f"{permit.notes}\n[Revoked {timezone.now():%Y-%m-%d}] {note}"
                if permit.notes
                else f"[Revoked {timezone.now():%Y-%m-%d}] {note}"
            )
        permit.save()
        logger.info(
            "Permit revoked: project=%s permit=%s by=%s",
            self.project.pk,
            permit.permit_number,
            getattr(self.user, "pk", None),
        )
        return permit

    def delete_permit(self, permit: Permit) -> None:
        """Hard-delete a permit. Mainly used by tests / admin recovery."""
        if permit.project_id != self.project.id:
            raise PermitValidationError("Permit does not belong to this project.")
        permit.delete()


# Convenience: parsers that return the parsed value or None — kept here so
# views don't reinvent date parsing in three places.


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string. Returns None for blank input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise PermitValidationError(f"Invalid datetime: {value!r}") from exc
