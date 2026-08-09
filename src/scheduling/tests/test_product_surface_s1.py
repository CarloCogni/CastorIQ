# scheduling/tests/test_product_surface_s1.py
"""Package S1 — Product Surface Reset (nav + wording honesty)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory

_REQUIRED_HUB = ("Schedule", "Links", "Model", "Time View", "Quantities", "Controls")
_FORBIDDEN_HUB = (
    "Advanced",
    "Intelligence",
    "Monte Carlo",
    "Completion ML",
    "Commercial 5D",
    "ERP",
    "QS valuation",
    "company actual cost",
)
_FORBIDDEN_LINK_UI = (
    "Approve",
    "Reject",
    "Governance",
    "Authority",
    "Quality Gate",
    "Review Gate",
)


def _visible_hub_html(html: str) -> str:
    """Strip d-none nav items so forbidden labels in hidden compat links do not fail."""
    return re.sub(
        r'<li class="nav-item d-none"[^>]*>.*?</li>',
        "",
        html,
        flags=re.DOTALL,
    )


@pytest.mark.django_db
def test_hub_exposes_six_simplified_labels(client):
    """Primary 4D/5D hub shows Schedule/Links/Model/Time View/Quantities/Controls."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    visible = _visible_hub_html(html)

    assert response.status_code == 200
    assert 'data-testid="hub-primary-nav"' in html
    for label in _REQUIRED_HUB:
        assert label in visible
        assert f"hub-{label.lower().replace(' ', '-')}" in html.replace("time-view", "time-view")
    assert 'data-testid="hub-schedule"' in html
    assert 'data-testid="hub-links"' in html
    assert 'data-testid="hub-model"' in html
    assert 'data-testid="hub-time-view"' in html
    assert 'data-testid="hub-quantities"' in html
    assert 'data-testid="hub-controls"' in html


@pytest.mark.django_db
def test_hub_hides_forbidden_product_concepts(client):
    """Visible hub chrome excludes Advanced / Intelligence / theater / commercial claims."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    )
    html = response.content.decode()
    m = re.search(
        r'<ul class="nav nav-pills"[^>]*data-testid="hub-primary-nav"[^>]*>(.*?)</ul>',
        html,
        flags=re.DOTALL,
    )
    assert m is not None
    visible = _visible_hub_html(m.group(0))

    for phrase in _FORBIDDEN_HUB:
        assert phrase not in visible, f"{phrase!r} leaked into visible hub"
    assert "Apply / 4D Link" not in visible
    assert "Data Sources" not in visible
    assert "Executive Controls" not in visible
    assert "EVM Diagnostics" not in visible
    # Intelligence remains only as hidden compat destination
    assert 'data-testid="hub-intelligence-hidden"' in html


@pytest.mark.django_db
def test_links_ui_uses_confirm_ignore_suggest_wording(client):
    """Links tab uses Confirm Link / Suggest Links / Applied Links language."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Suggest Links" in html
    assert "Applied Links" in html
    assert "Confirm Link" in html
    assert "does not confirm links" in html
    assert "Propose links" not in html
    assert ">Link Quality<" not in html and "Link Quality</" not in html
    assert "Advanced trust" not in html
    assert "Quality Gate" not in html
    assert "Review Gate" not in html
    # Product buttons must not say Approve/Reject as visible labels
    assert re.search(r">\s*Approve\s*<", html) is None
    assert re.search(r">\s*Reject\s*<", html) is None
    assert "Authority:" not in html


@pytest.mark.django_db
def test_applied_links_workspace_avoids_governance_product_words(client):
    """Applied Links workspace uses Confirm Link / Ignore Suggestion."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(
        reverse("scheduling:link_governance_workspace", kwargs={"pk": project.pk})
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Applied Links" in html
    assert "Applied / Confirmed map" in html
    assert "Link Quality" not in html
    assert "Authority:" not in html
    assert "Advanced trust" not in html


@pytest.mark.django_db
def test_review_queue_confirm_ignore_labels(client):
    """Review queue buttons say Confirm Link / Ignore Suggestion."""
    project = ProjectFactory()
    client.force_login(project.owner)
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-S1")
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.99,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
    )

    response = client.get(
        reverse("scheduling:link_governance_review_queue", kwargs={"pk": project.pk})
        + "?mode=review&page=1",
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "Confirm Link" in html
    assert "Ignore Suggestion" in html
    assert ">Approve<" not in html
    assert ">Reject<" not in html


@pytest.mark.django_db
def test_controls_labels_remain_source_honest(client):
    """Controls EVM page uses schedule performance wording — not financial Cost EVM."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls_evm", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Schedule Performance" in html
    assert 'data-testid="exec-evm-decision-badge"' in html
    assert "Schedule / progress sourced" in html
    assert "ERP" in html and "invoice" in html and "QS" in html
    assert "EVM Analytics" not in html
    assert "Decision-facing" not in html
    assert ">Cost EVM<" not in html
    assert "Monetary EVM" not in html

@pytest.mark.django_db
def test_exec_subnav_hides_matrix_trades_resources(client):
    """Matrix / Trades / Resources are not primary Controls destinations."""
    project = ProjectFactory()
    client.force_login(project.owner)

    response = client.get(reverse("scheduling:executive_controls", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'class="nav-item d-none" data-testid="exec-subnav-advanced-matrix"' in html
    assert ">Advanced<" not in html
    assert "Controls" in html
