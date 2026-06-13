# scheduling/tests/test_binding_lifecycle.py
"""E2-E immutable governance lifecycle, audit history, and parity repair tests."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import BindingGovernanceEvent, TaskEntityBinding
from scheduling.services.governance.active_state import apply_active_review, apply_trusted
from scheduling.services.governance.audit_history import BindingAuditHistoryService
from scheduling.services.governance.binding_lifecycle import (
    BindingLifecycleService,
    LifecycleValidationError,
    StaleLifecycleError,
)
from scheduling.services.governance.lifecycle_vocabulary import (
    PARITY_REPAIR_CONFIRM_PHRASE,
    REVERSE_CONFIRM_PHRASE,
    SUPERSEDE_CONFIRM_PHRASE,
)
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review: bool = True, method=None, confidence=0.8):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=method or TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=needs_review,
    )


@pytest.mark.django_db
def test_migration_backfill_maps_review_and_trusted():
    """Existing needs_review flags map to active_review and trusted lifecycle states."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    review = _bind(task, "GID-R", needs_review=True)
    trusted = _bind(task, "GID-T", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)

    assert review.governance_status == TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW
    assert review.is_active is True
    assert trusted.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
    assert trusted.is_active is True


@pytest.mark.django_db
def test_trusted_reads_use_lifecycle_filter():
    """Trusted reader excludes reversed bindings after lifecycle transition."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    IFCEntityFactory(ifc_file__project=project, global_id="GID-A")
    trusted = _bind(task, "GID-A", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    reader = BindingGovernanceReader(project.pk)
    assert "GID-A" in reader.trusted_entity_gids()

    svc = BindingLifecycleService(project, UserFactory())
    preview = svc.preview_reverse(str(trusted.pk))
    svc.reverse(
        str(trusted.pk),
        fingerprint=preview.fingerprint,
        reason_code="mistaken_approval",
        reason_text="test",
        confirmation=REVERSE_CONFIRM_PHRASE,
    )
    reader = BindingGovernanceReader(project.pk)
    assert "GID-A" not in reader.trusted_entity_gids()


@pytest.mark.django_db
def test_governance_event_is_append_only():
    """Events cannot be updated or deleted via application model methods."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-E", needs_review=True)
    svc = BindingLifecycleService(project, UserFactory())
    preview = svc.preview_reject(str(binding.pk))
    result = svc.reject(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="wrong_entity",
        reason_text="",
    )
    event = BindingGovernanceEvent.objects.get(pk=result.event_id)
    with pytest.raises(ValueError, match="append-only"):
        event.reason_text = "changed"
        event.save()
    with pytest.raises(ValueError, match="append-only"):
        event.delete()


@pytest.mark.django_db
def test_reject_requires_reason_and_excludes_from_queue():
    """Rejected review binding leaves queue and never becomes trusted."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-RJ", needs_review=True)
    user = UserFactory()
    svc = BindingLifecycleService(project, user)

    with pytest.raises(LifecycleValidationError):
        preview = svc.preview_reject(str(binding.pk))
        svc.reject(str(binding.pk), fingerprint=preview.fingerprint, reason_code="", reason_text="")

    preview = svc.preview_reject(str(binding.pk))
    svc.reject(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="other",
        reason_text="Not a match",
    )
    binding.refresh_from_db()
    assert binding.governance_status == TaskEntityBinding.GovernanceStatus.REJECTED
    assert binding.is_active is False
    assert apply_active_review(TaskEntityBinding.objects.filter(pk=binding.pk)).count() == 0
    assert apply_trusted(TaskEntityBinding.objects.filter(pk=binding.pk)).count() == 0


@pytest.mark.django_db
def test_reject_idempotent_on_repeat():
    """Identical reject decision reference returns no-op without duplicate effects."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-IDEM", needs_review=True)
    user = UserFactory()
    svc = BindingLifecycleService(project, user)
    preview = svc.preview_reject(str(binding.pk))
    first = svc.reject(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="duplicate",
        reason_text="",
    )
    second = svc.reject(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="duplicate",
        reason_text="",
    )
    assert second.noop is True
    assert (
        BindingGovernanceEvent.objects.filter(event_type="rejected", binding=binding).count() == 1
    )
    assert first.event_id == second.event_id


@pytest.mark.django_db
def test_stale_fingerprint_blocks_reject():
    """Stale fingerprint produces zero writes."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-STALE", needs_review=True)
    svc = BindingLifecycleService(project, UserFactory())
    with pytest.raises(StaleLifecycleError):
        svc.reject(
            str(binding.pk),
            fingerprint="deadbeef",
            reason_code="wrong_task",
            reason_text="",
        )
    binding.refresh_from_db()
    assert binding.governance_status == TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW


@pytest.mark.django_db
def test_reverse_removes_m2m_when_safe():
    """Reversal removes M2M when no other trusted binding needs the pair."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-M2M")
    task = TaskFactory(project=project)
    task.ifc_entities.add(entity)
    binding = _bind(
        task, entity.global_id, needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT
    )

    svc = BindingLifecycleService(project, UserFactory())
    preview = svc.preview_reverse(str(binding.pk))
    result = svc.reverse(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="mistaken_approval",
        reason_text="",
        confirmation=REVERSE_CONFIRM_PHRASE,
    )
    assert result.m2m_removed == 1
    assert not task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_reverse_preview_retains_m2m_when_unsafe(monkeypatch):
    """Reversal preview reports M2M retain when removal would be unsafe."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-RET", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    svc = BindingLifecycleService(project, UserFactory())
    monkeypatch.setattr(svc, "_can_remove_m2m", lambda _b: False)
    preview = svc.preview_reverse(str(binding.pk))
    assert preview.expected_m2m_change == "retain"


@pytest.mark.django_db
def test_supersede_atomic_linked_events():
    """Supersession ends old trusted binding and promotes replacement with linked events."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    old_entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-OLD")
    new_entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-NEW")
    task = TaskFactory(project=project)
    task.ifc_entities.add(old_entity)
    old_b = _bind(
        task, old_entity.global_id, needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT
    )
    new_b = _bind(task, new_entity.global_id, needs_review=True)

    svc = BindingLifecycleService(project, UserFactory())
    preview = svc.preview_supersede(str(old_b.pk), str(new_b.pk))
    result = svc.supersede(
        str(old_b.pk),
        str(new_b.pk),
        fingerprint=preview.fingerprint,
        reason_code="source_changed",
        reason_text="",
        confirmation=SUPERSEDE_CONFIRM_PHRASE,
    )
    old_b.refresh_from_db()
    new_b.refresh_from_db()
    assert old_b.governance_status == TaskEntityBinding.GovernanceStatus.SUPERSEDED
    assert new_b.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
    assert len(result.related_event_ids) == 2
    assert task.ifc_entities.filter(pk=new_entity.pk).exists()


@pytest.mark.django_db
def test_parity_add_missing_m2m():
    """Parity repair adds M2M for trusted binding missing relation."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-PAR")
    task = TaskFactory(project=project)
    binding = _bind(
        task, entity.global_id, needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT
    )

    svc = BindingLifecycleService(project, UserFactory())
    preview = svc.preview_parity_repair(
        binding_id=str(binding.pk),
        repair_type="accepted_missing_m2m",
    )
    result = svc.repair_parity(
        fingerprint=preview.fingerprint,
        reason_code="accepted_missing_m2m",
        reason_text="",
        confirmation=PARITY_REPAIR_CONFIRM_PHRASE,
        binding_id=str(binding.pk),
        repair_type="accepted_missing_m2m",
    )
    assert result.m2m_added == 1
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_audit_history_paginated_and_filtered():
    """Audit history returns immutable events with filters."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-AUD", needs_review=True)
    user = UserFactory()
    svc = BindingLifecycleService(project, user)
    preview = svc.preview_reject(str(binding.pk))
    svc.reject(
        str(binding.pk),
        fingerprint=preview.fingerprint,
        reason_code="insufficient_evidence",
        reason_text="",
    )
    history = BindingAuditHistoryService(str(project.pk)).build(
        BindingAuditHistoryService.filters_from_request({"event_type": "rejected"})
    )
    assert history["immutable"] is True
    assert history["pagination"]["total_items"] == 1
    assert history["events"][0]["event_type"] == "rejected"


@pytest.mark.django_db
def test_lifecycle_reject_api_post_only(client):
    """Lifecycle endpoints reject GET writes."""
    project = ProjectFactory()
    user = project.owner
    client.force_login(user)
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-GET", needs_review=True)
    url = reverse("scheduling:link_lifecycle_reject_preview", args=[project.pk, binding.pk])
    response = client.get(url)
    assert response.status_code == 405


@pytest.mark.django_db
def test_no_fake_migration_events_created():
    """Lifecycle backfill does not fabricate historical approval events."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-LEG", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    assert BindingGovernanceEvent.objects.filter(project=project).count() == 0
