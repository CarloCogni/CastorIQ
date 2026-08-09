# scheduling/tests/test_links_workspace_v1.py
"""Links Workspace Polish V1 — layout, wording, playback ownership split."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.tests.factories import TaskFactory

_FORBIDDEN_PRIMARY = (
    "Governance",
    "Authority",
    "Quality Gate",
    "Destructive ops require owner",
    "governance-authority-v1",
    "trusted-binding-v1",
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
    assert "Selected Activity" in html
    assert "Suggested Links" in html
    assert "Applied Links" in html
    assert "Unlinked Activities" in html
    assert "Link Coverage" in html
    assert "Confirm Link" in html
    assert "Ignore Suggestion" in html
    assert "prefers-reduced-motion" in html
    assert "lw-card-enter" in html
    assert "Select an activity to review suggested and applied model links." in html


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
    assert 'data-testid="links-advanced-tools"' in html
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
    assert (
        'id="la-slider"'
        not in html.split('data-testid="links-workspace"', 1)[1].split(
            'data-testid="links-advanced-tools"', 1
        )[0]
    )
    assert 'aria-hidden="true"' in html.split('id="fd-timeline-section"', 1)[1][:200]


@pytest.mark.django_db
def test_links_workspace_v1_avoids_governance_primary_chrome(client):
    """Default Links markup does not surface governance/approval product chrome."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    # Primary workspace (outside demoted details) should not advertise governance
    primary = html.split('data-testid="links-advanced-tools"', 1)[0]
    for phrase in _FORBIDDEN_PRIMARY:
        assert phrase not in primary, f"forbidden primary chrome: {phrase!r}"
    assert re.search(r">\s*Approve\s*<", primary) is None
    assert re.search(r">\s*Reject\s*<", primary) is None
    assert "Review queue" not in primary
    assert "Advanced link tools" not in primary
    assert "when you have permission" not in primary
    assert "links-confirm-hint" not in primary


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
