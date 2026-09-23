# scheduling/tests/test_approved_match_persistence.py
"""Tests for E1-E approved exact-match binding persistence."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.urls import reverse

from environments.models import ProjectMembership
from environments.services import ProjectAccessService
from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.approved_match_persistence import (
    ApprovalValidationError,
    ApprovedMatchPersistenceService,
    MatchApprovalRequest,
    StalePreviewError,
    validate_approval_request,
)
from scheduling.services.match_preview import MatchPreviewService
from scheduling.tests.factories import TaskFactory

CONFIRM = "APPROVE"


def _approval_from_preview(preview, **overrides) -> MatchApprovalRequest:
    """Build a valid approval request from a server preview."""
    data = {
        "preview_fingerprint": preview.preview_fingerprint,
        "param_name": preview.param_name,
        "expected_matched_task_count": preview.matched_task_count,
        "expected_projected_binding_count": preview.projected_binding_count,
        "confirmation": CONFIRM,
        "confirm_acknowledged": True,
    }
    data.update(overrides)
    return MatchApprovalRequest.from_payload(data)


def _apply_url(project_pk) -> str:
    return reverse("scheduling:schedule_link_apply_approved_param", kwargs={"pk": project_pk})


def _preview_url(project_pk, param: str = "Activity ID") -> str:
    return (
        reverse("scheduling:schedule_link_preview_param", kwargs={"pk": project_pk})
        + f"?param_name={param}&format=json"
    )


@pytest.mark.django_db
def test_valid_approval_persists_exact_pairs():
    """Valid approval creates accepted bindings for exact preview pairs."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-E1E-001",
        properties={"Castor.Activity ID": "E1E-001"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)

    result = ApprovedMatchPersistenceService(project, user).persist(approval)

    assert result.inserted_accepted_bindings == 1
    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=entity.global_id)
    assert binding.needs_review is False
    assert binding.link_method == TaskEntityBinding.LinkMethod.EXACT
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_one_task_many_entity_bindings():
    """One matched task can receive many entity bindings."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-MULTI")
    for i in range(8):
        IFCEntityFactory(
            ifc_file__project=project,
            global_id=f"GID-MULTI-{i}",
            properties={"Identity Data.Activity ID": "E1E-MULTI"},
        )

    preview = MatchPreviewService(project).preview("Activity ID")
    result = ApprovedMatchPersistenceService(project, user).persist(
        _approval_from_preview(preview),
    )

    assert preview.matched_task_count == 1
    assert result.approved_pair_count == 8
    assert result.inserted_accepted_bindings == 8
    assert TaskEntityBinding.objects.filter(task__project=project).count() == 8


@pytest.mark.django_db
def test_repeat_approval_is_idempotent():
    """Identical approval on unchanged data produces no duplicate bindings."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-IDEM")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-IDEM",
        properties={"Castor.Activity ID": "E1E-IDEM"},
    )
    svc = ApprovedMatchPersistenceService(project, user)

    preview1 = MatchPreviewService(project).preview("Activity ID")
    r1 = svc.persist(_approval_from_preview(preview1))

    preview2 = MatchPreviewService(project).preview("Activity ID")
    r2 = svc.persist(_approval_from_preview(preview2))

    assert r1.inserted_accepted_bindings == 1
    assert r2.inserted_accepted_bindings == 0
    assert r2.noop_existing_accepted_bindings == 1
    assert TaskEntityBinding.objects.filter(task__project=project).count() == 1


@pytest.mark.django_db
def test_stale_fingerprint_rejected_zero_writes():
    """Stale fingerprint rejects persistence with no binding writes."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-STALE")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-STALE"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview, preview_fingerprint="0" * 64)

    before = TaskEntityBinding.objects.count()
    with pytest.raises(StalePreviewError):
        ApprovedMatchPersistenceService(project, user).persist(approval)

    assert TaskEntityBinding.objects.count() == before
    assert not TaskEntityBinding.objects.filter(task=task).exists()


@pytest.mark.django_db
def test_modified_task_code_invalidates_approval():
    """Changing task activity code after preview invalidates fingerprint."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-TASK-OLD")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-TASK-OLD"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)

    task.activity_code = "E1E-TASK-NEW"
    task.save(update_fields=["activity_code"])

    with pytest.raises(StalePreviewError):
        ApprovedMatchPersistenceService(project, user).persist(approval)


@pytest.mark.django_db
def test_modified_ifc_activity_id_invalidates_approval():
    """Changing IFC Activity ID after preview invalidates fingerprint."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-IFC-OLD")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-IFC-OLD"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)

    entity.properties = {"Castor.Activity ID": "E1E-IFC-NEW"}
    entity.save(update_fields=["properties"])

    with pytest.raises(StalePreviewError):
        ApprovedMatchPersistenceService(project, user).persist(approval)


@pytest.mark.django_db
def test_modified_binding_state_invalidates_approval():
    """Adding a binding after preview changes fingerprint."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-BIND-OLD")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-BIND-OLD",
        properties={"Castor.Activity ID": "E1E-BIND-OLD"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)

    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-OTHER",
        needs_review=False,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
    )

    with pytest.raises(StalePreviewError):
        ApprovedMatchPersistenceService(project, user).persist(approval)


@pytest.mark.django_db
def test_incorrect_expected_counts_rejected():
    """Mismatched expected counts are rejected before writes."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-COUNT")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-COUNT"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview, expected_projected_binding_count=999)

    with pytest.raises(ApprovalValidationError):
        ApprovedMatchPersistenceService(project, user).persist(approval)


@pytest.mark.django_db
def test_missing_confirmation_rejected():
    """Missing confirmation phrase is rejected."""
    project = ProjectFactory()
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview, confirmation="NOPE")

    with pytest.raises(ApprovalValidationError):
        validate_approval_request(approval, preview)


@pytest.mark.django_db
def test_missing_ack_checkbox_rejected():
    """Missing acknowledgement checkbox is rejected."""
    project = ProjectFactory()
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview, confirm_acknowledged=False)

    with pytest.raises(ApprovalValidationError):
        validate_approval_request(approval, preview)


@pytest.mark.django_db
def test_unsupported_policy_rejected():
    """Unsupported policy values are rejected."""
    project = ProjectFactory()
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview, stale_binding_policy="delete_all")

    with pytest.raises(ApprovalValidationError):
        validate_approval_request(approval, preview)


@pytest.mark.django_db
def test_cross_project_entity_excluded():
    """Entities outside project scope are not persisted."""
    project = ProjectFactory()
    other = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-SCOPE")
    IFCEntityFactory(
        ifc_file__project=other,
        properties={"Castor.Activity ID": "E1E-SCOPE"},
    )

    preview = MatchPreviewService(project).preview("Activity ID")
    assert preview.projected_binding_count == 0

    with pytest.raises(ApprovalValidationError):
        ApprovedMatchPersistenceService(project, user).persist(_approval_from_preview(preview))


@pytest.mark.django_db
def test_review_pair_promoted_to_accepted():
    """Review-only exact pair is promoted to accepted on approval."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-PROMO")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PROMO",
        properties={"Castor.Activity ID": "E1E-PROMO"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        needs_review=True,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        confidence=0.7,
    )

    preview = MatchPreviewService(project).preview("Activity ID")
    result = ApprovedMatchPersistenceService(project, user).persist(
        _approval_from_preview(preview),
    )

    assert result.promoted_review_bindings == 1
    binding = TaskEntityBinding.objects.get(pk=TaskEntityBinding.objects.first().pk)
    assert binding.needs_review is False
    assert binding.link_method == TaskEntityBinding.LinkMethod.EXACT


@pytest.mark.django_db
def test_unrelated_review_binding_untouched():
    """Review bindings outside approved pairs remain review-only."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-MATCH")
    other = TaskFactory(project=project, activity_code="E1E-OTHER")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-MATCH",
        properties={"Castor.Activity ID": "E1E-MATCH"},
    )
    review = TaskEntityBinding.objects.create(
        task=other,
        entity_global_id="GID-REVIEW-UNRELATED",
        needs_review=True,
        link_method=TaskEntityBinding.LinkMethod.EMBEDDING,
    )

    preview = MatchPreviewService(project).preview("Activity ID")
    ApprovedMatchPersistenceService(project, user).persist(_approval_from_preview(preview))

    review.refresh_from_db()
    assert review.needs_review is True
    assert review.link_method == TaskEntityBinding.LinkMethod.EMBEDDING


@pytest.mark.django_db
def test_unrelated_manual_accepted_binding_untouched():
    """Accepted manual bindings outside approved pairs are preserved."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-KEEP")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-KEEP",
        properties={"Castor.Activity ID": "E1E-KEEP"},
    )
    manual = TaskEntityBinding.objects.create(
        task=TaskFactory(project=project, activity_code="OTHER-TASK"),
        entity_global_id="GID-MANUAL-KEEP",
        needs_review=False,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
    )

    preview = MatchPreviewService(project).preview("Activity ID")
    ApprovedMatchPersistenceService(project, user).persist(_approval_from_preview(preview))

    manual.refresh_from_db()
    assert manual.link_method == TaskEntityBinding.LinkMethod.MANUAL
    assert TaskEntityBinding.objects.filter(entity_global_id="GID-MANUAL-KEEP").exists()


@pytest.mark.django_db
def test_stale_accepted_bindings_reported_not_deleted():
    """Stale accepted bindings are reported but never deleted."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-STALE-RPT")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-MATCH-STALE",
        properties={"Castor.Activity ID": "E1E-STALE-RPT"},
    )
    stale = TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-STALE-OUT",
        needs_review=False,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
    )

    preview = MatchPreviewService(project).preview("Activity ID")
    result = ApprovedMatchPersistenceService(project, user).persist(
        _approval_from_preview(preview),
    )

    assert result.stale_bindings_reported >= 1
    assert TaskEntityBinding.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db
def test_legacy_m2m_adds_approved_entities():
    """Approved pairs add entities to legacy Task.ifc_entities M2M."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-M2M")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-M2M-ADD",
        properties={"Castor.Activity ID": "E1E-M2M"},
    )
    assert task.ifc_entities.count() == 0

    preview = MatchPreviewService(project).preview("Activity ID")
    result = ApprovedMatchPersistenceService(project, user).persist(
        _approval_from_preview(preview),
    )

    assert result.m2m_additions == 1
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_legacy_m2m_unrelated_not_removed():
    """Unrelated legacy M2M links are not removed."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-M2M-KEEP")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-M2M-MATCH",
        properties={"Castor.Activity ID": "E1E-M2M-KEEP"},
    )
    other_entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-M2M-OTHER",
        properties={"Castor.Activity ID": "NO-MATCH"},
    )
    task.ifc_entities.add(other_entity)

    preview = MatchPreviewService(project).preview("Activity ID")
    ApprovedMatchPersistenceService(project, user).persist(_approval_from_preview(preview))

    assert task.ifc_entities.filter(pk=other_entity.pk).exists()
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_repeat_approval_no_duplicate_m2m():
    """Repeat approval does not duplicate M2M rows."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-M2M-IDEM")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-M2M-IDEM"},
    )
    svc = ApprovedMatchPersistenceService(project, user)
    preview1 = MatchPreviewService(project).preview("Activity ID")
    svc.persist(_approval_from_preview(preview1))
    m2m_count = task.ifc_entities.count()

    preview2 = MatchPreviewService(project).preview("Activity ID")
    r2 = svc.persist(_approval_from_preview(preview2))

    assert task.ifc_entities.count() == m2m_count
    assert r2.m2m_additions == 0


@pytest.mark.django_db
def test_transaction_rolls_back_bindings_on_failure():
    """Mid-operation failure rolls back binding inserts."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-TX-FAIL")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-TX-FAIL"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)
    before = TaskEntityBinding.objects.count()

    with patch(
        "scheduling.models.TaskEntityBinding.objects.bulk_create",
        side_effect=IntegrityError("forced failure"),
    ):
        with pytest.raises(IntegrityError):
            ApprovedMatchPersistenceService(project, user).persist(approval)

    assert TaskEntityBinding.objects.count() == before


@pytest.mark.django_db
def test_transaction_rolls_back_m2m_on_failure():
    """Mid-operation M2M failure rolls back entire transaction."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project, activity_code="E1E-M2M-FAIL")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-M2M-FAIL"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = _approval_from_preview(preview)
    through = task.ifc_entities.through

    with patch.object(
        through.objects,
        "bulk_create",
        side_effect=IntegrityError("forced m2m failure"),
    ):
        with pytest.raises(IntegrityError):
            ApprovedMatchPersistenceService(project, user).persist(approval)

    assert not TaskEntityBinding.objects.filter(task=task).exists()
    assert not task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_unauthorized_user_cannot_persist(client):
    """Viewer without modify access cannot apply approved bindings."""
    owner = UserFactory()
    viewer = UserFactory()
    project = ProjectFactory(owner=owner)
    ProjectAccessService.add_member(
        project=project, user=viewer, permission=ProjectMembership.Permission.VIEWER
    )
    TaskFactory(project=project, activity_code="E1E-AUTH")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-AUTH"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    client.force_login(viewer)

    response = client.post(
        _apply_url(project.pk),
        {
            "preview_fingerprint": preview.preview_fingerprint,
            "param_name": "Activity ID",
            "expected_matched_task_count": preview.matched_task_count,
            "expected_projected_binding_count": preview.projected_binding_count,
            "confirmation": CONFIRM,
            "confirm_acknowledged": "true",
        },
    )

    assert response.status_code in (403, 404)
    assert not TaskEntityBinding.objects.filter(task__project=project).exists()


@pytest.mark.django_db
def test_get_cannot_persist(client):
    """Apply endpoint rejects GET requests."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    response = client.get(_apply_url(project.pk))
    assert response.status_code == 405


@pytest.mark.django_db
def test_apply_endpoint_returns_audit_payload(client):
    """Successful POST returns detailed audit JSON."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="E1E-API")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-API"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    client.force_login(user)

    response = client.post(
        _apply_url(project.pk) + "?format=json",
        {
            "preview_fingerprint": preview.preview_fingerprint,
            "param_name": "Activity ID",
            "expected_matched_task_count": preview.matched_task_count,
            "expected_projected_binding_count": preview.projected_binding_count,
            "confirmation": CONFIRM,
            "confirm_acknowledged": "true",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["inserted_accepted_bindings"] == 1
    assert data["audit_reference_id"]
    assert data["transaction_status"] == "committed"


@pytest.mark.django_db
def test_stale_fingerprint_http_409(client):
    """Stale fingerprint returns HTTP 409 via API."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="E1E-409")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-409"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    client.force_login(user)

    response = client.post(
        _apply_url(project.pk) + "?format=json",
        {
            "preview_fingerprint": "deadbeef" * 8,
            "param_name": "Activity ID",
            "expected_matched_task_count": preview.matched_task_count,
            "expected_projected_binding_count": preview.projected_binding_count,
            "confirmation": CONFIRM,
            "confirm_acknowledged": "true",
        },
    )

    assert response.status_code == 409
    assert not TaskEntityBinding.objects.filter(task__project=project).exists()


@pytest.mark.django_db
def test_preview_endpoint_remains_no_write(client):
    """Preview GET still makes no writes after E1-E."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="E1E-PREV-NW")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "E1E-PREV-NW"},
    )
    client.force_login(user)
    before = TaskEntityBinding.objects.count()

    response = client.get(_preview_url(project.pk))
    assert response.status_code == 200
    assert TaskEntityBinding.objects.count() == before
