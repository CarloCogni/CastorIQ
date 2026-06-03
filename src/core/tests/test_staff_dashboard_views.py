# core/tests/test_staff_dashboard_views.py
"""Access tests for the staff-only BI/health dashboard.

Three layers of gating must hold for every dashboard URL:
- anonymous → 302 to login
- authenticated non-staff → 302 (user_passes_test default redirect)
- authenticated staff → 200
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from environments.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ── /staff/dashboard/ → redirects to /overview/ for staff ──────────────────


def test_root_redirect_to_overview_for_staff(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard"))
    assert response.status_code == 302
    assert response["Location"].endswith("/staff/dashboard/overview/")


def test_root_redirect_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard"))
    # user_passes_test sends to LOGIN_URL; non-200 either way is the contract.
    assert response.status_code == 302
    assert "login" in response["Location"].lower() or "staff/dashboard" not in response["Location"]


def test_root_redirect_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard"))
    assert response.status_code == 302


# ── /staff/dashboard/overview/ — the main shipping page ────────────────────


def test_overview_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard_overview"))
    assert response.status_code == 302


def test_overview_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard_overview"))
    assert response.status_code == 302


def test_overview_allows_staff_and_renders_context(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    assert response.status_code == 200

    ctx = response.context
    assert "kpis" in ctx
    assert "tokens_per_day" in ctx
    assert "cost_per_day" in ctx
    assert "top_users" in ctx
    assert "pulse" in ctx
    assert ctx["active_tab"] == "overview"
    assert ctx["window_days"] == 7  # default
    assert ctx["daily_cost_threshold_usd"] == 10


def test_overview_window_query_param_clamps_to_known_values(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)

    # Valid values stick.
    for value in (1, 7, 30):
        response = client.get(
            reverse("staff_dashboard_overview"), {"window": str(value)}
        )
        assert response.status_code == 200
        assert response.context["window_days"] == value

    # Bad values silently fall back to 7.
    response = client.get(
        reverse("staff_dashboard_overview"), {"window": "999"}
    )
    assert response.status_code == 200
    assert response.context["window_days"] == 7


def test_overview_renders_chart_data_as_json_script(client):
    """The page must embed both chart payloads via Django json_script tags."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert 'id="tokens-per-day-data"' in body
    assert 'id="cost-per-day-data"' in body
    # Chart.js script tag — verify the chart library is wired.
    assert "chart.umd.min.js" in body


def test_overview_help_pill_present(client):
    """Castor convention: every meaningful tab ships with a ? help pill."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert 'class="help-pill"' in body
    assert 'data-bs-target="#dashboardOverviewHelpModal"' in body
    assert 'id="dashboardOverviewHelpModal"' in body


# ── /staff/dashboard/cost/ — Tab 2 ─────────────────────────────────────────


def test_cost_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard_cost"))
    assert response.status_code == 302


def test_cost_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard_cost"))
    assert response.status_code == 302


def test_cost_allows_staff_and_renders_context(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_cost"))

    assert response.status_code == 200

    ctx = response.context
    assert "kpis" in ctx
    assert "cost_by_purpose" in ctx
    assert "tokens_local_vs_paid" in ctx
    assert "cost_per_user_per_day" in ctx
    assert "budget_strip" in ctx
    assert ctx["active_tab"] == "cost"


def test_cost_renders_chart_data_as_json_script(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_cost"))

    body = response.content.decode("utf-8")
    assert 'id="cost-by-purpose-data"' in body
    assert 'id="local-vs-paid-data"' in body
    assert 'id="cost-per-user-data"' in body
    assert "chart.umd.min.js" in body


def test_cost_help_pill_present(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_cost"))

    body = response.content.decode("utf-8")
    assert 'class="help-pill"' in body
    assert 'data-bs-target="#dashboardCostHelpModal"' in body
    assert 'id="dashboardCostHelpModal"' in body


def test_tab_nav_shows_overview_and_cost_on_overview_page(client):
    """All registered tabs render in the nav strip on every dashboard page."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert reverse("staff_dashboard_overview") in body
    assert reverse("staff_dashboard_cost") in body
    assert reverse("staff_dashboard_reliability") in body


# ── /staff/dashboard/reliability/ — Tab 3 ──────────────────────────────────


def test_reliability_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard_reliability"))
    assert response.status_code == 302


def test_reliability_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard_reliability"))
    assert response.status_code == 302


def test_reliability_allows_staff_and_renders_context(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_reliability"))

    assert response.status_code == 200

    ctx = response.context
    for key in (
        "kpis",
        "provider_success",
        "p95_latency",
        "error_breakdown",
        "error_backlog",
        "failure_taxonomy",
        "ingestion",
        "success_threshold_pct",
    ):
        assert key in ctx, f"missing context key: {key}"
    assert ctx["active_tab"] == "reliability"
    assert ctx["success_threshold_pct"] == 95


def test_reliability_renders_chart_data_as_json_script(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_reliability"))

    body = response.content.decode("utf-8")
    assert 'id="provider-success-data"' in body
    assert 'id="p95-latency-data"' in body
    assert 'id="error-breakdown-data"' in body
    assert 'id="failure-taxonomy-data"' in body
    assert "chart.umd.min.js" in body


def test_reliability_help_pill_present(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_reliability"))

    body = response.content.decode("utf-8")
    assert 'class="help-pill"' in body
    assert 'data-bs-target="#dashboardReliabilityHelpModal"' in body
    assert 'id="dashboardReliabilityHelpModal"' in body


# ── /staff/dashboard/engagement/ — Tab 4 ───────────────────────────────────


def test_engagement_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard_engagement"))
    assert response.status_code == 302


def test_engagement_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard_engagement"))
    assert response.status_code == 302


def test_engagement_allows_staff_and_renders_context(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_engagement"))

    assert response.status_code == 200

    ctx = response.context
    for key in (
        "kpis",
        "dau_wau_mau",
        "ttfv",
        "feature_mix",
        "modify_funnel",
        "activity_heatmap",
        "cohort_grid",
        "needs_plotly",
    ):
        assert key in ctx, f"missing context key: {key}"
    assert ctx["active_tab"] == "engagement"
    assert ctx["needs_plotly"] is True


def test_engagement_renders_chart_data_as_json_script(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_engagement"))

    body = response.content.decode("utf-8")
    assert 'id="dau-wau-mau-data"' in body
    assert 'id="ttfv-data"' in body
    assert 'id="feature-mix-data"' in body
    assert 'id="modify-funnel-data"' in body
    assert 'id="activity-heatmap-data"' in body
    assert 'id="cohort-grid-data"' in body
    assert "chart.umd.min.js" in body


def test_engagement_loads_plotly_when_needs_plotly_set(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_engagement"))

    body = response.content.decode("utf-8")
    # Plotly lazy-load gated by needs_plotly — present here.
    assert "plotly" in body.lower()


def test_engagement_help_pill_present(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_engagement"))

    body = response.content.decode("utf-8")
    assert 'class="help-pill"' in body
    assert 'data-bs-target="#dashboardEngagementHelpModal"' in body
    assert 'id="dashboardEngagementHelpModal"' in body


def test_tab_nav_shows_engagement_on_overview_page(client):
    """Engagement pill registered in TABS list → renders on every dashboard page."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert reverse("staff_dashboard_engagement") in body


def test_overview_does_not_load_plotly(client):
    """Plotly stays opt-in — pages without needs_plotly must not pull the CDN."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert "plotly" not in body.lower()


# ── /staff/dashboard/investor/ — Tab 7 ─────────────────────────────────────


def test_investor_blocks_anonymous(client):
    response = client.get(reverse("staff_dashboard_investor"))
    assert response.status_code == 302


def test_investor_blocks_non_staff(client):
    user = UserFactory(is_staff=False)
    client.force_login(user)
    response = client.get(reverse("staff_dashboard_investor"))
    assert response.status_code == 302


def test_investor_allows_staff_and_renders_context(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_investor"))

    assert response.status_code == 200

    ctx = response.context
    for key in (
        "kpis",
        "cohort_grid",
        "acceptance",
        "provider_mix",
        "ingestion_scatter",
        "design_partners",
        "needs_plotly",
    ):
        assert key in ctx, f"missing context key: {key}"
    assert ctx["active_tab"] == "investor"
    assert ctx["needs_plotly"] is True


def test_investor_renders_chart_data_as_json_script(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_investor"))

    body = response.content.decode("utf-8")
    assert 'id="investor-cohort-data"' in body
    assert 'id="investor-ingestion-data"' in body


def test_investor_loads_plotly_when_needs_plotly_set(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_investor"))

    body = response.content.decode("utf-8")
    assert "plotly" in body.lower()


def test_investor_help_pill_present(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_investor"))

    body = response.content.decode("utf-8")
    assert 'class="help-pill"' in body
    assert 'data-bs-target="#dashboardInvestorHelpModal"' in body
    assert 'id="dashboardInvestorHelpModal"' in body


def test_tab_nav_shows_investor_on_overview_page(client):
    """Investor pill registered in TABS → renders on every dashboard page."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_overview"))

    body = response.content.decode("utf-8")
    assert reverse("staff_dashboard_investor") in body


def test_investor_dateline_header_renders_today_date(client):
    """Header strip should ship with today's ISO date as a printable line."""
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("staff_dashboard_investor"))

    body = response.content.decode("utf-8")
    today_iso = timezone.now().date().isoformat()
    assert today_iso in body
