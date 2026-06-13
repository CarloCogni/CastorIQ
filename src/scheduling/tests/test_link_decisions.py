# scheduling/tests/test_link_decisions.py
"""E2-C controlled link approval decision tests."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from environments.models import ProjectMembership
from environments.services import ProjectAccessService
from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.governance.link_decision import (
    BULK_API_MAX,
    BULK_CONFIRM_PHRASE,
    BULK_UI_MAX,
    DecisionValidationError,
    LinkDecisionService,
    StaleDecisionError,
    compute_selection_fingerprint,
)
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.governance.review_queue import LinkReviewQueueService
from scheduling.services.match_preview import MatchPreviewService
from scheduling.tests.factories import TaskFactory

BULK_PHRASE = BULK_CONFIRM_PHRASE


def _bind(task, gid: str, *, needs_review=True, method=None, confidence=1.0):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=method or TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=needs_review,
    )


def _entity(project, gid: str, **kwargs):
    return IFCEntityFactory(ifc_file__project=project, global_id=gid, **kwargs)


def _preview_url(project_pk, binding_pk) -> str:
    return reverse(
        "scheduling:link_decision_preview_one",
        kwargs={"pk": project_pk, "binding_pk": binding_pk},
    )


def _apply_url(project_pk, binding_pk) -> str:
    return reverse(
        "scheduling:link_decision_apply_one",
        kwargs={"pk": project_pk, "binding_pk": binding_pk},
    )


def _bulk_preview_url(project_pk) -> str:
    return reverse("scheduling:link_decision_bulk_preview", kwargs={"pk": project_pk})


def _bulk_apply_url(project_pk) -> str:
    return reverse("scheduling:link_decision_bulk_apply", kwargs={"pk": project_pk})


@pytest.mark.django_db
def test_review_binding_promoted_to_trusted():
    """Review binding is promoted to trusted on explicit approval."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-APPROVE")
    binding = _bind(task, entity.global_id, needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    result = svc.approve_one(
        binding.pk,
        selection_fingerprint=preview.selection_fingerprint,
    )

    binding.refresh_from_db()
    assert binding.needs_review is False
    assert result.promoted_count == 1
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_already_accepted_is_noop():
    """Already accepted binding returns no-op without mutation."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-NOOP")
    binding = _bind(task, entity.global_id, needs_review=False)
    task.ifc_entities.add(entity)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    assert preview.already_accepted_count == 1
    result = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    assert result.promoted_count == 0
    assert result.noop_count == 1


@pytest.mark.django_db
def test_normalized_requires_explicit_approval():
    """Normalized suggestion can be approved only via explicit human decision."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-NORM")
    binding = _bind(
        task,
        "GID-NORM",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.NORMALIZED,
        confidence=0.95,
    )

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    assert preview.eligible_count == 1
    assert preview.items[0]["link_method"] == TaskEntityBinding.LinkMethod.NORMALIZED
    result = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    binding.refresh_from_db()
    assert binding.needs_review is False
    assert binding.link_method == TaskEntityBinding.LinkMethod.NORMALIZED
    assert result.promoted_count == 1


@pytest.mark.django_db
def test_confidence_095_never_auto_approves_via_governance():
    """High confidence normalized binding stays in review until explicit approval."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _entity(project, "GID-HIGH")
    binding = _bind(
        task,
        "GID-HIGH",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.NORMALIZED,
        confidence=0.95,
    )
    filters = LinkReviewQueueService.filters_from_request({"mode": "review"})
    payload = LinkReviewQueueService(project.pk).build(filters)
    assert payload["pagination"]["total_items"] == 1
    assert TaskEntityBinding.objects.get(pk=binding.pk).needs_review is True


@pytest.mark.django_db
def test_semantic_requires_explicit_approval():
    """Semantic/embedding suggestion requires explicit approval."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-SEM")
    binding = _bind(
        task,
        "GID-SEM",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.EMBEDDING,
        confidence=0.88,
    )
    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    result = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    assert result.promoted_count == 1


@pytest.mark.django_db
def test_missing_entity_hard_blocked():
    """Binding whose entity is absent from project IFC scope is hard blocked."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-MISSING", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    assert preview.hard_blocked_count == 1
    assert preview.eligible_count == 0


@pytest.mark.django_db
def test_cross_project_binding_invalid():
    """Binding from another project is not eligible."""
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    user = p1.owner
    task = TaskFactory(project=p2)
    _entity(p2, "GID-XPROJ")
    binding = _bind(task, "GID-XPROJ", needs_review=True)

    svc = LinkDecisionService(p1, user)
    preview = svc.preview_selected([str(binding.pk)])
    assert preview.invalid_count == 1
    assert preview.eligible_count == 0


@pytest.mark.django_db
def test_stale_fingerprint_zero_writes():
    """Stale fingerprint raises conflict with zero writes."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-STALE")
    binding = _bind(task, "GID-STALE", needs_review=True)

    svc = LinkDecisionService(project, user)
    svc.preview_one(binding.pk)
    with pytest.raises(StaleDecisionError):
        svc.approve_one(binding.pk, selection_fingerprint="deadbeef")
    binding.refresh_from_db()
    assert binding.needs_review is True


@pytest.mark.django_db
def test_conflict_warning_requires_acknowledgment():
    """Possible overlap conflict requires explicit acknowledgment."""
    project = ProjectFactory()
    user = project.owner
    today = date.today()
    task1 = TaskFactory(project=project, start_date=today, end_date=today + timedelta(days=5))
    task2 = TaskFactory(
        project=project,
        start_date=today + timedelta(days=2),
        end_date=today + timedelta(days=10),
    )
    _entity(project, "GID-OVERLAP")
    _bind(task1, "GID-OVERLAP", needs_review=False)
    binding2 = _bind(task2, "GID-OVERLAP", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding2.pk)
    if preview.conflict_warning_count:
        with pytest.raises(DecisionValidationError):
            svc.approve_one(binding2.pk, selection_fingerprint=preview.selection_fingerprint)
        result = svc.approve_one(
            binding2.pk,
            selection_fingerprint=preview.selection_fingerprint,
            conflict_acknowledged=True,
        )
        assert result.promoted_count == 1


@pytest.mark.django_db
def test_m2m_added_on_approval():
    """Successful approval adds M2M when absent."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-M2M-ADD")
    binding = _bind(task, entity.global_id, needs_review=True)
    assert not task.ifc_entities.filter(pk=entity.pk).exists()

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    result = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    assert result.m2m_additions == 1
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_existing_m2m_is_noop():
    """Existing M2M relation is counted as no-op on approval."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-M2M-EXISTS")
    task.ifc_entities.add(entity)
    binding = _bind(task, entity.global_id, needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    result = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    assert result.m2m_noop >= 1
    assert result.m2m_removals == 0


@pytest.mark.django_db
def test_m2m_never_removed_on_approval():
    """Approval never removes M2M relations."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-KEEP-M2M")
    extra = _entity(project, "GID-EXTRA")
    task.ifc_entities.add(extra)
    binding = _bind(task, entity.global_id, needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    assert task.ifc_entities.filter(pk=extra.pk).exists()


@pytest.mark.django_db
def test_bulk_selected_only_scope():
    """Bulk apply only affects explicitly selected binding IDs."""
    project = ProjectFactory()
    user = project.owner
    t1 = TaskFactory(project=project)
    t2 = TaskFactory(project=project)
    _entity(project, "GID-B1")
    _entity(project, "GID-B2")
    b1 = _bind(t1, "GID-B1", needs_review=True)
    b2 = _bind(t2, "GID-B2", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_selected([str(b1.pk)])
    result = svc.approve_selected(
        [str(b1.pk)],
        selection_fingerprint=preview.selection_fingerprint,
        confirmation=BULK_PHRASE,
        confirm_acknowledged=True,
        require_bulk_phrase=True,
    )
    assert result.promoted_count == 1
    assert TaskEntityBinding.objects.get(pk=b2.pk).needs_review is True


@pytest.mark.django_db
def test_bulk_limit_enforced():
    """Bulk selection over API maximum is rejected."""
    project = ProjectFactory()
    user = project.owner
    ids = [str(uuid.uuid4()) for _ in range(BULK_API_MAX + 1)]
    svc = LinkDecisionService(project, user)
    with pytest.raises(DecisionValidationError):
        svc.preview_selected(ids)


@pytest.mark.django_db
def test_bulk_ui_limit_enforced_via_view(client: Client):
    """Bulk preview view enforces UI maximum of 100."""
    project = ProjectFactory()
    client.force_login(project.owner)
    ids = [str(uuid.uuid4()) for _ in range(BULK_UI_MAX + 1)]
    response = client.post(
        _bulk_preview_url(project.pk),
        data=json.dumps({"binding_ids": ids}),
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_fingerprint_deterministic():
    """Selection fingerprint is deterministic for same inputs."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _entity(project, "GID-FP")
    binding = _bind(task, "GID-FP", needs_review=True)
    svc = LinkDecisionService(project, project.owner)
    p1 = svc.preview_one(binding.pk)
    p2 = svc.preview_one(binding.pk)
    assert p1.selection_fingerprint == p2.selection_fingerprint


@pytest.mark.django_db
def test_changed_item_invalidates_fingerprint():
    """Promoting binding between preview and apply invalidates fingerprint."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-CHG")
    binding = _bind(task, "GID-CHG", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    binding.needs_review = False
    binding.save(update_fields=["needs_review"])

    with pytest.raises(StaleDecisionError):
        svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)


@pytest.mark.django_db
def test_bulk_all_or_nothing_rollback():
    """Unexpected failure during bulk promote rolls back all changes."""
    project = ProjectFactory()
    user = project.owner
    t1 = TaskFactory(project=project)
    t2 = TaskFactory(project=project)
    _entity(project, "GID-R1")
    _entity(project, "GID-R2")
    b1 = _bind(t1, "GID-R1", needs_review=True)
    b2 = _bind(t2, "GID-R2", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_selected([str(b1.pk), str(b2.pk)])

    with patch.object(
        LinkDecisionService,
        "_promote_binding_ids",
        side_effect=RuntimeError("forced rollback"),
    ):
        with pytest.raises(RuntimeError):
            svc.approve_selected(
                [str(b1.pk), str(b2.pk)],
                selection_fingerprint=preview.selection_fingerprint,
                confirmation=BULK_PHRASE,
                confirm_acknowledged=True,
                require_bulk_phrase=True,
            )
    assert TaskEntityBinding.objects.get(pk=b1.pk).needs_review is True
    assert TaskEntityBinding.objects.get(pk=b2.pk).needs_review is True


@pytest.mark.django_db
def test_idempotent_repeat_no_ops():
    """Repeating approval on already trusted binding is idempotent."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = _entity(project, "GID-IDEM")
    binding = _bind(task, entity.global_id, needs_review=False)
    task.ifc_entities.add(entity)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    r1 = svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)
    preview2 = svc.preview_one(binding.pk)
    r2 = svc.approve_one(binding.pk, selection_fingerprint=preview2.selection_fingerprint)
    assert r1.noop_count >= 1
    assert r2.noop_count >= 1
    assert r2.promoted_count == 0


@pytest.mark.django_db
def test_unauthorized_reviewer_rejected(client: Client):
    """Viewer without modify permission receives 403 on apply."""
    project = ProjectFactory()
    viewer = UserFactory()
    ProjectAccessService.add_member(
        project=project,
        user=viewer,
        permission=ProjectMembership.Permission.VIEWER,
    )
    task = TaskFactory(project=project)
    _entity(project, "GID-403")
    binding = _bind(task, "GID-403", needs_review=True)

    client.force_login(viewer)
    response = client.post(
        _apply_url(project.pk, binding.pk),
        data={"selection_fingerprint": "x"},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_e1_preview_remains_read_only():
    """E1 match preview performs zero writes."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="RO-001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-RO",
        properties={"Castor.Activity ID": "RO-001"},
    )
    before = TaskEntityBinding.objects.count()
    MatchPreviewService(project).preview("Activity ID")
    assert TaskEntityBinding.objects.count() == before


@pytest.mark.django_db
def test_governance_reader_correct_after_decision():
    """Trusted reader reflects promotion after approval."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-READER")
    binding = _bind(task, "GID-READER", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_one(binding.pk)
    svc.approve_one(binding.pk, selection_fingerprint=preview.selection_fingerprint)

    reader = BindingGovernanceReader(str(project.pk))
    assert reader.trusted_bindings_qs().filter(pk=binding.pk).exists()


@pytest.mark.django_db
def test_no_reject_endpoint_in_governance_urls():
    """No persistent reject endpoint exists in E2-C governance routes."""
    from scheduling import urls as scheduling_urls

    decision_paths = [
        str(p.pattern)
        for p in scheduling_urls.urlpatterns
        if "decision" in str(p.pattern) or "governance" in str(p.pattern)
    ]
    assert not any("reject" in p for p in decision_paths)


@pytest.mark.django_db
def test_bulk_preview_counts_reconcile():
    """Bulk preview eligible + noop + invalid equals requested."""
    project = ProjectFactory()
    user = project.owner
    t1 = TaskFactory(project=project)
    t2 = TaskFactory(project=project)
    _entity(project, "GID-C1")
    _entity(project, "GID-C2")
    b1 = _bind(t1, "GID-C1", needs_review=True)
    b2 = _bind(t2, "GID-C2", needs_review=False)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_selected([str(b1.pk), str(b2.pk)])
    assert preview.requested_count == 2
    assert preview.eligible_count + preview.already_accepted_count + preview.invalid_count >= 1


@pytest.mark.django_db
def test_bulk_requires_confirm_phrase():
    """Bulk apply requires APPROVE SELECTED confirmation phrase."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    _entity(project, "GID-PHRASE")
    binding = _bind(task, "GID-PHRASE", needs_review=True)

    svc = LinkDecisionService(project, user)
    preview = svc.preview_selected([str(binding.pk)])
    with pytest.raises(DecisionValidationError):
        svc.approve_selected(
            [str(binding.pk)],
            selection_fingerprint=preview.selection_fingerprint,
            confirmation="WRONG",
            confirm_acknowledged=True,
            require_bulk_phrase=True,
        )


@pytest.mark.django_db
def test_legacy_only_binding_id_not_approvable():
    """Non-UUID binding identifiers are rejected before database lookup."""
    project = ProjectFactory()
    user = project.owner
    svc = LinkDecisionService(project, user)
    with pytest.raises(DecisionValidationError):
        svc.preview_selected(["legacy-not-a-uuid"])


@pytest.mark.django_db
def test_compute_selection_fingerprint_stable():
    """Fingerprint helper produces stable hash."""
    from scheduling.services.governance.link_decision import BindingDecisionSpec

    specs = [
        BindingDecisionSpec(
            binding_id="a",
            task_id="t",
            entity_global_id="g",
            needs_review=True,
            link_method="manual",
            confidence=1.0,
        )
    ]
    fp1 = compute_selection_fingerprint("proj", specs)
    fp2 = compute_selection_fingerprint("proj", specs)
    assert fp1 == fp2
    assert len(fp1) == 64
