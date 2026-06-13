# scheduling/tests/test_governance_authority_metrics.py
"""E2-F governance authority policy and scorecard tests."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import BindingGovernanceEvent, TaskEntityBinding
from scheduling.services.governance.authority import (
    GovernanceAuthorityError,
    GovernanceAuthorityPolicy,
    GovernanceCapability,
)
from scheduling.services.governance.binding_lifecycle import BindingLifecycleService
from scheduling.services.governance.governance_overview import GovernanceOverviewService
from scheduling.services.governance.lifecycle_vocabulary import REVERSE_CONFIRM_PHRASE
from scheduling.services.governance.link_decision import LinkDecisionService
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review: bool = True, method=None):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=0.8,
        link_method=method or TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=needs_review,
    )


@pytest.mark.django_db
def test_viewer_can_view_cannot_approve():
    """Viewer can read governance but cannot approve."""
    project = ProjectFactory()
    viewer = UserFactory()
    ProjectMembershipFactory(project=project, user=viewer, permission="viewer")
    policy = GovernanceAuthorityPolicy(project, viewer)
    assert policy.check(GovernanceCapability.VIEW_GOVERNANCE).allowed
    assert not policy.check(GovernanceCapability.APPROVE_INDIVIDUAL).allowed


@pytest.mark.django_db
def test_editor_can_approve_reject_reaffirm():
    """Editor (reviewer tier) can approve individual, reject, reaffirm."""
    project = ProjectFactory()
    editor = UserFactory()
    ProjectMembershipFactory(project=project, user=editor, permission="editor")
    policy = GovernanceAuthorityPolicy(project, editor)
    assert policy.check(GovernanceCapability.APPROVE_INDIVIDUAL).allowed
    assert policy.check(GovernanceCapability.REJECT).allowed
    assert policy.check(GovernanceCapability.REAFFIRM).allowed


@pytest.mark.django_db
def test_editor_cannot_reverse_or_bulk():
    """Editor cannot reverse, supersede, or bulk approve."""
    project = ProjectFactory()
    editor = UserFactory()
    ProjectMembershipFactory(project=project, user=editor, permission="editor")
    policy = GovernanceAuthorityPolicy(project, editor)
    assert not policy.check(GovernanceCapability.REVERSE).allowed
    assert not policy.check(GovernanceCapability.SUPERSEDE).allowed
    assert not policy.check(GovernanceCapability.APPROVE_BULK).allowed


@pytest.mark.django_db
def test_owner_can_destructive_ops():
    """Owner has senior authority for bulk, reverse, supersede, M2M remove."""
    project = ProjectFactory()
    owner = project.owner
    policy = GovernanceAuthorityPolicy(project, owner)
    assert policy.check(GovernanceCapability.APPROVE_BULK).allowed
    assert policy.check(GovernanceCapability.REVERSE).allowed
    assert policy.check(GovernanceCapability.REPAIR_M2M_REMOVE).allowed


@pytest.mark.django_db
def test_cross_project_authority_blocked():
    """User with membership on another project cannot act on this project."""
    project = ProjectFactory()
    other = ProjectFactory()
    user = UserFactory()
    ProjectMembershipFactory(project=other, user=user, permission="editor")
    policy = GovernanceAuthorityPolicy(project, user)
    assert not policy.check(GovernanceCapability.APPROVE_INDIVIDUAL).allowed


@pytest.mark.django_db
def test_superuser_override():
    """Superuser receives explicit override capability."""
    project = ProjectFactory()
    admin = UserFactory(is_superuser=True)
    policy = GovernanceAuthorityPolicy(project, admin)
    result = policy.check(GovernanceCapability.REVERSE)
    assert result.allowed
    assert result.authority_source == "superuser_override"


@pytest.mark.django_db
def test_service_enforces_reverse_for_editor():
    """BindingLifecycleService blocks editor reverse at service layer."""
    project = ProjectFactory()
    editor = UserFactory()
    ProjectMembershipFactory(project=project, user=editor, permission="editor")
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-RV", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    svc = BindingLifecycleService(project, editor)
    preview = svc.preview_reverse(str(binding.pk))
    with pytest.raises(GovernanceAuthorityError):
        svc.reverse(
            str(binding.pk),
            fingerprint=preview.fingerprint,
            reason_code="mistaken_approval",
            reason_text="",
            confirmation=REVERSE_CONFIRM_PHRASE,
        )


@pytest.mark.django_db
def test_link_decision_bulk_requires_owner():
    """Bulk approval raises authority error for editor."""
    project = ProjectFactory()
    editor = UserFactory()
    ProjectMembershipFactory(project=project, user=editor, permission="editor")
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-B1")
    task = TaskFactory(project=project)
    b1 = _bind(task, entity.global_id, needs_review=True)
    svc = LinkDecisionService(project, editor)
    preview = svc.preview_selected([str(b1.pk), str(b1.pk)])
    with pytest.raises(GovernanceAuthorityError):
        svc.approve_selected(
            [str(b1.pk)],
            selection_fingerprint=preview.selection_fingerprint,
            require_bulk_phrase=True,
            confirmation="APPROVE SELECTED",
            confirm_acknowledged=True,
        )


@pytest.mark.django_db
def test_overview_scorecard_separates_trust_states():
    """Scorecard reports trusted and review counts separately."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    _bind(task, "GID-R", needs_review=True)
    payload = GovernanceOverviewService(project).build(project.owner, None)
    assert payload["scorecard"]["trust_state"]["trusted"]["value"] == 1
    assert payload["scorecard"]["trust_state"]["active_review"]["value"] == 1


@pytest.mark.django_db
def test_task_coverage_denominator_explicit():
    """Task coverage uses all schedule tasks as denominator."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    TaskFactory.create_batch(3, project=project)
    cov = GovernanceOverviewService(project).build(project.owner, None)["scorecard"]["coverage"]
    assert cov["task_coverage"]["numerator"] == 1
    assert cov["task_coverage"]["denominator"] == 4
    assert "all schedule activities" in cov["task_coverage"]["caveat"].lower()


@pytest.mark.django_db
def test_pre_audit_caveat_in_overview():
    """Overview includes pre-audit legacy caveat and zero events."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-L", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    payload = GovernanceOverviewService(project).build(project.owner, None)
    assert payload["scorecard"]["decision_activity"]["total_events"]["value"] == 0
    assert "pre-audit" in payload["audit_boundary"]["pre_audit_legacy_caveat"].lower()


@pytest.mark.django_db
def test_methodology_fields_present():
    """Overview exposes methodology registry entries."""
    project = ProjectFactory()
    payload = GovernanceOverviewService(project).build(project.owner, None)
    assert len(payload["methodology"]) >= 5
    assert payload["methodology"][0]["numerator_definition"]


@pytest.mark.django_db
def test_overview_query_count_bounded():
    """Overview uses bounded queries without full reconciliation scan."""
    project = ProjectFactory()
    for i in range(5):
        task = TaskFactory(project=project)
        _bind(task, f"GID-{i}", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    with CaptureQueriesContext(connection) as ctx:
        GovernanceOverviewService(project).build(project.owner, None)
    assert len(ctx.captured_queries) <= 30


@pytest.mark.django_db
def test_overview_endpoint_get_only(client):
    """Overview endpoint is read-only GET."""
    project = ProjectFactory()
    client.force_login(project.owner)
    url = reverse("scheduling:link_governance_overview", args=[project.pk])
    assert client.get(url).status_code == 200
    assert client.post(url).status_code == 405


@pytest.mark.django_db
def test_viewer_bulk_preview_403(client):
    """Editor bulk preview returns 403 for viewer."""
    project = ProjectFactory()
    viewer = UserFactory()
    ProjectMembershipFactory(project=project, user=viewer, permission="viewer")
    task = TaskFactory(project=project)
    binding = _bind(task, "GID-V", needs_review=True)
    client.force_login(viewer)
    url = reverse("scheduling:link_decision_bulk_preview", args=[project.pk])
    response = client.post(url, {"binding_ids": [str(binding.pk)]})
    assert response.status_code == 403


@pytest.mark.django_db
def test_no_hardcoded_roles_in_lifecycle_service():
    """Lifecycle service imports authority policy, not role name strings."""
    import inspect

    from scheduling.services import governance

    src = inspect.getsource(governance.binding_lifecycle)
    assert "ProjectRole" not in src
    assert "facilitiesmanager" not in src.lower()
    assert "GovernanceAuthorityPolicy" in src


@pytest.mark.django_db
def test_audit_events_prospective_only():
    """Governance events count reflects append-only prospective audit."""
    project = ProjectFactory()
    assert BindingGovernanceEvent.objects.filter(project=project).count() == 0
