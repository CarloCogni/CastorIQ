# scheduling/tests/test_links_workspace_v1.py
"""Links Workspace Polish V1 — layout, wording, playback ownership split."""

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
    """Links tab exposes toolbar, inspector, suggested/applied sections."""
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
    assert 'data-testid="links-selected-activity"' in html
    assert 'data-testid="links-task-empty"' in html
    assert 'data-testid="links-suggested-section"' in html
    assert 'data-testid="links-applied-section"' in html
    assert 'data-testid="links-center-empty"' in html
    assert 'data-testid="links-model-context"' in html
    assert 'data-testid="links-model-context-primary"' in html
    assert "Selected Activity" in html
    assert "Suggested Links" in html
    assert "Applied Links" in html
    assert "Model Context" in html
    assert "Unlinked Activities" in html
    assert "Link Coverage" in html
    assert "Confirm Link" in html
    assert "Ignore Suggestion" in html
    assert "prefers-reduced-motion" in html
    assert "lw-card-enter" in html
    assert "Suggest Links does not confirm links" in html
    # Model Context is the primary center zone; Suggested/Applied live in the inspector
    primary_center = html.split('data-testid="links-model-context-primary"', 1)[1].split(
        'data-testid="links-selected-activity"', 1
    )[0]
    assert 'data-testid="links-model-context"' in primary_center
    assert 'data-testid="links-suggested-section"' not in primary_center
    inspector = html.split('data-testid="links-selected-activity"', 1)[1].split(
        'id="fd-timeline-section"', 1
    )[0]
    assert 'data-testid="links-suggested-section"' in inspector
    assert 'data-testid="links-applied-section"' in inspector
    assert 'data-testid="links-center-empty"' in inspector
    # Viewer is not capped as a tiny bottom preview
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
    # Playback bar demoted/hidden — Time View owns simulation
    assert 'id="fd-timeline-section"' in html
    assert "display: none !important" in html or "display:none !important" in html
    assert 'data-testid="links-advanced-tools"' not in html
    # Visible toolbar must not advertise Play as a primary control label in toolbar
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert re.search(r">\s*Play\s*<", toolbar) is None
    assert re.search(r">\s*Pause\s*<", toolbar) is None
    # Primary workspace panels (exclude hidden timeline stubs kept for compat)
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
    """Normal Links page has no advanced Applied Links / diagnostics console."""
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
    assert "Review queue" not in html
    assert 'data-testid="links-advanced-tools"' not in html
    assert 'data-testid="fourd-link-quality-tab"' not in html
    assert 'id="lw-advanced"' not in html
    assert 'id="fd-gov-pane"' not in html
    assert 'id="fourD-bottom-panels"' not in html
    assert reverse("scheduling:link_governance_workspace", args=[project.pk]) not in html
    assert 'data-testid="suggest-links-btn"' in html
    assert 'data-testid="links-suggest-results-slot"' in html


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
    # Diagnostics details must not be open by default
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
    assert "Applied / Confirmed" in html
    assert "Link lifecycle" in html
    assert "Link Coverage" in html
    assert "Trust state" not in html
    assert "Destructive ops require owner" not in html
    assert "Your access:" not in html
    assert "Trusted bindings created before E2-E" not in html
    # Policy ids only inside demoted Advanced link details
    assert 'data-testid="link-diagnostics-extra"' in html
    extra = html.split('data-testid="link-diagnostics-extra"', 1)[1]
    assert "open" not in extra.split(">", 1)[0]
    assert "trusted-binding-v1" in extra
    assert "governance-authority-v1" in extra


@pytest.mark.django_db
def test_time_view_owns_playback_toolbar(client):
    """Time View exposes playback controls (functional reuse of timeline intervals)."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk})
        + "?tab=lookahead&basis=nearest_linked&weeks=3"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="lookahead-trusted-caveat"' in html
    assert "applied / confirmed links" in html.lower()
    assert "la-week-chips" in html or "Look-ahead" in html
    assert 'data-testid="time-view-playback-toolbar"' in html
    assert 'data-testid="time-view-play-btn"' in html
    assert 'data-testid="time-view-pause-btn"' in html
    assert 'data-testid="time-view-scrubber"' in html
    assert "Play" in html
    assert "Pause" in html
    assert "Start" in html
    assert (
        "Playback controls are available when applied schedule-model links provide a timeline."
        in html
    )
