# facilities/tests/test_permit_service.py
"""Tests for ``facilities.services.permit_service.PermitService``."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import Permit
from facilities.services.permit_service import (
    PermitNotFoundError,
    PermitService,
    PermitValidationError,
)
from facilities.tests.factories import PermitFactory


def _service():
    project = ProjectFactory()
    user = UserFactory()
    return PermitService(project, user), project, user


@pytest.mark.django_db
class TestReads:
    def test_get_permit_returns_in_project(self):
        svc, project, _ = _service()
        permit = PermitFactory(project=project)
        assert svc.get_permit(permit.pk) == permit

    def test_get_permit_misses_outside_project(self):
        svc, _, _ = _service()
        other = PermitFactory()
        with pytest.raises(PermitNotFoundError):
            svc.get_permit(other.pk)

    def test_list_filters_by_status(self):
        svc, project, _ = _service()
        PermitFactory(project=project, status=Permit.Status.ACTIVE)
        PermitFactory(project=project, status=Permit.Status.REVOKED)
        active = svc.list_permits(status=Permit.Status.ACTIVE)
        assert active.count() == 1

    def test_list_excludes_revoked_when_asked(self):
        svc, project, _ = _service()
        PermitFactory(project=project, status=Permit.Status.ACTIVE)
        PermitFactory(project=project, status=Permit.Status.REVOKED)
        assert svc.list_permits(include_revoked=False).count() == 1

    def test_list_filters_by_kind(self):
        svc, project, _ = _service()
        PermitFactory(project=project, kind=Permit.Kind.HOT_WORK)
        PermitFactory(project=project, kind=Permit.Kind.ELECTRICAL)
        assert svc.list_permits(kind=Permit.Kind.HOT_WORK).count() == 1

    def test_list_q_matches_number_or_title(self):
        svc, project, _ = _service()
        PermitFactory(project=project, permit_number="HW-2026-001", title="x")
        PermitFactory(project=project, permit_number="ZZZ", title="x")
        assert svc.list_permits(q="HW-2026").count() == 1

    def test_list_expiring_within_days(self):
        svc, project, _ = _service()
        PermitFactory(project=project, valid_until=timezone.now() + timedelta(days=10))
        PermitFactory(project=project, valid_until=timezone.now() + timedelta(days=120))
        assert svc.list_permits(expiring_within_days=30).count() == 1


@pytest.mark.django_db
class TestMutations:
    def test_create_permit_success(self):
        svc, project, _ = _service()
        permit = svc.create_permit(
            permit_number="P-1",
            title="Hot work May",
            kind=Permit.Kind.HOT_WORK,
        )
        assert permit.project == project
        assert permit.permit_number == "P-1"

    def test_create_rejects_missing_number(self):
        svc, _, _ = _service()
        with pytest.raises(PermitValidationError):
            svc.create_permit(title="x")

    def test_create_rejects_missing_title(self):
        svc, _, _ = _service()
        with pytest.raises(PermitValidationError):
            svc.create_permit(permit_number="P-1")

    def test_create_rejects_unknown_field(self):
        svc, _, _ = _service()
        with pytest.raises(PermitValidationError):
            svc.create_permit(permit_number="P-1", title="x", oops="yes")

    def test_update_permit_success(self):
        svc, project, _ = _service()
        permit = PermitFactory(project=project)
        svc.update_permit(permit, title="renamed")
        permit.refresh_from_db()
        assert permit.title == "renamed"

    def test_update_rejects_cross_project(self):
        svc, _, _ = _service()
        permit = PermitFactory()  # different project
        with pytest.raises(PermitValidationError):
            svc.update_permit(permit, title="x")

    def test_revoke_permit(self):
        svc, project, _ = _service()
        permit = PermitFactory(project=project, status=Permit.Status.ACTIVE)
        svc.revoke_permit(permit, note="No longer needed")
        permit.refresh_from_db()
        assert permit.status == Permit.Status.REVOKED
        assert "No longer needed" in permit.notes

    def test_revoke_permit_idempotent(self):
        svc, project, _ = _service()
        permit = PermitFactory(project=project, status=Permit.Status.REVOKED)
        # No exception, no change.
        svc.revoke_permit(permit)
        permit.refresh_from_db()
        assert permit.status == Permit.Status.REVOKED

    def test_delete_permit(self):
        svc, project, _ = _service()
        permit = PermitFactory(project=project)
        svc.delete_permit(permit)
        assert not Permit.objects.filter(pk=permit.pk).exists()
