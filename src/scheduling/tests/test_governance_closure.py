# scheduling/tests/test_governance_closure.py
"""E2-G integration hardening, performance, and closure tests."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import BindingGovernanceEvent, TaskEntityBinding
from scheduling.services.governance.binding_lifecycle import (
    BindingLifecycleService,
    LifecycleValidationError,
)
from scheduling.services.governance.binding_reconciliation import BindingReconciliationService
from scheduling.services.governance.review_queue import LinkReviewQueueService
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review=False, method=None):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=0.9,
        link_method=method or TaskEntityBinding.LinkMethod.EXACT,
        needs_review=needs_review,
    )


def _editor(project):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user, permission="editor")
    return user


@pytest.mark.django_db
def test_reconciliation_shell_without_run():
    """Reconciliation tab loads without full project scan."""
    project = ProjectFactory()
    filters = BindingReconciliationService.filters_from_request({})
    payload = BindingReconciliationService(str(project.pk)).build(filters)
    assert payload["not_evaluated"] is True
    assert payload["summary"]["status"] == "not_evaluated"
    assert payload["findings"] == []


@pytest.mark.django_db
def test_reconciliation_runs_when_explicit():
    """Full scan only when run=1."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-RUN", needs_review=False)
    filters = BindingReconciliationService.filters_from_request({"run": "1"})
    payload = BindingReconciliationService(str(project.pk)).build(filters)
    assert payload.get("not_evaluated") is not True
    assert payload["summary"]["total_evaluated"] >= 1


@pytest.mark.django_db
def test_review_queue_uses_lightweight_summary():
    """Queue build avoids GovernanceSummaryService full trusted row scan."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    for i in range(10):
        _bind(task, f"GID-Q{i}", needs_review=False)
    filters = LinkReviewQueueService.filters_from_request({"mode": "trusted", "page_size": "50"})
    with CaptureQueriesContext(connection) as ctx:
        LinkReviewQueueService(str(project.pk)).build(filters)
    assert len(ctx.captured_queries) <= 20


@pytest.mark.django_db
def test_supersede_pair_candidates_review_only():
    """Supersede pairing lists active review bindings only."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    trusted = _bind(task, "GID-OLD", needs_review=False)
    review = _bind(
        task, "GID-NEW", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC
    )
    svc = LinkReviewQueueService(str(project.pk))
    candidates = svc.supersede_replacement_candidates(trusted.pk)
    ids = {c["binding_id"] for c in candidates}
    assert str(review.pk) in ids
    assert str(trusted.pk) not in ids


@pytest.mark.django_db
def test_parity_bulk_preview_requires_selection():
    """Bulk parity preview rejects empty selection."""
    project = ProjectFactory()
    editor = _editor(project)
    svc = BindingLifecycleService(project, editor)
    with pytest.raises(LifecycleValidationError):
        svc.preview_parity_selected([])


@pytest.mark.django_db
def test_reconciliation_endpoint_shell_get(client):
    """Reconciliation GET without run returns shell."""
    project = ProjectFactory()
    client.force_login(project.owner)
    url = reverse("scheduling:link_governance_reconciliation", kwargs={"pk": project.pk})
    data = client.get(url).json()
    assert data["not_evaluated"] is True


@pytest.mark.django_db
def test_reconciliation_endpoint_post_not_allowed(client):
    """Reconciliation remains read-only."""
    project = ProjectFactory()
    client.force_login(project.owner)
    url = reverse("scheduling:link_governance_reconciliation", kwargs={"pk": project.pk})
    assert client.post(url).status_code == 405


@pytest.mark.django_db
def test_migration_0024_present():
    """Migration 0024 exists; DF-B1 adds 0027 as latest provisional 002x successor."""
    from pathlib import Path

    mig_dir = Path(__file__).resolve().parents[1] / "migrations"
    names = sorted(p.name for p in mig_dir.glob("002*.py"))
    assert "0024_binding_governance_events.py" in names
    successors = [
        n for n in names if n > "0024_binding_governance_events.py" and n.startswith("002")
    ]
    assert successors == [
        "0025_source_version_foundation.py",
        "0026_baseline_domain.py",
        "0027_analytical_snapshot_manifest.py",
    ]


@pytest.mark.django_db
def test_synthetic_lifecycle_journey():
    """Synthetic reject journey records audit event and inactive state."""
    project = ProjectFactory()
    editor = _editor(project)
    task = TaskFactory(project=project)
    review = _bind(task, "GID-J", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    svc = BindingLifecycleService(project, editor)
    preview = svc.preview_reject(str(review.pk))
    assert preview.eligible
    svc.reject(
        str(review.pk),
        fingerprint=preview.fingerprint,
        reason_code="wrong_entity",
        reason_text="Not the same element",
    )
    review.refresh_from_db()
    assert review.governance_status == TaskEntityBinding.GovernanceStatus.REJECTED
    assert review.is_active is False
    assert BindingGovernanceEvent.objects.filter(project=project).count() >= 1


@pytest.mark.django_db
def test_overview_still_read_only(client):
    """Overview GET does not mutate bindings."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-O", needs_review=False)
    before = TaskEntityBinding.objects.count()
    client.force_login(project.owner)
    url = reverse("scheduling:link_governance_overview", kwargs={"pk": project.pk})
    assert client.get(url).status_code == 200
    assert TaskEntityBinding.objects.count() == before
