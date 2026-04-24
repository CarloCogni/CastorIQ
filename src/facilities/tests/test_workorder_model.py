# facilities/tests/test_workorder_model.py
"""Tests for the work-order models in ``facilities/models/work.py``.

These cover the data layer only — the state machine, role gates, and
broadcast hook live on :class:`WorkOrderService` (M3.B). What is asserted
here is shape: auto-numbering, uniqueness, immutability of status events,
and the ``is_overdue`` / ``is_expiring_soon`` properties.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from environments.tests.factories import ProjectFactory
from facilities.models import (
    ActionRequest,
    ImmutableError,
    Permit,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderStatus,
    WorkOrderStatusEvent,
)
from facilities.tests.factories import (
    ActionRequestFactory,
    PermitFactory,
    WorkOrderAttachmentFactory,
    WorkOrderFactory,
    WorkOrderStatusEventFactory,
)

# ── ActionRequest ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestActionRequest:
    """ActionRequest is the simplest of the M3 models — a thin reporting shell."""

    def test_str_truncates_to_60_chars(self):
        ar = ActionRequestFactory(title="x" * 80)
        assert str(ar).startswith("AR: ")
        assert len(str(ar)) <= len("AR: ") + 60

    def test_default_status_is_open(self):
        ar = ActionRequestFactory()
        assert ar.status == ActionRequest.Status.OPEN
        assert ar.is_open is True

    def test_is_open_false_after_dismissal(self):
        ar = ActionRequestFactory(status=ActionRequest.Status.DISMISSED)
        assert ar.is_open is False


# ── WorkOrder numbering and uniqueness ────────────────────────────────────


@pytest.mark.django_db
class TestWorkOrderNumbering:
    """The ``WO-NNNNN`` auto-numbering is project-scoped and stable."""

    def test_first_wo_in_project_is_00001(self):
        wo = WorkOrderFactory()
        assert wo.wo_number == "WO-00001"

    def test_second_wo_in_same_project_is_00002(self):
        project = ProjectFactory()
        WorkOrderFactory(project=project)
        wo2 = WorkOrderFactory(project=project)
        assert wo2.wo_number == "WO-00002"

    def test_two_projects_each_get_their_own_sequence(self):
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        a1 = WorkOrderFactory(project=project_a)
        b1 = WorkOrderFactory(project=project_b)
        assert a1.wo_number == "WO-00001"
        assert b1.wo_number == "WO-00001"

    def test_wo_number_is_stable_across_resaves(self):
        wo = WorkOrderFactory()
        original = wo.wo_number
        wo.title = "Updated title"
        wo.save()
        wo.refresh_from_db()
        assert wo.wo_number == original

    def test_explicit_wo_number_is_preserved(self):
        wo = WorkOrder.objects.create(
            project=ProjectFactory(),
            title="Manual import",
            wo_number="LEGACY-42",
        )
        assert wo.wo_number == "LEGACY-42"

    def test_unique_per_project_constraint(self):
        project = ProjectFactory()
        WorkOrder.objects.create(project=project, title="A", wo_number="WO-00001")
        with pytest.raises(IntegrityError):
            WorkOrder.objects.create(project=project, title="B", wo_number="WO-00001")


# ── WorkOrder shape & properties ──────────────────────────────────────────


@pytest.mark.django_db
class TestWorkOrderShape:
    """Defaults, ``is_open`` / ``is_overdue``, and clean()-level validation."""

    def test_default_status_is_draft(self):
        wo = WorkOrderFactory()
        assert wo.status == WorkOrderStatus.DRAFT

    def test_is_open_true_in_draft(self):
        assert WorkOrderFactory(status=WorkOrderStatus.DRAFT).is_open is True

    def test_is_open_false_when_closed(self):
        assert WorkOrderFactory(status=WorkOrderStatus.CLOSED).is_open is False

    def test_is_open_false_when_cancelled(self):
        assert WorkOrderFactory(status=WorkOrderStatus.CANCELLED).is_open is False

    def test_is_overdue_false_without_due_at(self):
        wo = WorkOrderFactory(due_at=None)
        assert wo.is_overdue is False

    def test_is_overdue_false_when_due_in_future(self):
        wo = WorkOrderFactory(due_at=timezone.now() + timedelta(days=1))
        assert wo.is_overdue is False

    def test_is_overdue_true_when_past_and_open(self):
        wo = WorkOrderFactory(
            due_at=timezone.now() - timedelta(days=1),
            status=WorkOrderStatus.IN_PROGRESS,
        )
        assert wo.is_overdue is True

    def test_is_overdue_false_when_past_but_completed(self):
        wo = WorkOrderFactory(
            due_at=timezone.now() - timedelta(days=1),
            status=WorkOrderStatus.COMPLETED,
        )
        assert wo.is_overdue is False

    def test_clean_rejects_end_before_start(self):
        wo = WorkOrderFactory.build(
            scheduled_start=timezone.now() + timedelta(hours=2),
            scheduled_end=timezone.now() + timedelta(hours=1),
        )
        with pytest.raises(ValidationError):
            wo.clean()

    def test_str_includes_wo_number_and_title(self):
        wo = WorkOrderFactory(title="Inspect AHU-04")
        assert wo.wo_number in str(wo)
        assert "Inspect AHU-04" in str(wo)


# ── WorkOrderStatusEvent immutability ─────────────────────────────────────


@pytest.mark.django_db
class TestWorkOrderStatusEvent:
    """Status events are append-only — updates are rejected with ImmutableError."""

    def test_create_succeeds(self):
        event = WorkOrderStatusEventFactory(
            from_status=WorkOrderStatus.DRAFT,
            to_status=WorkOrderStatus.SUBMITTED,
        )
        assert event.pk is not None

    def test_update_raises_immutable_error(self):
        event = WorkOrderStatusEventFactory(
            from_status=WorkOrderStatus.DRAFT,
            to_status=WorkOrderStatus.SUBMITTED,
        )
        event.note = "Trying to modify history"
        with pytest.raises(ImmutableError):
            event.save()

    def test_str_renders_from_to_labels(self):
        event = WorkOrderStatusEventFactory(
            from_status=WorkOrderStatus.DRAFT,
            to_status=WorkOrderStatus.SUBMITTED,
        )
        rendered = str(event)
        assert "Draft" in rendered
        assert "Submitted" in rendered
        assert event.work_order.wo_number in rendered

    def test_str_handles_initial_event_with_null_from_status(self):
        event = WorkOrderStatusEventFactory(
            from_status=None,
            to_status=WorkOrderStatus.DRAFT,
        )
        assert "—" in str(event)

    def test_ordering_is_chronological(self):
        wo = WorkOrderFactory()
        e1 = WorkOrderStatusEventFactory(work_order=wo, to_status=WorkOrderStatus.DRAFT)
        e2 = WorkOrderStatusEventFactory(work_order=wo, to_status=WorkOrderStatus.SUBMITTED)
        events = list(WorkOrderStatusEvent.objects.filter(work_order=wo))
        assert events == [e1, e2]


# ── WorkOrderAttachment ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestWorkOrderAttachment:
    """Attachments are simple file rows; default kind is photo."""

    def test_default_kind_is_photo(self):
        att = WorkOrderAttachmentFactory()
        assert att.kind == WorkOrderAttachment.Kind.PHOTO

    def test_str_uses_caption_when_present(self):
        att = WorkOrderAttachmentFactory(caption="Pre-work photo")
        assert str(att) == "Pre-work photo"

    def test_str_falls_back_to_kind_and_wo_number(self):
        wo = WorkOrderFactory()
        att = WorkOrderAttachmentFactory(work_order=wo, caption="")
        assert wo.wo_number in str(att)


# ── Permit ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPermit:
    """Permit shape, uniqueness, and expiry properties."""

    def test_str_includes_number_and_title(self):
        p = PermitFactory(permit_number="HW-2026-001", title="Roof hot work May 2026")
        assert "HW-2026-001" in str(p)
        assert "Roof hot work" in str(p)

    def test_unique_permit_number_per_project(self):
        project = ProjectFactory()
        PermitFactory(project=project, permit_number="DUPE-1")
        with pytest.raises(IntegrityError):
            PermitFactory(project=project, permit_number="DUPE-1")

    def test_same_permit_number_allowed_across_projects(self):
        PermitFactory(project=ProjectFactory(), permit_number="SHARED-1")
        PermitFactory(project=ProjectFactory(), permit_number="SHARED-1")
        assert Permit.objects.filter(permit_number="SHARED-1").count() == 2

    def test_is_expiring_soon_false_without_valid_until(self):
        p = PermitFactory(valid_until=None)
        assert p.is_expiring_soon is False

    def test_is_expiring_soon_true_at_t_minus_30(self):
        p = PermitFactory(valid_until=timezone.now() + timedelta(days=30))
        assert p.is_expiring_soon is True

    def test_is_expiring_soon_true_at_t_minus_89(self):
        p = PermitFactory(valid_until=timezone.now() + timedelta(days=89))
        assert p.is_expiring_soon is True

    def test_is_expiring_soon_false_well_past_window(self):
        # Use 120 days to stay clear of the 90-day boundary's microsecond drift.
        p = PermitFactory(valid_until=timezone.now() + timedelta(days=120))
        assert p.is_expiring_soon is False

    def test_is_expiring_soon_false_when_already_expired(self):
        p = PermitFactory(valid_until=timezone.now() - timedelta(days=1))
        assert p.is_expiring_soon is False

    def test_is_expired_true_when_past(self):
        p = PermitFactory(valid_until=timezone.now() - timedelta(days=1))
        assert p.is_expired is True

    def test_is_expired_false_without_valid_until(self):
        p = PermitFactory(valid_until=None)
        assert p.is_expired is False

    def test_m2m_link_to_work_orders(self):
        permit = PermitFactory()
        wo = WorkOrderFactory(project=permit.project)
        permit.work_orders.add(wo)
        assert wo in permit.work_orders.all()
        assert permit in wo.permits.all()
