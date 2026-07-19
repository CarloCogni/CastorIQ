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
    assert "Operational EVM diagnostics" in html
    assert 'data-testid="legacy-evm-decision-note"' in html
    assert "decision-facing view" in html
    assert "Executive EVM" in html
    assert "Advanced supporting diagnostics" in html
    assert "EVM Dashboard" not in html
    assert "Decision Summary" not in html
    assert "Top 5 — Needs Attention" not in html
    assert "Diagnostics summary" in html
    assert "Items to review" in html
    assert 'data-testid="legacy-evm-diagnostics-summary"' in html


@pytest.mark.django_db
def test_executive_evm_remains_decision_facing(client):
    """Executive EVM Analytics keeps the decision-facing badge."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "EVM Analytics" in html
    assert 'data-testid="exec-evm-decision-badge"' in html
    assert html.count("Decision-facing") == 1


@pytest.mark.django_db
def test_fourd_link_proposals_wording_not_approval(client):
    """4D Link uses Link Proposals wording; Governance remains approval authority."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Link Proposals" in html
    assert "Smart Pipeline (propose)" not in html
    assert "Propose links" in html
    assert "Castor Link Engine" not in html
    assert "Governance" in html
    assert "Castor AI" not in html
    assert "Link assistant" in html
    assert "Advisory suggestions" in html
    assert "does not approve links" in html


@pytest.mark.django_db
def test_link_proposals_surface_has_no_inline_approve(client):
    """Proposal review has no binding_accept control — Governance is the approve surface."""
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
    assert "Open Governance for ≥95% proposals" in html
    assert html.count("Proposed links require Governance approval") == 1
    assert reverse("scheduling:link_governance_workspace", args=[project.pk]) in html


@pytest.mark.django_db
def test_governance_authority_badge_present(client):
    """Governance workspace labels trusted-binding approval authority once."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:link_governance_workspace", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="governance-authority-badge"' in html
    assert html.count("Trusted bindings") == 1


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
    assert "Resources Readiness" in html
    assert "Resources readiness" in html
    assert html.count("Not full E8-E") == 1
    assert html.count('data-testid="resources-non-claims"') == 1
    assert html.count('data-testid="resources-source-version-caveat"') == 1
    assert 'data-testid="resources-caveats"' not in html
    assert "Not site headcount" in html


@pytest.mark.django_db
def test_bim_nav_demotes_legacy_evm_label(client):
    """4D/5D nav labels legacy EVM as diagnostics."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "EVM Diagnostics" in html
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
