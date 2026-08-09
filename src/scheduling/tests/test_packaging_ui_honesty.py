# scheduling/tests/test_packaging_ui_honesty.py
"""Packaging Fix Packages 2–4 — less-is-more UI honesty polish."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import (
    ResourceAssignmentFactory,
    ResourceFactory,
    TaskFactory,
)


@pytest.mark.django_db
def test_legacy_evm_labelled_diagnostic_not_primary_decision(client):
    """Legacy Schedule EVM is demoted to operational diagnostics."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=evm")
    html = response.content.decode()

    assert response.status_code == 200
    assert "Schedule Health" in html
    assert 'data-testid="legacy-evm-decision-note"' in html
    assert "Executive EVM" in html
    assert "Schedule Readiness diagnostics" in html
    assert "EVM Dashboard" not in html
    assert "Decision Summary" not in html
    assert "Top 5 — Needs Attention" not in html
    assert "Diagnostics summary" in html
    assert "Items to review" in html
    assert 'data-testid="legacy-evm-diagnostics-summary"' in html
    assert 'data-testid="value-cleanup-hide-monte-carlo"' in html
    assert 'data-testid="value-cleanup-hide-ml"' in html
    assert 'data-testid="value-cleanup-hide-cashflow"' in html
    assert 'data-testid="value-cleanup-hide-chat"' in html
    assert "Company actual cost" not in html
    assert "company cashflow" not in html.lower() or "Not company cashflow" in html


@pytest.mark.django_db
def test_executive_evm_remains_decision_facing(client):
    """Controls indicators page keeps schedule/assignment sourced badge."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Schedule / Assignment Indicators" in html
    assert 'data-testid="exec-evm-decision-badge"' in html
    assert html.count("Schedule / assignment sourced") == 1


@pytest.mark.django_db
def test_fourd_link_proposals_wording_not_approval(client):
    """Links uses Link Proposals; Applied Links is the confirmation surface."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Link Proposals" in html
    assert "Smart Pipeline (propose)" not in html
    assert "Suggest Links" in html
    assert "Castor Link Engine" not in html
    assert "Applied Links" in html
    assert 'data-testid="fourd-link-quality-tab"' in html
    assert "Castor AI" not in html
    assert "Link assistant" in html
    assert "Advisory suggestions" in html
    assert "does not confirm links" in html
    assert "approval authority" not in html.lower()


@pytest.mark.django_db
def test_link_proposals_surface_has_no_inline_approve(client):
    """Proposal review has no binding_accept control — Applied Links is the confirm surface."""
    project = ProjectFactory()
    client.force_login(project.owner)
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-PROP")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.99,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
    )

    response = client.get(reverse("scheduling:review", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert "binding_accept" not in html
    assert "Approve as trusted" not in html
    assert "Approve ≥95%" not in html
    assert "binding_bulk_accept" not in html
    assert 'data-testid="proposals-open-governance-cta"' in html
    assert "Open Applied Links for ≥95% proposals" in html
    assert html.count("Proposed links require Applied Links confirmation") == 1
    assert reverse("scheduling:link_governance_workspace", args=[project.pk]) in html


@pytest.mark.django_db
def test_governance_authority_badge_present(client):
    """Applied Links workspace keeps applied badge once; method badge not user-facing."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:link_governance_workspace", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="governance-authority-badge"' in html
    assert "Applied Links" in html
    assert html.count("Applied / Confirmed map") == 1
    assert 'data-testid="governance-method-badge-advanced"' in html


@pytest.mark.django_db
def test_matrix_stage_proxy_badge_once(client):
    """Matrix stage-proxy honesty appears once when hierarchy is stage proxy."""
    project = ProjectFactory()
    TaskFactory(project=project, stage="structure")
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:executive_controls_matrix", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    if "Stage proxy — not canonical WBS" in html:
        assert html.count("Stage proxy — not canonical WBS") == 1


@pytest.mark.django_db
def test_trades_proxy_badge_not_duplicated(client):
    """Trades fragment proxy honesty is a single badge pair, not repeated paragraphs."""
    project = ProjectFactory()
    TaskFactory(project=project, sub_stage="electrical")
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:executive_controls_trades_analysis", kwargs={"pk": project.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="trades-proxy-badges"' in html
    assert html.count("Trade proxy — not governed · diagnostic") == 1
    assert "labeled separately from governed mapping authority" not in html


@pytest.mark.django_db
def test_resources_caveats_appear_once(client):
    """Resources readiness non-claims once; caveats card not duplicated on-page."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_units=Decimal("12.0"),
        actual_units=Decimal("3.0"),
        actual_cost=Decimal("40.00"),
        is_pending=False,
    )
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Resource / Cost Data Readiness" in html
    assert html.count("Not full E8-E") == 1
    assert html.count('data-testid="resources-non-claims"') == 1
    assert html.count('data-testid="resources-source-version-caveat"') == 1
    assert 'data-testid="resources-caveats"' not in html
    assert "Not site headcount" in html
    assert "data readiness" in html.lower()
    assert "resource planning" in html.lower()  # in "Not resource planning" framing
    assert 'data-testid="resources-fte-advanced-hidden"' in html


@pytest.mark.django_db
def test_bim_nav_demotes_legacy_evm_label(client):
    """4D/5D primary nav hides Schedule Health / legacy diagnostics."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "EVM Diagnostics" not in html
    assert 'data-testid="hub-schedule-health-advanced"' in html
    assert "Links" in html
    assert 'data-testid="hub-links"' in html
    assert 'data-testid="data-sources-purpose"' in html
    assert "Imported schedule and provenance" in html


@pytest.mark.django_db
def test_project_nav_labels_ifc_schedule_vs_4d5d(client):
    """Project shell distinguishes IFC Schedule from 4D/5D."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("projects:ask", kwargs={"pk": project.pk}), follow=True)
    html = response.content.decode()

    assert response.status_code == 200
    assert "IFC Schedule" in html
    assert "4D/5D" in html


# ── Package 5 — dead surface / copy hygiene ───────────────────────────────


@pytest.mark.django_db
def test_schedule_tab_review_redirects_to_real_review(client):
    """Dead ``?tab=review`` deep-link redirects to the real Link Proposals route."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=review")

    assert response.status_code == 302
    assert response.url == reverse("scheduling:review", kwargs={"pk": project.pk})


@pytest.mark.django_db
def test_data_sources_review_url_points_to_real_review(client):
    """Data Sources deep-link target is the real review route, not blank tab=review."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert f'data-review-url="{reverse("scheduling:review", kwargs={"pk": project.pk})}"' in html
    assert "?tab=review" not in html


@pytest.mark.django_db
def test_fourd_link_hides_writeback_mutation_controls(client):
    """4D Link keeps advisory copy but exposes no writeback chat controls."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Link assistant" in html
    assert "Advisory suggestions" in html
    assert "does not confirm links" in html
    assert "schedule_writeback" not in html
    assert "fourD-chat-send" not in html
    assert "fourD-chat-input" not in html
    assert "fd-chat-toggle" not in html
    assert "fd-embed-btn" not in html


@pytest.mark.django_db
def test_autolink_summary_uses_proposal_not_authority_wording():
    """Autolink result partial speaks proposals + Applied Links, not auto-accept."""
    from django.template.loader import render_to_string

    project = ProjectFactory()
    html = render_to_string(
        "scheduling/components/autolink_summary.html",
        {
            "project": project,
            "ifc_param_name": "Activity ID",
            "summary": {
                "total_tasks": 3,
                "linked_exact": 1,
                "linked_normalized": 1,
                "linked_heuristic": 1,
                "linked_embedding": 0,
                "unlinked": 0,
                "needs_review": 2,
                "excluded_non_physical": 0,
            },
        },
    )

    assert "Link proposals generated" in html
    assert "Requires Applied Links confirmation" in html
    assert "Smart Auto-Link complete" not in html
    assert "linked automatically" not in html
    assert "auto_accepted" not in html


@pytest.mark.django_db
def test_legacy_evm_wbs_trade_proxy_labels(client):
    """Legacy EVM Diagnostics labels WBS/Trade as proxy / diagnostic only."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=evm")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="legacy-evm-wbs-proxy-badge"' in html
    assert "Stage proxy — not canonical WBS" in html
    assert 'data-testid="legacy-evm-trade-proxy-badge"' in html
    assert "Trade proxy — not governed Trade" in html
    assert html.count("Diagnostic only") >= 2


@pytest.mark.django_db
def test_timeliner_help_drops_navisworks_clone_wording(client):
    """TimeLiner help is neutral visual review language, not a clone claim."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "exactly like Navisworks" not in html
    assert "Navisworks TimeLiner" not in html
    assert "Visual schedule" in html
    assert "Not a full simulation replacement" in html
    assert "advisory" in html.lower() or "Advisory" in html


# ── Package 6 — KPI source / label / unavailable honesty ──────────────────


@pytest.mark.django_db
def test_viewer_entity_linked_tasks_trusted_only(client):
    """Entity detail linked_tasks excludes proposed / under-review bindings."""
    project = ProjectFactory()
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-VIEW-1")
    trusted_task = TaskFactory(project=project, name="Trusted Task")
    review_task = TaskFactory(project=project, name="Review Task")
    TaskEntityBinding.objects.create(
        task=trusted_task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
    )
    TaskEntityBinding.objects.create(
        task=review_task,
        entity_global_id=entity.global_id,
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        is_active=True,
    )
    client.force_login(project.owner)

    response = client.get(
        reverse(
            "ifc_viewer:viewer_element_props",
            kwargs={"pk": project.pk, "global_id": entity.global_id},
        )
    )
    data = response.json()

    assert response.status_code == 200
    assert data["found"] is True
    names = {t["name"] for t in data["linked_tasks"]}
    assert "Trusted Task" in names
    assert "Review Task" not in names
    assert all(t.get("trust") == "trusted" for t in data["linked_tasks"])


@pytest.mark.django_db
def test_link_proposals_summary_uses_task_units_not_bindings(client):
    """Link Proposals summary labels physical-task counts, not Total bindings."""
    project = ProjectFactory()
    client.force_login(project.owner)
    task = TaskFactory(project=project)
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-PROP-1",
        confidence=0.99,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
    )

    response = client.get(reverse("scheduling:review", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Physical tasks" in html
    assert "Tasks in review" in html
    assert "Applied / Confirmed tasks" in html
    assert "Total bindings" not in html


@pytest.mark.django_db
def test_overview_cost_labels_respect_schedule_performance_mode():
    """Overview Cost uses proxy/index labels when not cost-backed."""
    from datetime import date

    from scheduling.services.executive_controls.overview_filters import OverviewFilters
    from scheduling.services.executive_controls.overview_service import (
        ExecutiveControlsOverviewService,
    )

    project = ProjectFactory()
    TaskFactory(
        project=project,
        cost=None,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 28),
        is_non_physical=False,
    )
    payload = ExecutiveControlsOverviewService(project).build_cost_section(OverviewFilters())

    assert payload["cost_evm_available"] is False
    pv = next(c for c in payload["cards"] if c["metric_id"] == "e8.pv")
    bac = next(c for c in payload["cards"] if c["metric_id"] == "e8.bac")
    assert "proxy" in pv["label"].lower()
    assert pv["unit"] == "index"
    assert "proxy" in bac["label"].lower()
    assert bac["unit"] == "index"


@pytest.mark.django_db
def test_executive_evm_ac_source_line_when_ac_available():
    """Executive EVM AC metric caveat names the AC store when available."""
    from datetime import date
    from decimal import Decimal

    from scheduling.services.executive_controls.current_evm_analytics import (
        CurrentEVMAnalyticsService,
    )

    project = ProjectFactory()
    task = TaskFactory(
        project=project,
        cost=Decimal("200.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        status="complete",
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 15),
        is_non_physical=False,
    )
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        actual_cost=Decimal("80.00"),
        planned_cost=Decimal("200.00"),
        is_pending=False,
    )

    payload = CurrentEVMAnalyticsService(project).build()
    ac = payload["metrics"].get("e8.ac")
    assert ac is not None
    assert ac["available"] is True
    assert ac["label"] == "Assignment actual cost indicator"
    assert "canonical ResourceAssignment" in ac["caveat"]
    assert "company" in ac["caveat"].lower() or "ERP" in ac["caveat"]
    assert "Company actual cost" not in ac["label"]


@pytest.mark.django_db
def test_cashflow_task_cost_source_includes_proxy_caveat():
    """Cashflow task_cost fallback exposes an incomplete→0 proxy caveat."""
    from datetime import date
    from decimal import Decimal

    from scheduling.services.cashflow import compute_cashflow

    project = ProjectFactory()
    TaskFactory(
        project=project,
        cost=Decimal("1000.00"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 31),
        status="in_progress",
        is_non_physical=False,
    )
    result = compute_cashflow(str(project.pk))

    assert result.get("has_data") is True
    assert result["source"] == "task_cost"
    assert "incomplete" in result["source_caveat"].lower()
    assert "0" in result["source_caveat"]


@pytest.mark.django_db
def test_lookahead_shows_schedule_vs_trusted_caveat(client):
    """Look-ahead shows schedule-count vs trusted-model caveat once."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="lookahead-trusted-caveat"' in html
    assert "Task counts follow schedule dates" in html
    assert "applied / confirmed links" in html
    assert "trusted links" not in html.lower()
    assert html.count("Task counts follow schedule dates") == 1


@pytest.mark.django_db
def test_fourd_timeline_shows_trusted_only_label(client):
    """Links keeps trusted-only timeline marker; playback UI is demoted to Time View."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="fd-timeline-trusted-only"' in html
    assert "Timeline uses applied / confirmed links only." in html
    # Playback is not primary Links chrome (owned by Time View)
    assert 'data-testid="links-workspace-toolbar"' in html
    toolbar = html.split('data-testid="links-workspace-toolbar"', 1)[1].split("id=", 1)[0]
    assert ">Play<" not in toolbar


@pytest.mark.django_db
def test_resources_remaining_unavailable_when_only_actual_units():
    """Remaining manhours are Unavailable when planned units are missing."""
    from decimal import Decimal

    from scheduling.services.executive_controls.resources_readiness import (
        ResourcesReadinessService,
    )

    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_units=Decimal("0"),
        actual_units=Decimal("8.0"),
        actual_cost=Decimal("40.00"),
        is_pending=False,
    )

    payload = ResourcesReadinessService(str(project.pk)).build()
    remaining = payload["manhours"]["remaining"]

    assert remaining["available"] is False
    assert remaining["display"] == "Unavailable"
    assert remaining["value"] is None


@pytest.mark.django_db
def test_legacy_evm_dcma_labelled_legacy_p6_diagnostic(client):
    """DCMA section is labelled as legacy P6 diagnostic."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=evm")
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="dcma-legacy-p6-badge"' in html
    assert "Legacy P6 diagnostic" in html


@pytest.mark.django_db
def test_value_cleanup_no_company_cost_overclaim_in_main_surfaces(client):
    """Main Overview/EVM/hub copy must not claim ERP/invoice/QS/company spend."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    forbidden = (
        "Company actual cost",
        "ERP actual",
        "invoice actual",
        "QS valuation",
        "BOQ commercial",
        "Company cashflow",
    )

    urls = [
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources",
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link",
        reverse("scheduling:executive_controls", kwargs={"pk": project.pk}),
        reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}),
        reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk}),
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        for phrase in forbidden:
            assert phrase not in html, f"{phrase!r} found in {url}"
        # Allow explicit negation "Not company cashflow" only
        assert "company cashflow" not in html.replace("Not company cashflow", "")


@pytest.mark.django_db
def test_overview_cost_section_uses_assignment_cost_wording(client):
    """Overview cost HTMX section is assignment indicators, not Cost Position."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:executive_controls_overview_cost", kwargs={"pk": project.pk}),
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Schedule / Assignment Cost Indicators" in html
    assert "Cost Position" not in html
    assert 'data-testid="exec-cost-source-caveat"' in html
    assert "not ERP, invoice, QS, or company actual spend" in html


@pytest.mark.django_db
def test_exec_subnav_marks_resources_matrix_trades_advanced(client):
    """Matrix / Trades / Resources subnav items are hidden, not primary heroes."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="exec-subnav-advanced-matrix"' in html
    assert 'data-testid="exec-subnav-advanced-trades"' in html
    assert 'data-testid="exec-subnav-advanced-resources"' in html
    assert 'class="nav-item d-none" data-testid="exec-subnav-advanced-matrix"' in html
    assert ">Advanced<" not in html
