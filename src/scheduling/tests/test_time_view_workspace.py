# scheduling/tests/test_time_view_workspace.py
"""Time View — 3D programme playback with Playback Setup / Appearance honesty."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.tests.factories import TaskFactory

_FORBIDDEN_WORKSPACE = (
    "Suggest Links",
    "Suggested Links",
    "AI suggestions",
    "Generating candidates",
    "Review queue",
    "Trust state",
    "link proposals",
    "trusted links",
    "company cost",
    "earned value",
    "Synchro",
    "Castor Simulator",
    "Look-ahead window",
    "2 weeks",
    "3 weeks",
    "4 weeks",
    "6 weeks",
    "Starting (",
    "In Progress (",
    "Finishing (",
    "Gantt",
    "Export animation",
    "Planned vs Actual",
    "Day/Week/Month",
    "Construction sets",
    "time-view-mode-construction",
    "time-view-legend-builder",
    "castor:appearance-colors",
    "CONSTRUCTION_SETS_URL",
)


@pytest.mark.django_db
def test_time_view_workspace_honesty_and_controls(client):
    """3D hero, Playback Setup duration, Schedule-state appearance, generated legend."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="time-view-title"' in html
    assert ">Time View<" in html.split('data-testid="time-view-title"', 1)[1][:80]
    assert 'data-testid="time-view-subtitle"' in html
    assert "Applied / Confirmed programme playback" in html
    assert "Programme playback" in html
    assert "Playback date" in html
    assert "applied / confirmed" in html.lower()
    assert "Model timeline" in html
    assert 'data-testid="time-view-viewport"' in html
    assert 'data-testid="time-view-playback-dock"' in html
    assert 'data-testid="time-view-dock-collapsed"' in html
    assert 'data-testid="time-view-dock-expanded"' in html
    assert 'data-testid="time-view-dock-expand"' in html
    assert 'data-testid="time-view-dock-collapse"' in html
    assert "tv-is-dock-collapsed" in html
    assert "tv-settings-collapsed" in html
    assert 'data-testid="time-view-settings"' in html
    assert "Playback settings" in html

    # Playback Setup
    assert 'data-testid="time-view-playback-setup"' in html
    assert "Playback Setup" in html
    assert 'data-testid="time-view-playback-duration"' in html
    assert ">30 sec<" in html
    assert ">1 min<" in html
    assert ">3 min<" in html
    assert ">5 min<" in html
    assert 'value="30000"' in html
    assert 'value="60000"' in html
    assert 'value="180000"' in html
    assert 'value="300000"' in html
    duration_block = html.split('data-testid="time-view-playback-duration"', 1)[1].split(
        "</select>", 1
    )[0]
    assert "Custom" not in duration_block
    assert 'data-testid="time-view-settings-range"' in html
    assert 'data-testid="time-view-playback-step"' in html
    assert "Weekly" in html.split('data-testid="time-view-playback-step"', 1)[1][:40]
    assert 'data-testid="time-view-speed"' not in html
    assert 'id="la-speed-select"' not in html
    assert 'id="la-settings-speed"' not in html

    # Appearance — Schedule state only; Task Legend Groups future; no IFC construction sets
    assert 'data-testid="time-view-appearance-setup"' in html
    assert 'data-testid="time-view-appearance-profile"' in html
    assert "Schedule state" in html.split('data-testid="time-view-appearance-profile"', 1)[1][:120]
    assert 'data-testid="time-view-mode-construction"' not in html
    assert 'data-testid="time-view-legend-builder"' not in html
    assert "castor:appearance-colors" not in html
    assert "CONSTRUCTION_SETS_URL" not in html
    assert 'data-testid="time-view-colour-basis"' in html
    assert (
        "schedule state" in html.split('data-testid="time-view-colour-basis"', 1)[1][:200].lower()
    )
    assert 'data-testid="time-view-task-legend-groups-future"' in html
    assert "Task Legend Groups" in html
    assert "Not available in this release" in html
    assert "not from IFC class" in html.lower() or "not from IFC class" in html
    assert 'data-testid="time-view-visibility"' in html
    assert 'data-testid="time-view-vis-complete"' in html
    assert 'data-bucket="complete"' in html
    assert "Reset playback colours" in html

    # Legend generated from schedule-state appearance profile
    assert 'data-testid="time-view-legend"' in html
    assert 'data-testid="time-view-legend-body"' in html
    assert 'data-testid="time-view-settings-legend"' in html
    assert "Legend · Schedule state" in html
    assert "_renderLegend" in html
    assert "APPEARANCE_BUCKETS" in html
    assert "_stepDelayMs" in html
    assert "TL_MIN_STEP_MS" in html
    assert 'data-testid="time-view-applied-status"' in html
    assert "No linked elements coloured for this date." in html
    assert "castor:timeline-applied" in html
    assert 'data-testid="time-view-play-btn"' in html
    assert 'data-testid="time-view-dock-play-btn"' in html
    assert 'aria-label="Play"' in html
    assert 'data-testid="time-view-scrubber"' in html
    assert "Loading model state…" in html or "Loading model state" in html
    assert "tv-workspace" in html
    assert "overflow: hidden" in html
    assert 'data-testid="time-view-lookahead-list"' not in html
    assert 'id="la-week-chips"' not in html
    assert (
        "This Week"
        not in html.split('data-testid="time-view-workspace"', 1)[1].split(
            'id="lookaheadHelpModal"', 1
        )[0]
    )
    assert 'data-testid="time-view-details-drawer"' in html
    assert "hidden" in html.split('data-testid="time-view-details-drawer"', 1)[1][:80]
    assert "hidden" in html.split('data-testid="time-view-settings"', 1)[1][:60]

    stage_idx = html.find('data-testid="time-view-preview"')
    dock_idx = html.find('data-testid="time-view-playback-dock"')
    assert stage_idx != -1 and dock_idx != -1 and stage_idx < dock_idx

    workspace = html.split('data-testid="time-view-workspace"', 1)[1].split(
        'id="lookaheadHelpModal"', 1
    )[0]
    help_html = html.split('id="lookaheadHelpLabel"', 1)[1].split("<style>", 1)[0]
    assert "TimeLiner" not in help_html
    assert "link proposals" not in help_html
    assert "Walkthrough / camera-path playback is not available" in help_html
    assert "This is not a walkthrough or construction simulation." in help_html
    assert "programme playback" in help_html.lower()
    assert "applied / confirmed" in help_html.lower()
    assert "Playback Setup" in help_html
    assert "Appearance" in help_html
    assert "Schedule state" in help_html
    assert "Task Legend Groups" in help_html
    assert "Construction sets" not in help_html
    for phrase in _FORBIDDEN_WORKSPACE:
        assert phrase not in workspace, phrase
        assert phrase not in help_html, phrase

    hub = html.split('data-testid="hub-time-view"', 1)[0][-200:]
    assert "tab=lookahead" in html
    assert "basis=" not in html.split('data-testid="hub-time-view"', 1)[0][-400:]
    assert hub


@pytest.mark.django_db
def test_time_view_nav_link_omits_ignored_basis(client):
    """Generated Time View href is tab=lookahead only."""
    project = ProjectFactory()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()
    href = html.split('data-testid="hub-time-view"', 1)[0]
    assert "?tab=lookahead" in href[-500:]
    assert "nearest_linked" not in html.split('data-testid="hub-primary-nav"', 1)[1][:4000]
