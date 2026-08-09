# scheduling/tests/test_links_workspace_v1.py
"""Links Workspace — practical manual linking surface (no suggestion UX)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.tests.factories import TaskFactory

_FORBIDDEN_LINKS_SURFACE = (
    "Governance",
    "Authority",
    "Quality Gate",
    "Destructive ops require owner",
    "governance-authority-v1",
    "trusted-binding-v1",
    "More linking details",
    "Advanced link details",
    "Applied Links workspace",
    "Suggested Links queue",
    "Reconciliation",
    "Audit history",
    "Legacy M2M",
    "Multi-applied",
    "Exact full preview",
    "Link Proposals",
    "Trust state",
    "Your access:",
    "Conf min",
    "IFC class",
    "Suggest Links",
    "Suggested Links",
    "Generating candidates",
    "Ignore Suggestion",
    "AI suggestions",
    "Review queue",
)

_FORBIDDEN_ADVANCED_LANDING = (
    "Trust state",
    "Destructive ops require owner",
    "Your access: none",
    "Trusted bindings",
    "governance-authority-v1",
    "trusted-binding-v1",
    "e2-f-v1",
    "E2-E",
)


@pytest.mark.django_db
def test_links_workspace_v1_layout_markers(client):
    """Links tab exposes toolbar, search, model context, applied inspector."""
    project = ProjectFactory()
    TaskFactory(project=project, name="Link WS Task")
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-workspace"' in html
    assert 'data-testid="links-workspace-toolbar"' in html
    assert 'data-testid="links-activities"' in html
    assert 'data-testid="links-activity-search"' in html
    assert 'data-testid="links-selected-activity"' in html
    assert 'data-testid="links-task-empty"' in html
    assert 'data-testid="links-applied-section"' in html
    assert 'data-testid="links-center-empty"' in html
    assert 'data-testid="links-model-context"' in html
    assert 'data-testid="links-model-context-primary"' in html
    assert "Selected Activity" in html
    assert "Applied Links" in html or "Applied / Confirmed" in html
    assert "Model Context" in html
    assert "Unlinked Activities" in html
    assert "Link Coverage" in html
    assert "Search activities" in html
    assert 'data-testid="links-suggested-section"' not in html
    assert 'data-testid="suggest-links-btn"' not in html
    assert 'data-testid="links-filter-suggested"' not in html
    assert 'data-filter="needs_review"' not in html
    assert "Suggested Links" not in html
    assert "Suggest Links" not in html
    assert "Ignore Suggestion" not in html
    # Modal keeps Link Element for viewer-driven element→task; no suggestion Confirm
    assert 'data-testid="links-confirm-link-btn"' not in html
    assert "prefers-reduced-motion" in html
    assert "lw-card-enter" in html
    primary_center = html.split('data-testid="links-model-context-primary"', 1)[1].split(
        'data-testid="links-selected-activity"', 1
    )[0]
    assert 'data-testid="links-model-context"' in primary_center
    assert 'data-testid="links-suggested-section"' not in primary_center
    inspector = html.split('data-testid="links-selected-activity"', 1)[1].split(
        'id="fd-timeline-section"', 1
    )[0]
    assert 'data-testid="links-applied-section"' in inspector
    assert 'data-testid="links-center-empty"' in inspector
    assert "max-height: 240px" not in html
    assert "height: 200px" not in html.split("lw-model-context", 1)[1].split("@media", 1)[0]


@pytest.mark.django_db
def test_links_workspace_v1_no_primary_playback_chrome(client):
    """Play/Pause/Start simulation controls are not primary Links chrome."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'id="fd-timeline-section"' in html
    assert "display: none !important" in html or "display:none !important" in html
    assert 'data-testid="links-advanced-tools"' not in html
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert re.search(r">\s*Play\s*<", toolbar) is None
    assert re.search(r">\s*Pause\s*<", toolbar) is None
    body = html.split('data-testid="links-workspace-body"', 1)[1].split(
        'id="fd-timeline-section"', 1
    )[0]
    assert re.search(r">\s*Play\s*<", body) is None
    assert re.search(r">\s*Pause\s*<", body) is None
    assert 'data-testid="time-view-playback-toolbar"' not in body
    assert 'id="la-slider"' not in html.split('data-testid="links-workspace"', 1)[1]
    assert 'aria-hidden="true"' in html.split('id="fd-timeline-section"', 1)[1][:200]


@pytest.mark.django_db
def test_links_workspace_v1_simple_surface_no_advanced_console(client):
    """Normal Links page has no suggestion UX and no advanced console."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    for phrase in _FORBIDDEN_LINKS_SURFACE:
        assert phrase not in html, f"forbidden Links surface chrome: {phrase!r}"
    assert re.search(r">\s*Approve\s*<", html) is None
    assert re.search(r">\s*Reject\s*<", html) is None
    assert 'data-testid="links-advanced-tools"' not in html
    assert 'data-testid="fourd-link-quality-tab"' not in html
    assert 'id="lw-advanced"' not in html
    assert 'id="fd-gov-pane"' not in html
    assert 'id="fourD-bottom-panels"' not in html
    assert reverse("scheduling:link_governance_workspace", args=[project.pk]) not in html
    assert 'data-testid="suggest-links-btn"' not in html
    assert 'data-testid="links-suggest-form"' not in html
    assert 'data-testid="links-suggest-status"' not in html
    assert 'id="fourD-link-results"' not in html
    assert 'data-testid="links-count-suggested"' not in html
    assert "Manual element linking is not available in this workspace yet." in html
    assert 'data-testid="links-activity-search"' in html
    assert 'data-filter="linked"' in html
    assert 'data-filter="unlinked"' in html
    assert 'data-filter="needs_review"' not in html


@pytest.mark.django_db
def test_task_detail_applied_only_no_suggestion_actions(client):
    """Task detail shows applied links only — no suggestion cards or Confirm/Ignore."""
    from ifc_processor.tests.factories import IFCEntityFactory
    from scheduling.models import TaskEntityBinding

    project = ProjectFactory()
    task = TaskFactory(project=project, name="Applied Task")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-APPLIED-1",
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=False,
    )
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-APPLIED-1",
        name="Wall-Applied",
        ifc_type="IfcWall",
    )
    # Review binding must not surface on normal Links task detail
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-REVIEW-1",
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:task_detail", kwargs={"pk": project.pk, "task_pk": task.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-applied-card"' in html
    assert "Wall-Applied" in html
    assert 'data-testid="links-suggestion-card"' not in html
    assert 'data-testid="links-confirm-link-btn"' not in html
    assert 'data-testid="links-ignore-suggestion-btn"' not in html
    assert "Suggested Links" not in html
    assert "Confirm Link" not in html
    assert "Ignore Suggestion" not in html


@pytest.mark.django_db
def test_task_detail_empty_applied_shows_manual_limitation(client):
    """Unlinked activity empty state is practical and does not invent Manual Link."""
    project = ProjectFactory()
    task = TaskFactory(project=project, name="Unlinked Task")
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:task_detail", kwargs={"pk": project.pk, "task_pk": task.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-applied-empty"' in html
    assert "No applied model links for this activity." in html
    assert 'data-testid="links-manual-link-limitation"' in html
    assert "Manual element linking is not available in this workspace yet." in html
    assert "Suggest Links" not in html
    assert 'data-testid="links-suggestion-card"' not in html
    assert re.search(r">\s*Manual Link\s*<", html) is None
    assert re.search(r">\s*Link Element\s*<", html) is None
    assert re.search(r">\s*Remove Link\s*<", html) is None


@pytest.mark.django_db
def test_applied_links_workspace_queue_first_no_trust_landing(client):
    """Standalone Applied Links workspace lands on queue; trust scorecard is demoted."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:link_governance_workspace", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-advanced-landing"' in html
    assert "Advanced link details are available here" in html
    assert "Link actions remain scoped to selected suggestions" in html
    assert 'data-testid="link-diagnostics"' in html
    assert 'id="gq-tab-queue"' in html
    assert "Suggested Links queue" in html
    assert "Confirm Link" in html or "Ignore Suggestion" in html or "Applied Links" in html
    for phrase in _FORBIDDEN_ADVANCED_LANDING:
        assert phrase not in html, f"forbidden advanced landing chrome: {phrase!r}"
    diagnostics = html.split('data-testid="link-diagnostics"', 1)[1][:200]
    assert "open" not in diagnostics.split(">", 1)[0]
    assert "gq-tab-overview" not in html


@pytest.mark.django_db
def test_link_diagnostics_overview_uses_product_wording(client):
    """Overview partial uses Applied / Confirmed labels, not trust/destructive chrome."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:link_governance_overview", kwargs={"pk": project.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Applied / Confirmed" in html or "Applied Links" in html
    assert "Destructive ops" not in html
    assert "Trust state" not in html
