# scheduling/tests/test_links_workspace_v1.py
"""Links Workspace — rule-based Parameter Match / Link Check surface."""

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
    """Links tab exposes toolbar, rule setup, model context, match results."""
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
    assert "Applied / Confirmed" in html or "Applied Links" in html
    assert "Model Context" in html
    assert "Unlinked Activities" in html
    assert "Link Coverage" in html
    assert "Search activities" in html
    assert 'data-testid="links-rule-setup"' in html
    assert 'data-testid="links-parameter-match"' in html
    assert 'data-testid="links-run-link-check"' in html
    assert 'data-testid="links-match-results"' in html
    assert 'data-testid="links-help-pill"' in html
    assert 'data-testid="links-toggle-rules"' in html
    assert 'data-testid="links-toggle-results"' in html
    assert 'data-testid="links-toggle-inspector"' in html
    assert "lw-results-collapsed" in html
    assert "lw-rules-collapsed" in html
    assert "lw-inspector-open" in html
    assert 'data-testid="links-suggested-section"' not in html
    assert 'data-testid="suggest-links-btn"' not in html
    assert 'data-testid="links-filter-suggested"' not in html
    assert 'data-filter="needs_review"' not in html
    assert "Suggested Links" not in html
    assert "Suggest Links" not in html
    assert "Ignore Suggestion" not in html
    assert 'data-testid="links-confirm-link-btn"' not in html
    assert "prefers-reduced-motion" in html
    assert "lw-card-enter" in html
    primary_center = html.split('data-testid="links-model-context-primary"', 1)[1].split(
        'data-testid="links-selected-activity"', 1
    )[0]
    assert 'data-testid="links-model-context"' in primary_center
    assert 'data-testid="links-match-results"' not in primary_center
    assert 'data-testid="links-suggested-section"' not in primary_center
    assert 'data-zoom="day"' not in html
    assert 'data-zoom="week"' not in html
    assert ">Day<" not in html
    assert ">Week<" not in html
    assert ">Month<" not in html
    assert "height: calc(100vh - 140px)" not in html
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert 'data-testid="links-run-link-check"' in toolbar
    assert 'data-testid="links-activity-search"' in toolbar
    inspector = html.split('data-testid="links-selected-activity"', 1)[1].split(
        'id="fd-timeline-section"', 1
    )[0]
    assert 'data-testid="links-applied-section"' in inspector
    assert 'data-testid="links-center-empty"' in inspector
    assert 'data-testid="links-manual-fallback-heading"' in inspector
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
    assert "link_governance" not in html
    assert 'data-testid="suggest-links-btn"' not in html
    assert 'data-testid="links-suggest-form"' not in html
    assert 'data-testid="links-suggest-status"' not in html
    assert 'id="fourD-link-results"' not in html
    assert 'data-testid="links-count-suggested"' not in html
    assert 'data-testid="links-manual-actions"' in html
    assert 'data-testid="links-link-selected-element-btn"' in html
    assert 'data-testid="links-open-link-element-tool-btn"' in html
    assert 'data-testid="links-selected-element"' in html
    assert "Link Selected Element" in html
    assert "Open Link Element tool" in html
    assert "Manual fallback" in html
    assert "Manual element linking is not available in this workspace yet." not in html
    assert 'data-testid="links-activity-search"' in html
    assert 'data-filter="linked"' in html
    assert 'data-filter="unlinked"' in html
    assert 'data-filter="needs_review"' not in html
    assert "link-element" in html
    assert "unlink-element" in html


@pytest.mark.django_db
def test_links_rule_based_workspace_markers(client):
    """Rule setup, dry-run Link Check, and Match Results categories are present."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Link Rules" in html
    assert "Parameter Match" in html
    assert 'data-testid="links-schedule-field"' in html
    assert 'data-testid="links-model-param"' in html
    assert 'data-testid="links-model-param-note"' in html
    assert "Model parameter list is not indexed yet" in html
    assert 'data-testid="links-run-link-check"' in html
    assert "Run Link Check" in html
    assert 'data-testid="links-link-check-dry-run-note"' in html
    assert "Dry-run / preview only" in html
    assert 'data-testid="links-match-results"' in html
    assert 'data-testid="links-results-tab-matched"' in html
    assert 'data-testid="links-results-tab-unmatched-activities"' in html
    assert 'data-testid="links-results-tab-unmatched-elements"' in html
    assert 'data-testid="links-results-tab-conflicts"' in html
    assert "Matched" in html
    assert "Unmatched Activities" in html
    assert "Unmatched Model Elements" in html
    assert "Conflicts" in html
    assert 'data-testid="links-apply-links-btn"' in html
    assert "Apply Links" in html
    assert "Apply Preview Matches" in html
    assert 'data-testid="links-apply-links-warning"' in html
    assert "Applies the latest preview set" in html
    assert reverse("scheduling:schedule_link_preview_param", args=[project.pk]) in html
    assert reverse("scheduling:schedule_link_apply_approved_param", args=[project.pk]) in html
    assert reverse("scheduling:schedule_link_smart", args=[project.pk]) not in html
    assert 'data-testid="links-manual-fallback-heading"' in html
    left = html.split('data-testid="links-left-column"', 1)[1].split(
        'data-testid="links-model-context-primary"', 1
    )[0]
    assert 'data-testid="links-rule-setup"' not in left
    assert 'data-testid="links-activities"' in left
    assert 'data-testid="links-activity-list"' in left
    assert 'data-testid="links-manual-actions"' not in left
    assert 'data-testid="links-match-results"' not in left
    assert 'data-zoom="day"' not in left
    assert 'id="fd-gantt-timeline-hdr"' not in html


@pytest.mark.django_db
def test_links_option_b_three_panel_drawers(client):
    """Option B: activity list + full-height model + inspector; Rules/Results are drawers."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'class="lw-workspace lw-rules-collapsed lw-inspector-open lw-results-collapsed"' in html
    assert 'data-lw-results="collapsed"' in html
    assert 'data-lw-rules="collapsed"' in html
    assert 'data-lw-inspector="open"' in html
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert 'data-testid="links-run-link-check"' in toolbar
    assert 'data-testid="links-toggle-rules"' in toolbar
    assert 'data-testid="links-toggle-results"' in toolbar
    assert 'data-testid="links-toggle-inspector"' in toolbar
    assert 'data-testid="links-highlight-mode"' in toolbar
    assert 'data-testid="links-clear-highlight"' in html
    assert 'data-testid="links-activity-search"' in toolbar
    assert 'aria-expanded="false"' in html.split('data-testid="links-toggle-rules"', 1)[1][:180]
    assert 'aria-expanded="false"' in html.split('data-testid="links-toggle-results"', 1)[1][:180]
    assert 'data-testid="links-rule-summary"' in html
    assert "Activity Code → Activity ID · Exact" in html
    assert 'data-testid="links-rule-body"' in html
    assert 'data-testid="links-rules-close"' in html
    assert 'data-testid="links-results-collapse"' in html
    assert 'data-testid="links-inspector-collapse"' in html
    assert 'data-testid="links-inspector-rail"' in html
    assert 'data-testid="links-drawer-scrim"' in html
    assert "#fourD-root.lw-results-open #lw-match-results" in html
    assert "height: 100%" in html.split("#fourD-root.lw-workspace", 1)[1][:400]
    assert "calc(100vh - 140px)" not in html
    center = html.split('data-testid="links-model-context-primary"', 1)[1].split(
        'data-testid="links-selected-activity"', 1
    )[0]
    assert 'data-testid="links-model-context"' in center
    assert 'data-testid="links-match-results"' not in center
    assert 'data-testid="links-activities"' not in center
    inspector = html.split('data-testid="links-selected-activity"', 1)[1].split(
        'id="lw-drawer-scrim"', 1
    )[0]
    assert 'data-testid="links-manual-actions"' in inspector
    assert "Select a model element to enable Link Selected Element." in inspector
    assert "Preview only" in html
    assert "Dry-run" in html
    assert 'data-testid="links-apply-links-btn"' in html
    assert (
        "disabled" in html.split('data-testid="links-apply-links-btn"', 1)[0][-80:]
        or "disabled" in html.split('id="lw-apply-links-btn"', 1)[1][:120]
    )
    assert "Suggest Links" not in html
    assert "Suggested Links" not in html
    assert 'data-zoom="day"' not in html
    assert 'id="fd-gantt-timeline-hdr"' not in html


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
    assert 'data-testid="links-remove-link-btn"' in html
    assert "Remove Link" in html
    assert 'data-testid="links-suggestion-card"' not in html
    assert 'data-testid="links-confirm-link-btn"' not in html
    assert 'data-testid="links-ignore-suggestion-btn"' not in html
    assert "Suggested Links" not in html
    assert "Confirm Link" not in html
    assert "Ignore Suggestion" not in html


@pytest.mark.django_db
def test_task_detail_empty_applied_shows_manual_guidance(client):
    """Unlinked activity empty state points to Link Check + Manual fallback."""
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
    assert 'data-testid="links-manual-link-guidance"' in html
    assert "Link Check" in html
    assert "Manual fallback" in html
    assert "Manual element linking is not available in this workspace yet." not in html
    assert "Suggest Links" not in html
    assert 'data-testid="links-suggestion-card"' not in html
    assert 'data-testid="links-remove-link-btn"' not in html


@pytest.mark.django_db
def test_links_professional_review_workflow(client):
    """Inspector collapse, Highlight Mode, presets, and simple Apply Confirm modal."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-toggle-inspector"' in html
    assert 'data-testid="links-inspector-collapse"' in html
    assert 'data-testid="links-inspector-rail"' in html
    assert "lw-inspector-collapsed" in html
    assert "#fourD-root.lw-inspector-collapsed #lw-inspector" in html
    assert (
        "width: 40px" in html.split("#fourD-root.lw-inspector-collapsed #lw-inspector", 1)[1][:200]
    )
    assert 'data-lw-inspector="open"' in html
    assert "lw-inspector-open" in html.split('id="fourD-root"', 1)[1][:400]

    assert 'data-testid="links-highlight-mode"' in html
    assert "Highlight Mode" in html
    assert 'data-testid="links-clear-highlight"' in html
    assert "Clear Highlight" in html
    assert 'data-testid="links-highlight-selected"' in html
    assert 'data-testid="links-highlight-legend"' in html
    assert "lw-act-check" in html
    assert "#fourD-root.lw-highlight-mode .lw-act-check" in html
    assert ".lw-act-check" in html
    assert "display: none" in html.split(".lw-act-check", 1)[1][:120]
    assert "castor:highlight" in html
    assert "castor:focus-element" in html
    assert "castor:reset" in html
    assert "castor:isolate" in html

    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert 'data-testid="links-run-link-check"' in toolbar
    assert 'data-testid="links-highlight-mode"' in toolbar
    assert 'data-testid="links-toggle-inspector"' in toolbar
    assert "Run Link Check" in toolbar

    assert 'data-testid="links-match-results"' in html
    assert 'data-lw-results="collapsed"' in html
    assert "lw-results-collapsed" in html
    assert 'data-testid="links-results-tab-matched"' in html
    assert 'data-testid="links-results-tab-unmatched-activities"' in html
    assert 'data-testid="links-results-tab-unmatched-elements"' in html
    assert 'data-testid="links-results-tab-conflicts"' in html
    assert 'data-testid="links-results-select-all"' in html
    assert 'data-testid="links-results-clear-selection"' in html

    assert 'data-testid="links-preset-activity-id"' in html
    assert "Activity Code ↔ Activity ID" in html
    assert 'data-testid="links-preset-task-code"' in html
    assert 'data-testid="links-preset-wbs"' in html
    assert 'data-testid="links-preset-level-type"' in html
    assert 'data-testid="links-preset-custom"' in html
    preset_task = html.split('data-testid="links-preset-task-code"', 1)[0][-120:]
    assert (
        "disabled" in preset_task
        or "disabled" in html.split('data-testid="links-preset-task-code"', 1)[1][:80]
    )
    assert "not available yet" in html

    assert "Type APPROVE" not in html
    assert 'type="checkbox" id="lw-apply-ack"' not in html
    assert 'id="lw-apply-ack"' in html
    assert 'type="hidden"' in html.split('id="lw-apply-ack"', 1)[0][-80:]
    assert 'data-testid="links-apply-ack"' in html
    assert 'data-testid="links-apply-confirm-phrase"' in html
    assert 'value="APPROVE"' in html.split('id="lw-apply-confirm-phrase"', 1)[1][:120]
    assert ">Cancel<" in html.split('id="lwApplyLinksModal"', 1)[1]
    assert ">Confirm<" in html.split('data-testid="links-apply-confirm-btn"', 1)[1][:80]
    assert "Applies the latest preview set" in html
    assert "Apply Preview Matches" in html

    assert 'data-testid="links-manual-fallback-heading"' in html
    assert "Manual fallback" in html
    assert 'data-testid="links-link-selected-element-btn"' in html
    assert "disabled" in html.split('data-testid="links-link-selected-element-btn"', 1)[1][:120]
    assert "Select an activity or run Link Check." in html
    assert "Run Link Check to compare schedule activities with model parameters." in html
    assert "Select a model element to enable Link Selected Element." in html
    assert "Check activities, choose a Highlight color, then Highlight Selected." in html

    assert "Suggest Links" not in html
    assert "Suggested Links" not in html
    assert "AI suggestions" not in html
    assert "Generating candidates" not in html
    assert "Review queue" not in html
    assert (
        "Governance"
        not in html.split('data-testid="links-workspace"', 1)[1].split(
            'id="fd-timeline-section"', 1
        )[0]
    )
    for phrase in (
        "Suggest Links",
        "Suggested Links",
        "AI suggestions",
        "Link Proposals",
        "Review queue",
        "Trust state",
        "Destructive ops require owner",
    ):
        assert phrase not in html

    primary = html.split('data-testid="links-model-context-primary"', 1)[1].split(
        'data-testid="links-selected-activity"', 1
    )[0]
    assert 'data-testid="links-model-context"' in primary
    assert 'data-testid="links-match-results"' not in primary
    assert 'data-zoom="day"' not in html
    assert 'id="fd-gantt-timeline-hdr"' not in html


@pytest.mark.django_db
def test_links_highlight_workflow_fix(client):
    """Inspector stays collapsed on select; highlight is explicit; Focus ≠ Isolate."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    select_fn = html.split("async function lwSelectActivity", 1)[1].split(
        "async function lwLinkSelectedElement", 1
    )[0]
    assert 'lwSetPanel("inspector", true)' not in select_fn
    assert "lwSyncInspectorRail" in select_fn

    click_block = html.split('bodyEl.addEventListener("click"', 1)[1].split(
        "function selectTaskByGlobalId", 1
    )[0]
    assert "castor:highlight" not in click_block
    assert "castor:focus-element" not in click_block
    assert "castor:isolate" not in click_block
    assert "lwSelectActivity(t)" in click_block

    highlight_fn = html.split("function lwHighlightSelected", 1)[1].split(
        "function lwClearHighlight", 1
    )[0]
    assert "color_map" in highlight_fn
    assert "LW_HL_PALETTE" in html
    assert "castor:highlight" in highlight_fn

    focus_fn = html.split("function lwFocusHighlighted", 1)[1].split(
        "function lwIsolateHighlighted", 1
    )[0]
    isolate_fn = html.split("function lwIsolateHighlighted", 1)[1].split(
        "function lwPreviewStateForTask", 1
    )[0]
    assert "castor:focus-element" in focus_fn
    assert "isolate: false" in focus_fn
    assert "castor:isolate" in isolate_fn
    assert "castor:focus-element" not in isolate_fn
    assert "lwIsolateHighlighted" in html
    assert 'addEventListener("click", lwIsolateHighlighted)' in html
    assert 'addEventListener("click", lwFocusHighlighted)' in html

    assert "links-legend-minimize" in html
    assert "lw-legend-minimized" in html
    assert "Highlight:" in html
    assert "links-legend-swatch" in html
    assert "Selected · " in html
    assert "Type APPROVE" not in html
    assert 'type="checkbox" id="lw-apply-ack"' not in html
    assert "Suggest Links" not in html
    assert "Suggested Links" not in html
    assert "AI suggestions" not in html
    assert "Generating candidates" not in html
    assert "Review queue" not in html
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert re.search(r">\s*Play\s*<", toolbar) is None
    assert "Isolate is not available" not in html


@pytest.mark.django_db
def test_links_user_controlled_highlight_colors(client):
    """Highlight Selected applies the user-chosen color; later groups keep prior colors."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="links-highlight-color"' in html
    assert 'data-selected-color="#f59e0b"' in html
    assert 'data-testid="links-highlight-color-amber"' in html
    assert 'data-testid="links-highlight-color-cyan"' in html
    assert 'data-testid="links-highlight-color-purple"' in html
    assert 'data-testid="links-highlight-color-custom"' in html
    assert "Highlight color" in html
    assert "lwSetHighlightColor" in html
    assert "_lwSelectedHighlightColor" in html
    assert "_lwHighlightedByKey" in html

    highlight_fn = html.split("function lwHighlightSelected", 1)[1].split(
        "function lwClearHighlight", 1
    )[0]
    assert "_lwSelectedHighlightColor" in highlight_fn
    assert "selected_color" in highlight_fn
    assert "color_map" in highlight_fn
    assert "LW_HL_PALETTE[order" not in highlight_fn
    assert "_lwHighlightedByKey[key]" in highlight_fn
    assert "_lwHighlightAssignOrder.push" in highlight_fn

    clear_fn = html.split("function lwClearHighlight", 1)[1].split(
        "function lwClearHighlightSelection", 1
    )[0]
    assert 'type: "castor:reset"' in clear_fn
    assert "_lwHighlightedByKey = {}" in clear_fn
    assert "_lwHighlightKeys" not in clear_fn

    sel_fn = html.split("function lwClearHighlightSelection", 1)[1].split(
        "function lwFocusHighlighted", 1
    )[0]
    assert "_lwHighlightKeys = new Set()" in sel_fn
    assert "castor:reset" not in sel_fn
    assert "_lwHighlightedByKey" not in sel_fn

    assert (
        'lwSetPanel("inspector", true)'
        not in html.split("async function lwSelectActivity", 1)[1].split(
            "async function lwLinkSelectedElement", 1
        )[0]
    )
    click_block = html.split('bodyEl.addEventListener("click"', 1)[1].split(
        "function selectTaskByGlobalId", 1
    )[0]
    assert "castor:highlight" not in click_block
    assert (
        "castor:focus-element"
        in html.split("function lwFocusHighlighted", 1)[1].split(
            "function lwIsolateHighlighted", 1
        )[0]
    )
    assert (
        "castor:isolate"
        in html.split("function lwIsolateHighlighted", 1)[1].split(
            "function lwPreviewStateForTask", 1
        )[0]
    )
    assert "Suggest Links" not in html
    assert "Suggested Links" not in html
    assert "AI suggestions" not in html
    assert "Generating candidates" not in html


@pytest.mark.django_db
def test_links_model_context_viewer_controls_no_site(client):
    """Links Model Context shows only wired camera controls; Site is gone."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()
    assert response.status_code == 200

    toolbar = html.split('data-testid="links-model-context"', 1)[1].split('id="fd-iframe"', 1)[0]
    assert "Site" not in toolbar
    assert 'data-mode="site-status"' not in html
    assert "_siteStatusBtn" not in html

    assert 'data-testid="links-model-link-check"' in toolbar
    assert "Link Check" in toolbar
    assert 'aria-label="Link Check"' in toolbar
    assert 'data-testid="links-model-reset-view"' in toolbar
    assert "Reset View" in toolbar
    assert 'aria-label="Reset View"' in toolbar
    assert "Reset colours" not in html
    assert 'data-testid="links-model-zoom-in"' in toolbar
    assert 'aria-label="Zoom In"' in toolbar
    assert 'data-testid="links-model-zoom-out"' in toolbar
    assert 'aria-label="Zoom Out"' in toolbar
    assert 'data-testid="links-model-focus"' in toolbar
    assert 'aria-label="Focus"' in toolbar

    assert 'type: "castor:reset-view"' in html
    assert 'type: "castor:viewer-zoom"' in html
    reset_handler = html.split('getElementById("fd-reset-view-btn")', 1)[1].split(
        "async function loadTimeline", 1
    )[0]
    assert "castor:reset-view" in reset_handler
    assert 'type: "castor:reset"' not in reset_handler

    zoom_in_handler = html.split('getElementById("fd-zoom-in-btn")', 1)[1].split(
        'getElementById("fd-zoom-out-btn")', 1
    )[0]
    zoom_out_handler = html.split('getElementById("fd-zoom-out-btn")', 1)[1].split(
        'querySelectorAll(".fd-lf-btn")', 1
    )[0]
    assert "castor:viewer-zoom" in zoom_in_handler
    assert 'direction: "in"' in zoom_in_handler
    assert "castor:viewer-zoom" in zoom_out_handler
    assert 'direction: "out"' in zoom_out_handler

    assert 'data-testid="links-highlight-mode"' in html
    assert "Highlight Mode" in html
    assert 'data-testid="links-toggle-inspector"' in html
    assert "lw-inspector-collapsed" in html
    assert "Suggest Links" not in html
    assert "Suggested Links" not in html
    assert "AI suggestions" not in html
    assert "Generating candidates" not in html
    assert "Review queue" not in html
    assert "Trust state" not in html
    assert "Destructive ops require owner" not in html
    visible_toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split(
        'data-testid="links-workspace-body"', 1
    )[0]
    assert re.search(r">\s*Play\s*<", visible_toolbar) is None
