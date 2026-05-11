# core/views_staff_dashboard.py
"""Staff-only BI/health dashboard views.

One CBV per tab on a shared base. The base handles ``is_staff`` gating and
threads the active tab + window-days into context so the same chrome works
for every page in the family. Aggregations live in
``core.services.usage_analytics`` — these views are deliberately thin.
"""

from __future__ import annotations

from logging import getLogger

from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, View

from core.services import usage_analytics

logger = getLogger(__name__)


def _staff_required(view_cls):
    """Class-decorator: gate dispatch behind ``is_active and is_staff``.

    ``user_passes_test`` is preferred over ``staff_member_required`` because
    the latter redirects to the admin login form — the dashboard lives in
    the regular Castor shell so users should land back on the normal login.
    """
    return method_decorator(
        user_passes_test(lambda u: u.is_active and u.is_staff),
        name="dispatch",
    )(view_cls)


# Tabs render in this order; the active one is highlighted in the nav strip.
TABS: list[dict[str, str]] = [
    {"slug": "overview", "label": "Overview", "url_name": "staff_dashboard_overview"},
    {"slug": "cost", "label": "Cost & Usage", "url_name": "staff_dashboard_cost"},
    {"slug": "reliability", "label": "Reliability", "url_name": "staff_dashboard_reliability"},
    {"slug": "engagement", "label": "Engagement", "url_name": "staff_dashboard_engagement"},
    {"slug": "investor", "label": "Investor", "url_name": "staff_dashboard_investor"},
    # Future tabs (Quality, Security) are wired in when each lands; the chrome
    # tolerates missing entries by only rendering tabs whose url_name resolves.
]


@_staff_required
class StaffDashboardRedirectView(LoginRequiredMixin, View):
    """Default ``/staff/dashboard/`` to the Overview tab."""

    def get(self, request, *args, **kwargs):
        return HttpResponseRedirect(reverse("staff_dashboard_overview"))


@_staff_required
class StaffDashboardBaseView(LoginRequiredMixin, TemplateView):
    """Shared chrome + window selector for every dashboard tab.

    Subclasses set ``active_tab`` and override ``get_context_data`` to add
    their tab-specific aggregates. Window days is read from ``?window=`` and
    coerced to one of {1, 7, 30}; bad values fall back to 7 silently.
    """

    active_tab: str = ""
    template_name = ""

    def _window_days(self) -> int:
        raw = self.request.GET.get("window", "7")
        if raw not in {"1", "7", "30"}:
            return 7
        return int(raw)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        window = self._window_days()
        ctx["active_tab"] = self.active_tab
        ctx["tabs"] = TABS
        ctx["window_days"] = window
        ctx["window_choices"] = [
            {"value": 1, "label": "24h"},
            {"value": 7, "label": "7d"},
            {"value": 30, "label": "30d"},
        ]
        return ctx


class OverviewView(StaffDashboardBaseView):
    """Tab 1 — Overview. The Phase 1 ship-gate page."""

    active_tab = "overview"
    template_name = "core/staff/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        window = ctx["window_days"]
        ctx["kpis"] = usage_analytics.kpis(window_days=window)
        # Stacked-area charts always show a 30-day view regardless of the
        # KPI window — short-window stacks are too sparse to be useful.
        ctx["tokens_per_day"] = usage_analytics.tokens_per_day(window_days=30)
        ctx["cost_per_day"] = usage_analytics.cost_per_day(window_days=30)
        ctx["top_users"] = usage_analytics.top_users_by_cost(
            window_days=window, limit=10
        )
        ctx["pulse"] = usage_analytics.system_pulse()
        # Soft daily budget threshold drawn as a red horizontal line on the
        # cost chart. Not enforced — purely visual. Adjust here if needed.
        ctx["daily_cost_threshold_usd"] = 10
        return ctx


class CostView(StaffDashboardBaseView):
    """Tab 2 — Cost & Usage Drill-down.

    Lenses on top of the same ``LLMCallLog`` data Tab 1 already aggregates:
    Ask vs Modify spend, Ollama-local vs paid mix (gross-margin frame),
    cost-per-active-user trend, and a per-user budget heat strip.
    """

    active_tab = "cost"
    template_name = "core/staff/cost.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # The KPI tiles use the configurable window; the trend charts always
        # render 30 days regardless — short windows are too sparse to read.
        window = ctx["window_days"]
        ctx["kpis"] = usage_analytics.kpis(window_days=window)
        ctx["cost_by_purpose"] = usage_analytics.cost_per_day_by_purpose(
            window_days=30
        )
        ctx["tokens_local_vs_paid"] = usage_analytics.tokens_per_day_local_vs_paid(
            window_days=30
        )
        ctx["cost_per_user_per_day"] = usage_analytics.cost_per_active_user_per_day(
            window_days=30
        )
        ctx["budget_strip"] = usage_analytics.user_budget_strip(
            activity_window_days=7
        )
        return ctx


class ReliabilityView(StaffDashboardBaseView):
    """Tab 3 — Reliability & Performance.

    The "is anything on fire?" view: provider success rates, p95 latency
    Ask-vs-Modify, error-type donut, unresolved error backlog, writeback
    failure taxonomy, ingestion health. KPI strip + most charts respect the
    window selector; the latency-over-time chart always spans 30 days so the
    p95 lines have enough samples to be readable.
    """

    active_tab = "reliability"
    template_name = "core/staff/reliability.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        w = ctx["window_days"]
        ctx["kpis"] = usage_analytics.kpis_for_reliability(window_days=w)
        ctx["provider_success"] = usage_analytics.provider_success_rate(
            window_days=w
        )
        ctx["p95_latency"] = usage_analytics.p95_latency_per_purpose_per_day(
            window_days=30
        )
        ctx["error_breakdown"] = usage_analytics.error_type_breakdown(
            window_days=w
        )
        ctx["error_backlog"] = usage_analytics.unresolved_error_backlog()
        ctx["failure_taxonomy"] = usage_analytics.failure_record_taxonomy(
            window_days=w
        )
        ctx["ingestion"] = usage_analytics.ingestion_status(window_days=w)
        ctx["success_threshold_pct"] = 95
        return ctx


class EngagementView(StaffDashboardBaseView):
    """Tab 4 — Engagement & Cohorts (the VC tab).

    DAU/WAU/MAU lines, time-to-first-value histograms, feature mix
    (Ask vs Modify), Modify funnel by tier, and two heatmaps — cohort
    retention and hour-of-day × day-of-week. The heatmaps render with
    Plotly (lazy-loaded via ``needs_plotly`` flag on the context); every
    other chart on the page is Chart.js so the rest of the dashboard
    family stays on the same stack.
    """

    active_tab = "engagement"
    template_name = "core/staff/engagement.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        w = ctx["window_days"]
        ctx["kpis"] = usage_analytics.engagement_kpis(window_days=w)
        # DAU/WAU/MAU + activity heatmap span a longer fixed window — the
        # KPI selector tunes the headline tiles, but the trend lines and
        # heatmap need enough samples to be readable.
        ctx["dau_wau_mau"] = usage_analytics.dau_wau_mau(window_days=60)
        ctx["ttfv"] = usage_analytics.time_to_first_value()
        ctx["feature_mix"] = usage_analytics.feature_mix(window_days=30)
        ctx["modify_funnel"] = usage_analytics.modify_funnel(window_days=30)
        ctx["activity_heatmap"] = usage_analytics.activity_heatmap(window_days=30)
        ctx["cohort_grid"] = usage_analytics.cohort_retention_grid(weeks=8)
        ctx["needs_plotly"] = True
        return ctx


class InvestorView(StaffDashboardBaseView):
    """Tab 7 — Investor View. Screenshot-friendly composition of existing helpers.

    Single page, minimal chrome inside the body, designed to be cropped or
    printed and dropped into a deck. Pulls the cohort grid from Tab 4, the
    Modify-funnel-derived acceptance rate by tier, the local-vs-paid token
    mix from Tab 2's data, and a new IFC ingestion scatter that proves the
    engine scales. The "design partner" table surfaces the engagement
    signal that isn't manual — a Likert score column is left for the
    operator to overlay by hand on the screenshot.
    """

    active_tab = "investor"
    template_name = "core/staff/investor.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["kpis"] = usage_analytics.investor_kpis()
        ctx["cohort_grid"] = usage_analytics.cohort_retention_grid(weeks=8)
        ctx["acceptance"] = usage_analytics.proposal_acceptance_rate_by_tier(
            window_days=30
        )
        ctx["provider_mix"] = usage_analytics.provider_mix_summary(window_days=30)
        ctx["ingestion_scatter"] = usage_analytics.ifc_ingestion_scatter()
        ctx["design_partners"] = usage_analytics.design_partner_engagement(
            window_days=30
        )
        ctx["needs_plotly"] = True
        return ctx
