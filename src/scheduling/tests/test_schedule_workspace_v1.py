# scheduling/tests/test_schedule_workspace_v1.py
"""Schedule Workspace Polish V1 — layout markers, mode exclusivity, honesty."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from scheduling.tests.factories import TaskFactory

_REQUIRED_HUB = ("Schedule", "Links", "Model", "Time View", "Quantities", "Controls")
_FORBIDDEN_AFFIRM = (
    "Company actual cost",
    "ERP actual",
    "invoice actual",
    "QS valuation",
    "BOQ commercial",
)


@pytest.mark.django_db
def test_schedule_workspace_v1_layout_with_tasks(client):
    """With imported tasks: Gantt default, demoted import, Info panel, no stacked table."""
    project = ProjectFactory()
    TaskFactory(project=project, name="WS Task A")
    client.force_login(project.owner)

    url = reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    for label in _REQUIRED_HUB:
        assert label in html

    assert 'data-testid="data-sources-purpose"' in html
    assert "Imported schedule and provenance" in html
    assert 'data-testid="schedule-workspace-toolbar"' in html
    assert 'data-testid="schedule-view-toggle"' in html
    assert 'data-testid="schedule-gantt-view"' in html
    assert 'data-testid="schedule-table-view"' in html
    assert 'data-testid="schedule-table-mode-btn"' in html
    assert 'data-default-view="gantt"' in html
    assert 'data-testid="schedule-task-table"' in html
    assert 'data-testid="schedule-gantt-loading"' in html

    # Default markup: Gantt active, Table hidden (mutual exclusion classes)
    assert "ds-view-active" in html
    assert "ds-view-hidden" in html
    assert 'id="ds-view-gantt-wrap"' in html
    assert "ds-view-active" in html.split('id="ds-view-gantt-wrap"', 1)[1].split(">", 1)[0]
    assert "ds-view-hidden" in html.split('id="ds-view-table-wrap"', 1)[1].split(">", 1)[0]
    assert (
        'style="display:none; min-height:0;"' in html
        or "display:none" in html.split('id="ds-view-table-wrap"', 1)[1][:200]
    )

    # Import demoted when tasks exist
    assert 'data-testid="schedule-import-demoted-btn"' in html
    assert 'data-testid="schedule-import-modal"' in html
    assert 'data-testid="schedule-import-prominent"' not in html
    assert "Import / Replace Schedule" in html

    # Side panel + readiness
    assert 'data-testid="schedule-side-panel"' in html
    assert 'data-testid="schedule-info-panel"' in html
    assert "Schedule Info" in html
    assert "Schedule Readiness" in html
    assert "Data Readiness" in html
    assert "Planned assignment cost" in html
    assert "Assignment actual cost indicator" in html
    assert "enables CPI" not in html
    assert "enables CV" not in html
    assert "enables EAC" not in html
    assert "enables VAC" not in html
    assert "enables ETC" not in html
    assert "enables TCPI" not in html
    assert "CPI, CV, EAC, VAC disabled" not in html
    assert "diagnostic only" in html
    assert "company-cost metrics remain unavailable" in html
    assert "Select an activity from the Gantt or Table to inspect dates, progress" in html
    assert "and schedule details" in html
    assert 'data-testid="schedule-task-empty"' in html
    assert 'data-testid="schedule-task-inspector"' in html
    # Schedule-focused inspector sections (in JS template)
    assert "Task Identity" in html
    assert "schedule-insp-header" in html
    assert "schedule-insp-dates" in html
    assert "schedule-insp-progress" in html
    assert "schedule-insp-classification" in html
    assert "Schedule Details" in html
    assert "Progress detail unavailable" in html
    # No link / 4D CTA wording in Schedule inspector
    assert "schedule-insp-notes" not in html
    assert "Use Links" not in html
    assert "model bindings" not in html
    assert "4D Link Context" not in html
    assert "schedule-insp-link-context" not in html
    assert "schedule-insp-actions" not in html
    assert "Review in Links" not in html
    assert "Open Time View" not in html
    assert "Link status" not in html
    assert "Linked elements" not in html
    assert "Open in Links" not in html

    for phrase in _FORBIDDEN_AFFIRM:
        assert phrase not in html, f"forbidden affirmative wording: {phrase!r}"

    # Mapping table not the default stacked hero; schedule task table exists for Table mode
    assert 'id="ds-table-section" style="display:none;' in html
    assert 'id="ds-gantt-section"' in html
    assert 'id="ds-schedule-task-table"' in html

    # Empty hint must not be the default visible message when tasks exist
    empty_block = html.split('id="ds-table-empty-hint"', 1)[1][:180]
    assert "display:none" in empty_block

    # Columns control present and wired (dropdown, not a dead lone button)
    assert 'data-testid="schedule-columns-btn"' in html
    assert 'id="ds-columns-menu"' in html
    assert "ds-columns-btn" in html

    # Table mode cleanup markers (no Gantt chrome / import summary strip by default)
    assert "ds-schedule-task-head" in html
    assert "ds-schedule-task-scroll" in html
    table_markup = html.split('id="ds-schedule-task-table"', 1)[1].split("</table>", 1)[0]
    assert "table-dark" not in table_markup
    summary_tag = html.split('id="ds-summary-bar"', 1)[1].split(">", 1)[0]
    assert "ds-summary-visible" not in summary_tag
    assert 'data-testid="schedule-import-summary"' in html
    assert "ds-view-hidden" in html


@pytest.mark.django_db
def test_schedule_workspace_v1_mode_markers_exclusive(client):
    """Gantt/Table panes ship with exclusive active/hidden markers for JS."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    assert response.status_code == 200

    gantt_tag = html.split('id="ds-view-gantt-wrap"', 1)[1].split(">", 1)[0]
    table_tag = html.split('id="ds-view-table-wrap"', 1)[1].split(">", 1)[0]
    assert "ds-view-active" in gantt_tag
    assert "ds-view-hidden" not in gantt_tag
    assert "ds-view-hidden" in table_tag
    assert "ds-view-active" not in table_tag
    assert 'data-active-view="gantt"' in html
    assert 'data-active-view="table"' in html
    # Hidden pane CSS must neutralize sticky Gantt layers (no leftover strip in Table)
    assert "#ds-gantt-hdr" in html or "ds-gantt-hdr" in html
    assert "visibility: hidden" in html or "visibility:hidden" in html
    assert "ds-schedule-task-scroll" in html


@pytest.mark.django_db
def test_schedule_workspace_v1_task_inspector_markup(client):
    """Selected Task inspector is schedule-scoped — no 4D link CTA block."""
    project = ProjectFactory()
    TaskFactory(project=project, name="Inspector Task")
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    assert response.status_code == 200

    assert 'data-testid="schedule-selected-task"' in html
    assert 'data-testid="schedule-task-empty"' in html
    assert "Select an activity from the Gantt or Table to inspect dates, progress" in html
    assert "and schedule details" in html
    assert "4D link context" not in html.lower()
    assert "Task Identity" in html
    assert "schedule-insp-dates" in html
    assert "schedule-insp-progress" in html
    assert "Schedule Details" in html
    assert "schedule-insp-classification" in html
    assert "schedule-insp-notes" not in html
    assert "Use Links" not in html
    assert "model bindings" not in html
    assert "4D Link Context" not in html
    assert "schedule-insp-link-context" not in html
    assert "schedule-insp-actions" not in html
    assert "Review in Links" not in html
    assert "Open Time View" not in html
    assert "Link status" not in html
    assert "Linked elements" not in html
    assert "Company actual cost" not in html
    assert "ERP actual" not in html
    assert "QS valuation" not in html


@pytest.mark.django_db
def test_schedule_workspace_v1_empty_shows_prominent_import(client):
    """Empty project keeps import prominent (not demoted to modal)."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="schedule-import-prominent"' in html
    assert 'data-testid="schedule-import-demoted-btn"' not in html
    assert 'data-testid="schedule-workspace-toolbar"' in html
    assert 'data-testid="schedule-gantt-view"' in html
    # No loading spinner for empty projects
    loading_tag = html.split('id="ds-gantt-loading"', 1)[1].split(">", 1)[0]
    assert "display:none" in loading_tag or "display: none" in loading_tag
