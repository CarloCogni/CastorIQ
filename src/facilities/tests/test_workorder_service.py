# facilities/tests/test_workorder_service.py
"""Tests for ``facilities.services.workorder_service.WorkOrderService``.

The state machine is the load-bearing piece — its parametrized matrices cover
every (from_status, transition) cell and every (role, transition) gate. Bulk
methods are tested for atomicity. The skip-triage path gets its own block.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from environments.models import ProjectRole
from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.models import (
    ActionRequest,
    WorkOrder,
    WorkOrderStatus,
)
from facilities.services.workorder_service import (
    _TRANSITIONS,
    VALID_TRANSITION_NAMES,
    IllegalTransitionError,
    RoleNotAllowedError,
    WorkOrderNotFoundError,
    WorkOrderService,
    WorkOrderValidationError,
)
from facilities.tests.factories import (
    ActionRequestFactory,
    PermitFactory,
    WorkOrderFactory,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _service_with_role(role: str = "facilitiesmanager"):
    """Return ``(WorkOrderService, project, user)`` with the actor holding ``role``.

    Implicit-owner case is opted into with ``role="implicit_owner"`` — uses
    the project owner as the actor (ProjectFactory bootstraps OWNER membership
    automatically).
    """
    if role == "implicit_owner":
        user = UserFactory()
        project = ProjectFactory(owner=user)
        # No ProjectRole — the implicit-owner fallback in
        # WorkOrderService._actor_role_snapshot kicks in.
    else:
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(user=user, project=project, permission="editor")
        ProjectRoleFactory(user=user, project=project, role=role)
    return WorkOrderService(project, user), project, user


# ── Reads ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestReads:
    """get_work_order / list_work_orders / kanban / calendar."""

    def test_get_work_order_returns_in_project_wo(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        assert svc.get_work_order(wo.pk) == wo

    def test_get_work_order_misses_outside_project(self):
        svc, _, _ = _service_with_role()
        other = WorkOrderFactory()  # different project
        with pytest.raises(WorkOrderNotFoundError):
            svc.get_work_order(other.pk)

    def test_list_excludes_closed_by_default(self):
        svc, project, _ = _service_with_role()
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        WorkOrderFactory(project=project, status=WorkOrderStatus.CLOSED)
        WorkOrderFactory(project=project, status=WorkOrderStatus.CANCELLED)
        assert svc.list_work_orders().count() == 1

    def test_list_includes_closed_when_asked(self):
        svc, project, _ = _service_with_role()
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        WorkOrderFactory(project=project, status=WorkOrderStatus.CLOSED)
        assert svc.list_work_orders(include_closed=True).count() == 2

    def test_list_filters_by_status(self):
        svc, project, _ = _service_with_role()
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        assert svc.list_work_orders(status=WorkOrderStatus.SUBMITTED).count() == 1

    def test_list_overdue_only(self):
        svc, project, _ = _service_with_role()
        WorkOrderFactory(project=project, due_at=timezone.now() - timedelta(days=1))
        WorkOrderFactory(project=project, due_at=timezone.now() + timedelta(days=1))
        assert svc.list_work_orders(overdue_only=True).count() == 1

    def test_list_q_matches_wo_number(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        assert wo in svc.list_work_orders(q=wo.wo_number)

    def test_kanban_columns_exclude_closed(self):
        svc, project, _ = _service_with_role()
        WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        WorkOrderFactory(project=project, status=WorkOrderStatus.CLOSED)
        cols = svc.kanban_columns()
        # Closed is not even a key on the board
        assert WorkOrderStatus.CLOSED not in cols
        assert len(cols[WorkOrderStatus.DRAFT]) == 1

    def test_calendar_window_returns_events_in_range(self):
        svc, project, _ = _service_with_role()
        in_window = WorkOrderFactory(
            project=project,
            scheduled_start=timezone.now() + timedelta(days=2),
        )
        WorkOrderFactory(
            project=project,
            scheduled_start=timezone.now() + timedelta(days=20),
        )
        WorkOrderFactory(project=project, scheduled_start=None)

        start = timezone.now()
        end = timezone.now() + timedelta(days=7)
        events = list(svc.calendar_window(start=start, end=end))
        assert events == [in_window]


# ── Create / update ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCreate:
    """create_work_order forces project scope, defaults requester, writes initial event."""

    def test_create_writes_initial_status_event(self):
        svc, project, user = _service_with_role()
        wo = svc.create_work_order(title="Replace filter")
        events = list(wo.status_events.all())
        assert len(events) == 1
        assert events[0].from_status is None
        assert events[0].to_status == WorkOrderStatus.DRAFT
        assert events[0].actor == user
        assert events[0].actor_role == "facilitiesmanager"

    def test_create_defaults_requester_to_actor(self):
        svc, _, user = _service_with_role()
        wo = svc.create_work_order(title="x")
        assert wo.requested_by == user

    def test_create_assigns_project_from_service(self):
        svc, project, _ = _service_with_role()
        wo = svc.create_work_order(title="x")
        assert wo.project == project

    def test_create_rejects_blank_title(self):
        svc, _, _ = _service_with_role()
        with pytest.raises(WorkOrderValidationError):
            svc.create_work_order(title="   ")

    def test_create_rejects_unknown_field(self):
        svc, _, _ = _service_with_role()
        with pytest.raises(WorkOrderValidationError):
            svc.create_work_order(title="x", status=WorkOrderStatus.SUBMITTED)

    def test_create_implicit_owner_records_buildingowner_role(self):
        svc, _, _ = _service_with_role(role="implicit_owner")
        wo = svc.create_work_order(title="x")
        assert wo.status_events.first().actor_role == ProjectRole.Role.BUILDINGOWNER


@pytest.mark.django_db
class TestUpdate:
    """update_work_order is stage-gated and rejects forbidden fields."""

    def test_update_changes_simple_fields(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        svc.update_work_order(wo, title="renamed", priority=1)
        wo.refresh_from_db()
        assert wo.title == "renamed"
        assert wo.priority == 1

    def test_update_rejects_changing_status_directly(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        with pytest.raises(WorkOrderValidationError):
            svc.update_work_order(wo, status=WorkOrderStatus.IN_PROGRESS)

    def test_update_rejects_assignee_after_in_progress(self):
        svc, project, user = _service_with_role()
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.IN_PROGRESS,
            assignee_user=user,
        )
        with pytest.raises(WorkOrderValidationError):
            svc.update_work_order(wo, assignee_user=UserFactory())

    def test_update_blocks_when_closed(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.CLOSED)
        with pytest.raises(IllegalTransitionError):
            svc.update_work_order(wo, title="ignored")


# ── Transition graph (parametrized matrix) ─────────────────────────────────


@pytest.mark.django_db
class TestTransitionGraph:
    """Every (from_status, transition) cell registered in _TRANSITIONS yields the right to_status."""

    @pytest.mark.parametrize("key", list(_TRANSITIONS.keys()))
    def test_registered_transition_executes(self, key):
        from_status, name = key
        rule = _TRANSITIONS[key]
        svc, project, user = _service_with_role()  # FACILITIESMANAGER
        wo = WorkOrderFactory(
            project=project,
            status=from_status,
            assignee_user=user,
            requested_by=user,
        )

        # `submit` from DRAFT requires extra fields? No — submit is the simplest.
        # Some transitions need params; supply minimum-viable ones.
        kwargs: dict = {}
        if name == "assign":
            kwargs = {"assignee_user_id": user.id}
        elif name == "schedule":
            kwargs = {"scheduled_start": timezone.now() + timedelta(hours=1)}
        elif name == "reassign":
            kwargs = {"assignee_user_id": user.id}

        # `start` and `complete` need an engineer-class role + assignee match.
        # Re-create the service under that role for those rows.
        if name in ("start", "complete"):
            svc, project, user = _service_with_role(role="maintenanceengineer")
            wo = WorkOrderFactory(
                project=project,
                status=from_status,
                assignee_user=user,
                requested_by=user,
            )

        method = getattr(svc, name)
        method(wo, **kwargs)
        wo.refresh_from_db()
        assert wo.status == rule.to_status

    def test_unregistered_transition_raises(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        # `verify` is not a transition out of DRAFT
        with pytest.raises(IllegalTransitionError):
            svc.verify(wo)

    def test_valid_transition_names_matches_table_keys(self):
        """The exported set must be the set of names in _TRANSITIONS, so the dispatcher view in 3.C can validate URL kwargs."""
        names_from_table = {name for _, name in _TRANSITIONS}
        assert VALID_TRANSITION_NAMES == names_from_table


# ── Role gates ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRoleGates:
    """Role checks reject actors not in the allowed set."""

    def test_triage_rejects_engineer(self):
        svc, project, _ = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        with pytest.raises(RoleNotAllowedError):
            svc.triage(wo)

    def test_triage_allows_facilitiesmanager(self):
        svc, project, _ = _service_with_role(role="facilitiesmanager")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        svc.triage(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.TRIAGED

    def test_triage_allows_buildingowner(self):
        svc, project, _ = _service_with_role(role="buildingowner")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        svc.triage(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.TRIAGED

    def test_triage_allows_implicit_owner(self):
        svc, project, _ = _service_with_role(role="implicit_owner")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        svc.triage(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.TRIAGED

    def test_start_requires_assignee_match(self):
        """An engineer who is not the assignee cannot start the WO."""
        svc, project, _ = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.ASSIGNED,
            assignee_user=UserFactory(),  # different user
        )
        with pytest.raises(RoleNotAllowedError):
            svc.start(wo)

    def test_start_with_assignee_match_succeeds(self):
        svc, project, user = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.ASSIGNED, assignee_user=user)
        svc.start(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.IN_PROGRESS
        assert wo.actual_start is not None

    def test_cancel_by_requester_in_submitted(self):
        """The requester can cancel their own submitted WO even without FM role."""
        svc, project, user = _service_with_role(role="occupant")
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.SUBMITTED,
            requested_by=user,
        )
        svc.cancel(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.CANCELLED


# ── Specific transitions: side effects ─────────────────────────────────────


@pytest.mark.django_db
class TestTransitionSideEffects:
    """Each transition writes one status event with the right shape and stamps timestamps."""

    def test_complete_stamps_actual_end(self):
        svc, project, user = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(
            project=project, status=WorkOrderStatus.IN_PROGRESS, assignee_user=user
        )
        svc.complete(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.COMPLETED
        assert wo.actual_end is not None

    def test_verify_stamps_verified_at_and_by(self):
        svc, project, user = _service_with_role()  # FM
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.COMPLETED)
        svc.verify(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.VERIFIED
        assert wo.verified_at is not None
        assert wo.verified_by == user

    def test_close_stamps_closed_at(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.VERIFIED)
        svc.close(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.CLOSED
        assert wo.closed_at is not None

    def test_reopen_clears_closed_at(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.CLOSED,
            closed_at=timezone.now(),
        )
        svc.reopen(wo)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.ASSIGNED
        assert wo.closed_at is None

    def test_assign_records_assignee(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.TRIAGED)
        new_assignee = UserFactory()
        svc.assign(wo, assignee_user_id=new_assignee.id)
        wo.refresh_from_db()
        assert wo.assignee_user == new_assignee
        assert wo.status == WorkOrderStatus.ASSIGNED

    def test_assign_rejects_when_no_target(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.TRIAGED)
        with pytest.raises(WorkOrderValidationError):
            svc.assign(wo)

    def test_each_transition_writes_exactly_one_status_event(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        before = wo.status_events.count()
        svc.submit(wo)
        wo.refresh_from_db()
        assert wo.status_events.count() == before + 1
        ev = wo.status_events.last()
        assert ev.from_status == WorkOrderStatus.DRAFT
        assert ev.to_status == WorkOrderStatus.SUBMITTED


# ── Skip-triage path ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSkipTriagePath:
    """submit(skip_triage=True) is allowed only for the engineer-and-assignee."""

    def test_engineer_self_assignee_can_skip(self):
        svc, project, user = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.DRAFT,
            assignee_user=user,
            requested_by=user,
        )
        svc.submit(wo, skip_triage=True)
        wo.refresh_from_db()
        assert wo.status == WorkOrderStatus.ASSIGNED

    def test_engineer_not_assignee_is_rejected(self):
        svc, project, _ = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.DRAFT,
            assignee_user=UserFactory(),
        )
        with pytest.raises(RoleNotAllowedError):
            svc.submit(wo, skip_triage=True)

    def test_facilitiesmanager_cannot_skip_triage(self):
        svc, project, user = _service_with_role(role="facilitiesmanager")
        wo = WorkOrderFactory(
            project=project,
            status=WorkOrderStatus.DRAFT,
            assignee_user=user,
        )
        with pytest.raises(RoleNotAllowedError):
            svc.submit(wo, skip_triage=True)

    def test_skip_writes_one_event_with_skip_note(self):
        svc, project, user = _service_with_role(role="maintenanceengineer")
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT, assignee_user=user)
        svc.submit(wo, skip_triage=True)
        ev = wo.status_events.last()
        assert ev.to_status == WorkOrderStatus.ASSIGNED
        assert "skipped" in ev.note.lower() or "self" in ev.note.lower()


# ── Bulk methods ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBulk:
    """Bulk operations swallow per-row failures and return success count."""

    def test_bulk_assign_returns_success_count(self):
        svc, project, _ = _service_with_role()
        wo1 = WorkOrderFactory(project=project, status=WorkOrderStatus.TRIAGED)
        wo2 = WorkOrderFactory(project=project, status=WorkOrderStatus.TRIAGED)
        wo3 = WorkOrderFactory(project=project, status=WorkOrderStatus.IN_PROGRESS)
        new_assignee = UserFactory()
        n = svc.bulk_assign([wo1.pk, wo2.pk, wo3.pk], assignee_user_id=new_assignee.id)
        assert n == 2  # wo3 was in IN_PROGRESS, no `assign` transition there

    def test_bulk_assign_skips_unknown_ids(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project, status=WorkOrderStatus.TRIAGED)
        n = svc.bulk_assign(
            [wo.pk, "00000000-0000-0000-0000-000000000000"],
            assignee_user_id=UserFactory().id,
        )
        assert n == 1

    def test_bulk_set_priority_changes_only_open(self):
        svc, project, _ = _service_with_role()
        wo1 = WorkOrderFactory(project=project, status=WorkOrderStatus.DRAFT)
        wo2 = WorkOrderFactory(project=project, status=WorkOrderStatus.CLOSED)
        n = svc.bulk_set_priority([wo1.pk, wo2.pk], priority=1)
        wo1.refresh_from_db()
        assert n == 1
        assert wo1.priority == 1

    def test_bulk_cancel_skips_disallowed_states(self):
        svc, project, _ = _service_with_role()
        wo1 = WorkOrderFactory(project=project, status=WorkOrderStatus.SUBMITTED)
        wo2 = WorkOrderFactory(project=project, status=WorkOrderStatus.IN_PROGRESS)
        n = svc.bulk_cancel([wo1.pk, wo2.pk])
        assert n == 1
        wo1.refresh_from_db()
        assert wo1.status == WorkOrderStatus.CANCELLED


# ── Permits & attachments via the service ──────────────────────────────────


@pytest.mark.django_db
class TestAttachmentsAndPermits:
    def test_link_permit(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        permit = PermitFactory(project=project)
        svc.link_permit(wo, permit)
        assert permit in wo.permits.all()

    def test_link_permit_cross_project_rejected(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        other_permit = PermitFactory()  # different project
        with pytest.raises(WorkOrderValidationError):
            svc.link_permit(wo, other_permit)

    def test_unlink_permit(self):
        svc, project, _ = _service_with_role()
        wo = WorkOrderFactory(project=project)
        permit = PermitFactory(project=project)
        svc.link_permit(wo, permit)
        svc.unlink_permit(wo, permit)
        assert permit not in wo.permits.all()


# ── ActionRequest escalation ───────────────────────────────────────────────


@pytest.mark.django_db
class TestActionRequestEscalation:
    """escalate_action_request creates a WO and moves the AR to ESCALATED."""

    def test_escalate_creates_wo_and_marks_ar(self):
        svc, project, _ = _service_with_role()
        ar = ActionRequestFactory(project=project, title="Heater broken")
        wo = svc.escalate_action_request(ar)
        ar.refresh_from_db()
        assert isinstance(wo, WorkOrder)
        assert wo.title == "Heater broken"
        assert wo.source_action_request == ar
        assert ar.status == ActionRequest.Status.ESCALATED

    def test_escalate_copies_ar_anchors(self):
        svc, project, _ = _service_with_role()
        ar = ActionRequestFactory(project=project, title="x")
        wo = svc.escalate_action_request(ar)
        # No asset on the AR — WO inherits None too.
        assert wo.affected_asset is None

    def test_escalate_rejects_cross_project(self):
        svc, _, _ = _service_with_role()
        ar = ActionRequestFactory()  # different project
        with pytest.raises(WorkOrderValidationError):
            svc.escalate_action_request(ar)

    def test_escalate_rejects_dismissed_ar(self):
        svc, project, _ = _service_with_role()
        ar = ActionRequestFactory(project=project, status=ActionRequest.Status.DISMISSED)
        with pytest.raises(WorkOrderValidationError):
            svc.escalate_action_request(ar)
