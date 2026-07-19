# scheduling/tests/test_writer_alignment.py
"""Packaging Fix Package 1 — 4D writer alignment with trusted binding contract."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import BindingGovernanceEvent, TaskEntityBinding
from scheduling.services.approved_match_persistence import (
    ApprovedMatchPersistenceService,
    MatchApprovalRequest,
)
from scheduling.services.autolink import run_autolink
from scheduling.services.governance.active_state import is_trusted_binding, trusted_filter
from scheduling.services.governance.trust_promotion import (
    create_trusted_bindings,
    promote_bindings_to_trusted,
)
from scheduling.services.match_preview import MatchPreviewService
from scheduling.tests.factories import TaskFactory


def _review_binding(task, gid: str, *, confidence: float = 0.96):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
    )


@pytest.mark.django_db
def test_promote_helper_sets_full_trusted_contract_and_event():
    """Promotion sets trusted triple and records one governance event."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-PROMOTE-1")
    binding = _review_binding(task, entity.global_id)

    result = promote_bindings_to_trusted(
        project=project,
        user=user,
        binding_ids=[str(binding.pk)],
        request_source="test_promotion",
    )
    binding.refresh_from_db()

    assert result.promoted == 1
    assert is_trusted_binding(binding)
    assert binding.needs_review is False
    assert binding.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
    assert binding.is_active is True
    assert (
        BindingGovernanceEvent.objects.filter(
            project=project, binding=binding, event_type="approved"
        ).count()
        == 1
    )
    assert task.ifc_entities.filter(pk=entity.pk).exists()


@pytest.mark.django_db
def test_promote_already_trusted_is_idempotent_no_duplicate_event():
    """Re-promoting a trusted binding is a no-op without a second event."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-IDEM")
    binding = TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
    )

    first = promote_bindings_to_trusted(
        project=project,
        user=user,
        binding_ids=[str(binding.pk)],
        request_source="test_idempotent",
    )
    second = promote_bindings_to_trusted(
        project=project,
        user=user,
        binding_ids=[str(binding.pk)],
        request_source="test_idempotent",
    )

    assert first.noop_already_trusted == 1
    assert first.promoted == 0
    assert second.noop_already_trusted == 1
    assert second.promoted == 0
    assert BindingGovernanceEvent.objects.filter(project=project, binding=binding).count() == 0


@pytest.mark.django_db
def test_pipeline_accept_produces_trusted_binding_and_event(client):
    """BindingAcceptView promotes via LinkDecisionService trusted contract."""
    project = ProjectFactory()
    user = project.owner
    client.force_login(user)
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-ACCEPT")
    binding = _review_binding(task, entity.global_id)

    url = reverse("scheduling:binding_accept", kwargs={"pk": project.pk, "binding_pk": binding.pk})
    response = client.post(url)

    assert response.status_code == 200
    binding.refresh_from_db()
    assert is_trusted_binding(binding)
    assert (
        BindingGovernanceEvent.objects.filter(
            project=project, binding=binding, event_type="approved"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_pipeline_bulk_accept_does_not_promote(client):
    """Bulk accept from Pipeline is propose-only — no trusted writes."""
    project = ProjectFactory()
    user = project.owner
    client.force_login(user)
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-BULK")
    binding = _review_binding(task, entity.global_id, confidence=0.99)

    url = reverse("scheduling:binding_bulk_accept", kwargs={"pk": project.pk})
    response = client.post(url)

    assert response.status_code == 200
    binding.refresh_from_db()
    assert binding.needs_review is True
    assert not is_trusted_binding(binding)
    assert BindingGovernanceEvent.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_autolink_creates_proposals_not_trusted():
    """Autolink exact matches remain proposed until Governance approval."""
    project = ProjectFactory()
    code = "ALIGN-1001"
    task = TaskFactory(project=project, activity_code=code)
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-ALIGN-1001",
        properties={"Castor.Activity ID": code},
    )

    with patch("scheduling.services.autolink.analyse_schedule_context", return_value={}):
        summary = run_autolink(project, ifc_param_name="Activity ID")

    assert summary["linked_exact"] == 1
    assert summary["needs_review"] == 1
    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=entity.global_id)
    assert binding.needs_review is True
    assert binding.governance_status == TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW
    assert not is_trusted_binding(binding)
    assert not task.ifc_entities.filter(pk=entity.pk).exists()
    assert TaskEntityBinding.objects.filter(trusted_filter(), task=task).count() == 0


@pytest.mark.django_db
def test_exact_match_approval_creates_trusted_with_event():
    """Exact-match persistence uses trusted contract + governance event."""
    project = ProjectFactory()
    user = project.owner
    TaskFactory(project=project, activity_code="E1E-ALIGN")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-E1E-ALIGN",
        properties={"Castor.Activity ID": "E1E-ALIGN"},
    )
    preview = MatchPreviewService(project).preview("Activity ID")
    approval = MatchApprovalRequest(
        preview_fingerprint=preview.preview_fingerprint,
        param_name="Activity ID",
        expected_matched_task_count=preview.matched_task_count,
        expected_projected_binding_count=preview.projected_binding_count,
        confirmation="APPROVE",
        confirm_acknowledged=True,
    )

    result = ApprovedMatchPersistenceService(project, user).persist(approval)

    assert result.inserted_accepted_bindings == 1
    binding = TaskEntityBinding.objects.get(entity_global_id="GID-E1E-ALIGN")
    assert is_trusted_binding(binding)
    assert (
        BindingGovernanceEvent.objects.filter(
            project=project, binding=binding, event_type="approved"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_trusted_filter_excludes_proposed_bindings():
    """Viewer/look-ahead trusted filter excludes proposed-only rows."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    IFCEntityFactory(ifc_file__project=project, global_id="GID-T")
    IFCEntityFactory(ifc_file__project=project, global_id="GID-P")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-T",
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-P",
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        is_active=True,
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
    )
    # Broken legacy: needs_review=False without trusted status (bypass save() promote)
    broken = TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-BROKEN",
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        is_active=True,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )
    TaskEntityBinding.objects.filter(pk=broken.pk).update(
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
    )

    trusted = list(TaskEntityBinding.objects.filter(task__project=project).filter(trusted_filter()))
    assert len(trusted) == 1
    assert trusted[0].entity_global_id == "GID-T"


@pytest.mark.django_db
def test_create_trusted_bindings_helper_sets_status_on_bulk_create():
    """bulk_create path sets governance_status=trusted explicitly."""
    project = ProjectFactory()
    user = project.owner
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-CREATE")

    result = create_trusted_bindings(
        project=project,
        user=user,
        specs=[
            {
                "task_id": str(task.pk),
                "entity_global_id": entity.global_id,
                "confidence": 1.0,
                "link_method": TaskEntityBinding.LinkMethod.MANUAL,
            }
        ],
        request_source="test_create",
    )

    assert result.promoted == 1
    binding = TaskEntityBinding.objects.get(task=task, entity_global_id=entity.global_id)
    assert is_trusted_binding(binding)


@pytest.mark.django_db
def test_pipeline_review_page_has_single_bulk_governance_cta(client):
    """Pipeline review renders one Governance bulk CTA — no duplicate approve buttons."""
    project = ProjectFactory()
    user = project.owner
    client.force_login(user)
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-UI")
    _review_binding(task, entity.global_id, confidence=0.99)

    url = reverse("scheduling:review", kwargs={"pk": project.pk})
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    assert html.count("Approve ≥95% in Governance") == 1
    assert "Bulk accept ≥95%" not in html
    assert (
        html.count('hx-post="' + reverse("scheduling:binding_bulk_accept", args=[project.pk])) == 1
    )
    # Single-row approve remains once per proposed row
    assert html.count("Approve as trusted") == 1
