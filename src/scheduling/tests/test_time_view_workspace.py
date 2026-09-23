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
    assert "Variance · Applied / Confirmed" in html
    assert "Programme playback for applied / confirmed schedule-model links" in html
    assert "Playback date" in html
    assert "applied / confirmed" in html.lower()
    # No IFC indexed in this fixture — the viewport says so instead of faking a model.
    assert "Upload and process an IFC to enable programme playback." in html
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

    # Appearance — Option-3 Planned / Actual / Variance modes; no IFC construction sets
    assert 'data-testid="time-view-appearance-setup"' in html
    assert 'data-testid="time-view-appearance-profile"' in html
    assert (
        "Planned / Actual / Variance"
        in html.split('data-testid="time-view-appearance-profile"', 1)[1][:160]
    )
    assert 'data-testid="time-view-mode-construction"' not in html
    assert 'data-testid="time-view-legend-builder"' not in html
    assert "castor:appearance-colors" not in html
    assert "CONSTRUCTION_SETS_URL" not in html
    assert 'data-testid="time-view-colour-basis"' in html
    basis = html.split('data-testid="time-view-colour-basis"', 1)[1][:400]
    assert "Colour basis follows the active mode on the playback date." in basis
    assert "Planned never claims actual completion." in basis
    assert "Actual never falls back to planned dates." in basis
    # The three modes are real dock buttons, not a construction-set legend builder.
    assert 'data-testid="time-view-mode-seg"' in html
    assert 'data-testid="time-view-mode-planned"' in html
    assert 'data-testid="time-view-mode-actual"' in html
    assert 'data-testid="time-view-mode-variance"' in html
    assert 'data-testid="time-view-mode-explain"' in html
    assert "Compares planned and actual evidence at the playback date." in html
    assert 'data-testid="time-view-visibility"' in html
    assert 'data-testid="time-view-vis-complete"' in html
    assert 'data-bucket="complete"' in html
    assert "Reset playback colours" in html

    # Legend generated from the active mode's canonical bucket list
    assert 'data-testid="time-view-legend"' in html
    assert 'data-testid="time-view-legend-body"' in html
    assert 'data-testid="time-view-settings-legend"' in html
    assert "Legend · Variance" in html
    assert "_renderLegend" in html
    assert "MODE_LEGENDS" in html
    assert "_legendForMode" in html
    # Legend labels are mode-owned copy, never re-derived from IFC class.
    assert "No linked schedule activity" in html
    assert "No actual dates recorded" in html
    assert "_stepDelayMs" in html
    assert "TL_MIN_STEP_MS" in html
    assert 'data-testid="time-view-applied-status"' in html
    assert "No linked elements coloured for this date." in html
    assert "castor:timeline-applied" in html
    assert 'data-testid="time-view-play-btn"' in html
    assert 'data-testid="time-view-dock-play-btn"' in html
    assert 'aria-label="Play"' in html
    assert 'data-testid="time-view-scrubber"' in html
    assert 'data-testid="time-view-dock-loading"' in html
    assert "Waiting for model colours…" in html
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
    assert "This is not a walkthrough, playlist of activities, or construction simulation." in (
        help_html
    )
    assert "programme playback" in help_html.lower()
    assert "applied / confirmed" in help_html.lower()
    assert "Programme Playback controls" in help_html
    assert "Full playback" in help_html
    # Option-3 modes are documented; construction-set legends are not a feature.
    assert "Modes" in help_html
    assert "<strong>Planned</strong>" in help_html
    assert "<strong>Actual</strong>" in help_html
    assert "<strong>Variance</strong>" in help_html
    assert "Construction sets" not in help_html
    assert "Task Legend Groups" not in help_html
    # Honesty: no fabricated actuals, no invented percentage claims.
    assert "does not invent history from status or percent-complete fields" in help_html
    assert "read-only playback view" in help_html
    for phrase in _FORBIDDEN_WORKSPACE:
        assert phrase not in workspace, phrase
        assert phrase not in help_html, phrase

    hub = html.split('data-testid="hub-time-view"', 1)[0][-200:]
    assert "tab=lookahead" in html
    assert "basis=" not in html.split('data-testid="hub-time-view"', 1)[0][-400:]
    assert hub


@pytest.mark.django_db
def test_time_view_viewport_embeds_viewer_when_ifc_indexed(client):
    """With a completed IFC the viewport is the real embed iframe, not the empty state."""
    from ifc_processor.tests.factories import IFCFileFactory

    project = ProjectFactory()
    TaskFactory(project=project)
    IFCFileFactory(project=project, status="completed")
    client.force_login(project.owner)

    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()

    viewport = html.split('data-testid="time-view-viewport"', 1)[1][:600]

    assert 'title="Time View model timeline"' in viewport
    assert reverse("ifc_viewer:viewer_embed", kwargs={"pk": project.pk}) in viewport
    # The empty state only survives as a JS warning string, never as rendered markup.
    assert "Upload and process an IFC to enable programme playback." not in viewport
    assert "const HAS_IFC       = true;" in html


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
