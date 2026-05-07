# Custom BI Dashboard — Staff-Only Usage Analytics

> Operator-facing view of Castor's runtime health: LLM cost, token spend per
> user, error rate, latency, and feature engagement. Pure read against existing
> models — no new schema. Built so the operator (Carlo, plus future co-admins)
> can spot abuse, runaway cost, or a broken provider in <30 seconds during the
> beta.
>
> **Audience:** `is_staff = True` users only. Never linked from the public site.
>
> **When to build:** before Wave-1 invites in M6. Phase 1 is non-negotiable
> ("can't ship without it"); Phase 2 and Phase 3 are nice-to-have.

---

## Context

The decision tree at the top of this doc:

| Question | Answer |
|---|---|
| Do we need an external BI tool (Power BI, Metabase, Superset)? | **No.** 5–10 beta users, ~6 week window. The data fits in a Django page. |
| Do we need a new "analytics" Django app? | **No.** A handful of views + service helpers in existing apps. |
| Do we need new models? | **No.** `LLMCallLog`, `UserTokenBudget`, `BetaApplication`, `ErrorLog`, `User`, `ModificationProposal` already capture every fact the dashboard needs. |
| Charting library? | **Chart.js** via CDN (zero install, ~70 KB gzipped). If a chart needs interactivity beyond hover, swap to Plotly per-tile. |
| Refresh model? | Plain GET + manual refresh button for v1. Add HTMX 30s poll only if it materially helps the operator. |
| Web traffic (page views, referrers)? | **Out of scope for this doc.** See `## Web traffic — separate decision` at the bottom. |

The dashboard is **a query layer + a template**, not a product. Everything below is sized to that.

---

## Existing data sources (don't add models — query these)

All in `src/core/models.py` unless noted.

| Model | Lines | What it tells you |
|---|---|---|
| `LLMCallLog` | 532–585 | One row per LLM invocation. Fields: `created_at`, `user`, `provider`, `model`, `purpose` (`ask` \| `modify`), `tokens_in`, `tokens_out`, `estimated_cost_usd`, `latency_ms`, `succeeded`, `error_type`. The single most important table. |
| `UserTokenBudget` | 443–530 | Per-user daily cap state: `daily_cap`, `used_today`, `last_reset_at`, `hard_blocked`. |
| `User` (built-in) | — | `last_login`, `date_joined`, `is_active`. Drives DAU/WAU. |
| `BetaApplication` | (beta app) | Funnel: applied → approved → activated → first call. |
| `ErrorLog` | (core) | Server-side exceptions. Already in admin. |
| `ModificationProposal` | (writeback) | Tier (1/2/3), status (proposed/accepted/applied/rejected). |

Everything else (RAG queries, IFC entities, projects) is secondary and can wait.

---

## Dashboard layout

Single page at `/staff/usage/`. Top-down: KPIs → cost charts → reliability → engagement → beta funnel.

### Phase 1 — ship before Wave 1 (≈3 hours)

The minimum that makes you safe. If a beta tester runs $50 of Claude calls in an afternoon, you must see it that same afternoon.

**A. KPI strip (six tiles, last 24h / last 7d toggle)**
- Total LLM calls
- Total tokens (in + out, summed)
- Total cost USD (sum of `estimated_cost_usd`)
- Active users (DISTINCT `user_id` in `LLMCallLog`)
- Errors logged (count from `LLMCallLog.succeeded=False` plus `ErrorLog` count)
- p95 latency (from `LLMCallLog.latency_ms`)

**B. Tokens per day, stacked by provider** (line + stacked area)
- Last 30 days.
- One series per provider (`ollama`, `anthropic`, `groq`).
- Y-axis: total tokens.

**C. Cost per day, stacked by provider** (line + stacked area)
- Same shape as B but Y-axis is `estimated_cost_usd`.
- A horizontal red line at the daily-budget alert threshold (e.g. $10/day) so you can eyeball "are we burning?".

**D. Top 10 users by 7-day cost** (table)
- Username, calls, total tokens, total cost, hard_blocked flag.
- Linkable to `/admin/auth/user/<id>/` for context.

**Stop here for v1.** This is sufficient to detect abuse and runaway cost.

### Phase 2 — first 1–2 weeks of beta (≈3 hours)

**E. Provider success rate, last 7 days** (bar chart)
- One bar per provider showing % calls with `succeeded=True`.
- A red threshold line at 95% — anything below is a provider issue worth investigating.

**F. p95 latency over time, per purpose** (line chart)
- Two series: Ask, Modify.
- 30-day window, daily buckets.

**G. Feature engagement** (small bar chart + numbers)
- Ask vs Modify call counts last 7 days.
- Modify-only: proposals proposed / accepted / applied / rejected by tier (T1/T2/T3) — pulls from `ModificationProposal`.

**H. Per-user token budget heat strip** (mini list)
- Every active user: `used_today / daily_cap` as a thin progress bar.
- Red when >80%, "BLOCKED" badge when `hard_blocked=True`.

### Phase 3 — post-launch nice-to-haves

**I. Beta funnel** (`BetaApplication`-driven)
- Applied → Approved → Activated (first login) → First Ask call → First Modify call.
- Drop-off percentages between stages.

**J. Activity heatmap** (Chart.js matrix add-on or just an HTML table)
- Hour-of-day × day-of-week, cells colored by call count.

**K. Error type breakdown** (donut chart)
- Top 10 `error_type` values from `LLMCallLog` and `ErrorLog`.

---

## Architecture

### Files to create

| Path | Purpose |
|---|---|
| `src/core/services/usage_analytics.py` | All aggregate queries. Pure functions returning dicts/lists. **Where business logic lives.** |
| `src/core/views.py` (extend) | `StaffUsageDashboardView` — thin, just calls services and passes context. |
| `src/core/templates/core/staff/usage_dashboard.html` | Template with Bootstrap 5 grid + Chart.js script blocks. |
| `src/core/templates/core/staff/_dashboard_help_modal.html` | Required by Castor convention (`?` pill on every meaningful page). |
| `src/config/urls.py` (extend) | Mount at `/staff/usage/`. |
| `src/core/tests/test_usage_analytics.py` | Tests for the service functions (date math is easy to get wrong). |

**Why a `services/usage_analytics.py` module:** per `CLAUDE.md` "services own business logic, views stay thin." Aggregate ORM queries with multiple `annotate` / `aggregate` / `Trunc` calls belong in a service. The view becomes 10 lines.

### URL + access control

```python
# src/config/urls.py — under the project urlpatterns block
path("staff/usage/", StaffUsageDashboardView.as_view(), name="staff_usage"),
```

```python
# src/core/views.py
from django.contrib.auth.decorators import user_passes_test
from django.utils.decorators import method_decorator

@method_decorator(user_passes_test(lambda u: u.is_active and u.is_staff), name="dispatch")
class StaffUsageDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/staff/usage_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["kpis"] = usage_analytics.kpis(window_days=7)
        ctx["tokens_per_day"] = usage_analytics.tokens_per_day(window_days=30)
        ctx["cost_per_day"] = usage_analytics.cost_per_day(window_days=30)
        ctx["top_users"] = usage_analytics.top_users_by_cost(window_days=7, limit=10)
        return ctx
```

`user_passes_test` rather than `staff_member_required` because the latter redirects to the admin login form, which we don't want here — the dashboard is in the regular Castor shell.

### Service layer skeleton

`src/core/services/usage_analytics.py`:

```python
# core/services/usage_analytics.py
"""Aggregate queries for the staff-only usage dashboard.

Pure read; no side effects. Every function is keyed off a window in days so
the same helper drives both the 24h and 7d KPI strips.
"""
from datetime import timedelta
from django.db.models import Count, Sum, F, Avg
from django.db.models.functions import TruncDay
from django.utils import timezone

from core.models import LLMCallLog, UserTokenBudget

def _since(days: int):
    return timezone.now() - timedelta(days=days)

def kpis(window_days: int) -> dict: ...
def tokens_per_day(window_days: int) -> list[dict]: ...
def cost_per_day(window_days: int) -> list[dict]: ...
def top_users_by_cost(window_days: int, limit: int) -> list[dict]: ...
def provider_success_rate(window_days: int) -> list[dict]: ...
def p95_latency_per_purpose(window_days: int) -> list[dict]: ...
def feature_engagement(window_days: int) -> dict: ...
def per_user_budget_strip() -> list[dict]: ...
```

For p95: `LLMCallLog.objects...annotate(p95=Percentile("latency_ms", 0.95))` if the Postgres backend supports it; otherwise `Window` + `PercentRank`. Worst-case fall back to median (`p50`) — good enough for beta.

### Template structure

```
{% extends "core/base.html" %}
{% block content %}
  <!-- Heading + ? help pill (Castor convention — non-negotiable) -->
  <div class="d-flex align-items-center gap-2 mb-3">
    <h5 class="mb-0">Usage Dashboard</h5>
    <button class="help-pill" data-bs-toggle="modal"
            data-bs-target="#dashboardHelpModal" ...>
      <i class="bi bi-question-circle"></i>
    </button>
    <div class="ms-auto">
      <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">
        <i class="bi bi-arrow-clockwise"></i> Refresh
      </button>
    </div>
  </div>

  <!-- KPI tiles (Bootstrap row, 6 cards) -->
  <div class="row g-3 mb-4"> ... </div>

  <!-- Charts (each in a Bootstrap card with a canvas inside) -->
  <div class="row g-3">
    <div class="col-md-6"><canvas id="tokensPerDay"></canvas></div>
    <div class="col-md-6"><canvas id="costPerDay"></canvas></div>
  </div>

  <!-- Top users table -->
  ...

  {% include "core/staff/_dashboard_help_modal.html" %}
{% endblock %}

{% block extra_js %}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script>
    const tokensData = {{ tokens_per_day|json_script:"tokens-data" }};
    // ...standard Chart.js init...
  </script>
{% endblock %}
```

Use `json_script` (Django built-in) to safely embed query results without XSS risk.

### Linking from existing UI

- Add a "Usage" link in the navbar dropdown that's **gated by `{% if user.is_staff %}`** so it never renders for beta testers.
- File: `src/core/templates/core/components/navbar.html`.

---

## Tests

`src/core/tests/test_usage_analytics.py`:

- Build 5–10 `LLMCallLogFactory` rows with known timestamps, providers, costs.
- Assert each service function returns the expected aggregates for a known window.
- Critical edge: **window boundary correctness** (does "last 7 days" include today's noon row when run at 11pm?). Pin `timezone.now()` with `freezegun`.
- View test: anonymous → 302, regular user → 403, staff user → 200 with the expected context keys.

Don't unit-test Chart.js rendering — that's a manual smoke test.

---

## Performance budget

For 5–10 users over 6 weeks, expected `LLMCallLog` row count: ~20K. All queries should be sub-100ms with the existing default index on `created_at`.

If beta scales beyond plan and `LLMCallLog` exceeds ~500K rows, add:
- `Index(fields=["user", "-created_at"])` for top-users queries
- `Index(fields=["provider", "-created_at"])` for per-provider rollups

Don't pre-index. Wait for the slow query.

---

## Web traffic — separate decision (recommend deferring)

The dashboard above is about **what users do inside Castor**, not **how they got there**. They're different problems with different tools.

For a beta with 5–10 invited users, you already know the answer to "where did traffic come from" (your invite emails). The valuable signal is engagement (the dashboard above), not acquisition.

If you still want page-view analytics:

| Option | Tradeoff |
|---|---|
| **Plausible self-hosted** | Docker container alongside the stack, EU residency preserved, cookie-free → no consent banner. Adds 1 service to maintain. Best fit if you keep it. |
| **GoAccess on nginx logs** | Zero new services. Renders an HTML report from `/var/log/nginx/access.log`. SSH + run, not embedded. Fine for "look once a week." |
| **Tiny Django middleware → `PageVisit` model** | Zero external deps; fully embedded in the BI dashboard. ~50 lines. But you build the analyzer too. |
| **GA4 / Plausible Cloud** | Drags you back into consent banners or non-EU data export. Skip. |

**Recommendation:** skip web traffic analytics for v1. Ship Phase 1 of the BI dashboard. If after Wave 1 you actually feel blind on traffic, add the middleware + `PageVisit` model — it's the lowest-friction path that keeps everything in-stack.

---

## Build order checklist

- [ ] Phase 1.A — `usage_analytics.kpis()` + KPI strip in template
- [ ] Phase 1.B — `tokens_per_day()` + Chart.js line chart
- [ ] Phase 1.C — `cost_per_day()` + Chart.js stacked area + budget threshold line
- [ ] Phase 1.D — `top_users_by_cost()` + Bootstrap table
- [ ] Phase 1 — `?` help modal explaining each tile
- [ ] Phase 1 — Navbar link gated by `is_staff`
- [ ] Phase 1 — Service-layer tests with `freezegun`
- [ ] Phase 1 — `ruff check` + `ruff format` clean
- [ ] **GATE:** ship before first Wave-1 invite

- [ ] Phase 2.E — provider success rate
- [ ] Phase 2.F — p95 latency per purpose
- [ ] Phase 2.G — feature engagement (Ask vs Modify, proposal funnel)
- [ ] Phase 2.H — per-user budget heat strip

- [ ] Phase 3.I — beta funnel
- [ ] Phase 3.J — activity heatmap
- [ ] Phase 3.K — error type breakdown

---

## What this doc does NOT cover

- **External BI tools** (Power BI, Metabase, Looker). Out of scope; in-stack is correct at this scale.
- **Web traffic analytics** beyond the brief recommendation above.
- **Anomaly detection / alerting** (e.g. "page me if cost > $20/day"). Phase 4 candidate, post-launch. For beta, eyeballing the dashboard daily is sufficient.
- **Data export** (CSV / Parquet). `LLMCallLog` admin already has Django's CSV export action; no need to duplicate.
- **Cross-tenant analytics** (Castor is single-tenant for the beta).

---

## References

- `LLMCallLog` definition: `src/core/models.py:532-585`
- `LLMCallLog` admin: `src/core/admin.py:204-233`
- `UserTokenBudget`: `src/core/models.py:443-530`
- `SiteLLMConfig` (provider in use): `src/core/models.py:341-440`
- Service layer convention: `CLAUDE.md` § "Django Patterns"
- Help modal convention: `CLAUDE.md` § "Help modals"
- Sentry / UptimeRobot / `/healthz/` (the *other* monitoring layer this dashboard complements): `docs/business/vps-deployment.md`
