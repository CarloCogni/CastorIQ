# scheduling/tests/test_resources_readiness_page.py
"""DF-E5a Resources Readiness page gate, route, and honesty tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from scheduling.models import P6ResourceAssignment
from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.resources_readiness import (
    SOURCE_VERSION_CAVEAT,
    ResourcesReadinessService,
    resources_page_gate_ok,
)
from scheduling.services.resource_foundation import COST_SOURCE_CANONICAL, COST_SOURCE_P6_FALLBACK
from scheduling.tests.factories import ResourceAssignmentFactory, ResourceFactory, TaskFactory


def _member_client(client, project):
    user = UserFactory()
    ProjectMembershipFactory(project=project, user=user)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_resources_route_resolves():
    """executive_controls_resources URL name resolves."""
    project = ProjectFactory()
    url = reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk})
    assert "/executive-controls/resources/" in url


@pytest.mark.django_db
def test_gate_fails_without_resource_signal():
    """No assignments / no labor / no AC → gate closed."""
    ok, reason = resources_page_gate_ok(
        has_assignment_store=False,
        has_labor_signal=False,
        has_ac_signal=False,
    )
    assert ok is False
    assert "No resource" in reason


@pytest.mark.django_db
def test_gate_fails_when_store_exists_but_no_signal():
    """Empty cost/unit signals keep Resources unavailable."""
    ok, reason = resources_page_gate_ok(
        has_assignment_store=True,
        has_labor_signal=False,
        has_ac_signal=False,
    )
    assert ok is False
    assert "labor" in reason.lower() or "actual cost" in reason.lower()


@pytest.mark.django_db
def test_readiness_canonical_source_and_source_version_caveat():
    """Canonical badge + source_version caveat when ScheduleSourceVersion absent."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_units=Decimal("10.0"),
        actual_units=Decimal("4.0"),
        actual_cost=Decimal("50.00"),
        is_pending=False,
    )

    payload = ResourcesReadinessService(str(project.pk)).build()

    assert payload["gate_enabled"] is True
    assert payload["source"] == COST_SOURCE_CANONICAL
    assert payload["source_version_unavailable"] is True
    assert SOURCE_VERSION_CAVEAT in payload["caveats"]
    assert payload["readiness"]["assignment_count"] == 1
    assert payload["manhours"]["planned"]["available"] is True
    assert "Not site headcount" in payload["non_claims"]


@pytest.mark.django_db
def test_readiness_p6_fallback_source():
    """P6-only projects use legacy fallback source badge."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    P6ResourceAssignment.objects.create(
        project=project,
        task=task,
        p6_activity_object_id="A1",
        p6_resource_object_id="R1",
        resource_type="Labor",
        planned_units=Decimal("8.0"),
        actual_units=Decimal("2.0"),
        actual_cost=Decimal("10.00"),
        is_pending=False,
    )

    payload = ResourcesReadinessService(str(project.pk)).build()

    assert payload["gate_enabled"] is True
    assert payload["source"] == COST_SOURCE_P6_FALLBACK


@pytest.mark.django_db
def test_missing_manhours_display_unavailable_not_zero():
    """When no labor units, manhour KPIs are Unavailable (not 0)."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    ResourceAssignmentFactory(
        project=project,
        task=task,
        resource=ResourceFactory(project=project),
        planned_units=None,
        actual_units=None,
        actual_cost=Decimal("25.00"),
        is_pending=False,
    )

    payload = ResourcesReadinessService(str(project.pk)).build()

    assert payload["gate_enabled"] is True
    assert payload["manhours"]["planned"]["display"] == "Unavailable"
    assert payload["manhours"]["planned"]["value"] is None
    assert payload["manhours"]["actual"]["display"] == "Unavailable"


@pytest.mark.django_db
def test_empty_project_gate_disabled_in_capability_pages():
    """Projects without resource signals keep resources page disabled."""
    project = ProjectFactory()
    TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        cost=Decimal("100.00"),
    )
    profile = ProjectAnalyticsCapabilityProfile(project).build()

    assert "resources" not in profile["recommended_visible_pages"]
    assert "resources" in profile["disabled_pages"]
    assert "resources" in profile["page_reasons"]


@pytest.mark.django_db
def test_resources_page_renders_canonical_content(client):
    """Authenticated GET shows source badge, caveat, non-claims, help modal."""
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
    _member_client(client, project)

    url = reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk})
    response = client.get(url)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "canonical_resource_assignment" in body
    assert "source_version is unavailable" in body
    assert "Not site headcount" in body
    assert "resourcesReadinessHelpModal" in body
    assert "Not full resource planning" in body
    assert 'data-testid="resources-subnav-enabled"' in body or "Resources" in body


@pytest.mark.django_db
def test_resources_page_unavailable_state_when_gate_fails(client):
    """Empty project page shows unavailable reason, not authoritative KPIs."""
    project = ProjectFactory()
    _member_client(client, project)

    url = reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk})
    response = client.get(url)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Resources readiness unavailable" in body
    assert (
        "canonical_resource_assignment" not in body or "Source:" not in body.split("unavailable")[0]
    )


@pytest.mark.django_db
def test_methodology_drilldown_url_name_exists():
    """Methodology manhour drilldown target executive_controls_resources exists."""
    from scheduling.services.executive_controls.methodology import E8_METRIC_REGISTRY

    assert (
        E8_METRIC_REGISTRY["e8.planned_manhours"].drilldown_route == "executive_controls_resources"
    )
    project = ProjectFactory()
    assert reverse("scheduling:executive_controls_resources", kwargs={"pk": project.pk})
