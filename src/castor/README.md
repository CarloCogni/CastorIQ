# Schedule Intelligence & EVM (4D / 5D)

**Module:** `src/castor/` — mounts at `/castor/` in the platform URL space.

---

## Overview

This module turns a raw P6 XML or CSV schedule into a live project-controls
dashboard. Upload a Primavera P6 XML export (or a structured CSV), and the
platform computes EVM, earned schedule, probabilistic completion forecasts,
schedule quality checks, and an AI-powered controls chat — all referenced
to the P6 data date, not today's date.

The IFC 3D model is also linked: tasks can be bound to IFC elements, enabling
a 4D timeline and a spatial view of progress. That link is used today for the
IFC Insights panel and a floor-level progress ring; the geometry-driven
analytics described in "Coming Soon" build on the same binding.

**Value proposition:** A project controls analyst, a site engineer, and a
project director all see the same numbers — computed from the same P6 XML file,
with every metric grounded in the same data date. The AI chat can answer
natural-language questions over those numbers without you having to export
anything to Excel.

---

## Tech stack

| Layer | Technology | Version |
|---|---|---|
| Framework | Django | ≥ 5.0 |
| Language | Python | ≥ 3.11 (3.13 in dev) |
| Database | PostgreSQL + pgvector | 16 |
| ORM driver | psycopg | ≥ 3.1 |
| IFC parsing | IfcOpenShell | ≥ 0.7 |
| Schedule import | Custom P6 XML / CSV / MSP parsers | — |
| LLM runtime | Ollama (local), Anthropic Claude, Groq | switchable |
| LLM orchestration | LangChain, langchain-ollama/anthropic/groq | ≥ 0.1 |
| ML | Pure NumPy logistic regression (no sklearn) | — |
| Async / ASGI | Daphne + Django Channels | ≥ 4.3 |
| Front-end | HTMX 1.9.10, Bootstrap 5.3.3, Bootstrap Icons 1.11.3 | CDN |
| Charts | HTML5 Canvas (custom — no Chart.js dependency) | — |
| Reports | python-docx, reportlab (PDF/Word generation) | ≥ 1.1 |

The LLM provider is selected via `get_llm(purpose=..., user=...)` in
`core/llm.py`. Switching from Ollama to Anthropic or Groq is a config change,
not a code change.

---

## How it works

```
P6 XML / CSV / MSP
        │
        ▼
  xer_parser.py         p6xml_parser.py          excel_parser.py
  msp_parser.py         ──────────────────────────────────────────
        │             Parse tasks, resources, calendars, WBS, deps
        ▼
  p6_save.py + TaskSaveView
        │             Write Task, TaskDependency, ScheduleSource,
        │             P6ResourceAssignment, P6Calendar to PostgreSQL
        ▼
  calendar_utils.py    Working-day calendars (P6 shifts → Python sets)
        │
  critical_path.py     Progress-aware CPM forward/backward pass
        │             Completed tasks anchored at actual dates;
        │             in-progress anchored at data_date + remaining;
        │             not-started floored at data_date.
        │
  evm.py               EVM engine — computes PV/EV/AC/SPI/CPI/EAC/VAC
        │             as-of the P6 DataDate. AC from P6ResourceAssignment
        │             only; disabled with a clear notice if absent.
        │
  ┌─────┴──────────────────────────────────────────────────┐
  │             Analytics services (all read-only)          │
  │  monte_carlo.py      PERT forward-pass simulation       │
  │  delay_rootcause.py  DAG-based root-cause tracing       │
  │  anomaly_detect.py   Cross-sectional stall detection    │
  │  dcma_check.py       DCMA 14-point schedule health      │
  │  completion_ml.py    Logistic regression P(on-time)     │
  │  cashflow.py         Spend curve forecast               │
  │  floor_health.py     Per-floor build/status matrix      │
  │  evm_engine.py       Earned Schedule + WBS risk scores  │
  │  trend_engine.py     SPI/CPI sparkline history          │
  │  timelocation.py     Flowline (time-location chart)     │
  │  decision_layer.py   Executive plain-language summary   │
  │  controls_chat.py    LLM chat over the KPI snapshot     │
  └─────────────────────────────────────────────────────────┘
        │
  EVMDataView / ScheduleIntelligenceView / MonteCarloView …
        │             JSON endpoints consumed by the EVM tab
        ▼
  evm.html              Single-page dashboard (HTMX + Canvas charts)
```

The front-end makes parallel async fetches to the JSON endpoints on page load.
Heavy computations (Monte Carlo, delay root-cause) are triggered on demand via
a "Run" button or are run automatically inside the Decision Summary.

---

## Features

- **EVM core** — PV, EV, AC, SPI, CPI, EAC, VAC, SV, CV; duration-weighted when
  no costs are imported, monetary when P6 resource assignments are present.
- **Earned Schedule** — ES, AT, SPI(t), SV(t), IEAC(t); time-based counterpart
  to cost-based EVM; less sensitive to early cost distortions.
- **Monte Carlo CPM forecast** — 1,000 PERT-sampled forward-pass simulations
  over the critical path (capped at 500 tasks for performance); returns
  P50/P80/P95 completion dates. The AI chat uses a lighter 250-simulation pass.
- **DCMA 14-point schedule health** — scored 0–100; checks logic density,
  leads/lags, missing durations, high float, unrealistic dates, and more.
- **Delay root-cause analysis** — DAG traversal from delayed tasks to the
  originating bottleneck; ranks by downstream task count.
- **Anomaly detection** — cross-sectional (single snapshot): running-long tasks,
  statistical outliers by trade, FS logic violations.
- **Completion probability (ML)** — logistic regression trained on completed
  tasks in the same project; predicts P(on-time) for near-critical incomplete tasks.
- **Cash flow forecast** — planned vs actual spend curve; peak month, burn rate.
- **Floor health matrix** — per-floor build quality vs work-performance heat-map
  using the IFC floor link.
- **WBS risk scores** — per-stage performance gap, overrun %, critical task
  density, ranked 0–100.
- **Decision Summary** — three-block plain-English executive read: where is
  the project headed, is the schedule built correctly, what needs attention today.
- **AI Controls Chat** — natural-language Q&A over the full KPI snapshot; the
  LLM system prompt is built from live EVM + schedule data on every request,
  with prompt caching for consecutive questions.
- **S-curve** — cumulative PV/EV/AC with Canvas-rendered chart.
- **Trend analysis** — 12-week SPI/CPI sparklines, TCPI, recovery index.
- **Time-Location (Flowline)** — per-trade progress lines across floors.
- **Schedule audit** — AI-assisted CSI MasterFormat section mismatch detection.
- **4D link / IFC binding** — tasks linked to IFC elements by activity code or
  manual review; used for the floor ring and the Coming Soon geometry views.

---

## Design principles

**Why the numbers are trustworthy (within their stated limits):**

1. **File as truth.** Re-importing the same P6 XML always produces the same
   result. No manual overrides are layered on top of the raw import.

2. **Everything computed as-of the P6 data date.** `calendar_utils.get_project_data_date()`
   returns the `DataDate` from the P6 XML. Every metric — EV, CPM float,
   forecast dates — is evaluated at that point in time, not today. CSV imports
   without a data date fall back to `date.today()` and show a banner warning.

3. **Real earned value from % complete.** EV = (percent_complete / 100) × BAC.
   If the import has no `%_complete` column, the linear cap rule applies
   (active tasks past their planned end get 100% EV with a visible warning).
   No synthetic estimates.

4. **Working-day calendars.** All duration arithmetic uses the P6 calendar
   shifts extracted from the XML. "5 working days" means 5 actual work days
   on that calendar — not 5 calendar days.

5. **Progress-aware CPM.** Completed tasks are anchored at their actual finish
   dates; in-progress tasks are anchored at the data date with remaining
   duration computed from % complete; not-started tasks are floored at the
   data date. The forward/backward pass does not place any task before its
   actual history.

6. **Zero fabrication.** Any metric that requires data that was not imported
   (actual cost, % complete, resource assignments) is explicitly disabled
   with a grey "N/A" card and a clear notice. It is never estimated or
   defaulted to a plausible-looking number.

---

## Current status and limitations

**Read this before demoing to a client or a project director.**

- **Forecasts are preliminary.** The SPI extrapolation, Earned Schedule IEAC,
  and Monte Carlo P80 date have not been validated row-by-row against a
  Primavera report on the same data. They have not been reviewed by a
  qualified Project Controls professional.

- **SPI and Monte Carlo can diverge significantly** (the IBS pilot shows SPI
  reading 1.17 — apparently ahead — while MC P80 lands ~7 months late). The
  UI explains why (completed-work distortion vs remaining critical path) and
  leads with MC when the divergence is large. Both are still **preliminary**.

  > ⚠️ **Before sharing outside the team:** "IBS" is named here explicitly.
  > Generalise to "a pilot project" if this README goes beyond our three-person team.

- **Do not present forecast dates as confirmed.** Use language like "the
  network simulation projects ~Jul 2027 (P80) — pending expert review."

- **The ML model is trained on the same project's completed tasks.** With
  fewer than 50 completed tasks the model will not train; with fewer than
  200 the AUC will be low (shown in the UI). It is a relative risk signal,
  not an absolute probability.

- **Cash flow is a mechanical projection.** It uses the schedule dates and
  P6 resource assignments, if present. It does not account for commercial
  terms, payment terms, or procurement lead times.

- **The AI chat hallucinates if you ask outside the data.** The system prompt
  contains the full KPI snapshot; the LLM is instructed to answer only from
  that data. Questions about matters not in the snapshot (e.g. contract
  details) will produce a refusal or a hallucination.

---

## Running it / where the code lives

### Prerequisites

```bash
# Python 3.11+ and uv
# PostgreSQL 16 running on port 5432 (or 5433 in dev)
# Ollama running locally (or set ANTHROPIC_API_KEY / GROQ_API_KEY)
```

### Start the development server

```bash
cd src
uv run python manage.py runserver --noreload 8001
# Open http://127.0.0.1:8001/
```

### Import a schedule and open the EVM screen

1. Log in (`/login/`) — two-step: username, then password.
2. Open a project → **Castor 4D/5D Controls** tab.
3. Inside the panel, click **Schedule** → **Data Sources** tab.
4. Upload a P6 XML export or a structured CSV.
5. Click the **EVM** tab — the dashboard loads automatically.

The EVM tab fires ~12 parallel async fetches on page load. The **Decision
Summary** (top two cards) runs a full Monte Carlo internally and takes
**30 seconds on small schedules, up to ~3 minutes on the largest** (e.g. a
~9,000-task P6 import). A spinner shows while it computes.

### Key service files

```
src/castor/scheduling/services/
  calendar_utils.py       Working-day calendar arithmetic
  critical_path.py        Progress-aware CPM (forward + backward pass)
  evm.py                  Core EVM engine (SPI/CPI/EAC/VAC/PV/EV/AC)
  evm_engine.py           Earned Schedule + WBS risk + critical-path tab
  monte_carlo.py          PERT-based Monte Carlo simulation (1,000 runs default)
  delay_rootcause.py      DAG root-cause tracing
  anomaly_detect.py       Cross-sectional anomaly detection
  dcma_check.py           DCMA 14-point schedule health
  completion_ml.py        Logistic regression completion probability
  cashflow.py             Cash flow forecast
  floor_health.py         Per-floor health matrix
  timelocation.py         Time-location flowline
  trend_engine.py         SPI/CPI trend history
  decision_layer.py       Executive decision summary (assembles all services)
  controls_chat.py        AI controls chat (LLM over live KPI snapshot)
  schedule_audit.py       AI-assisted CSI section mismatch audit
  report_generator.py     PDF / Word export
```

### Key JSON endpoints (all under `/castor/projects/<pk>/schedule/`)

| Endpoint | Purpose |
|---|---|
| `evm/` | Core EVM KPIs + S-curve series |
| `intelligence/` | Earned Schedule, CPM, WBS risk scores |
| `monte-carlo/` | P50/P80/P95 completion dates + histogram (1,000 runs) |
| `delay-rootcause/` | Root-cause tree + cluster breakdown |
| `anomalies/` | Running-long, outliers, logic errors |
| `dcma-check/` | 14-point score + check breakdown |
| `completion-ml/` | At-risk watchlist + feature importance |
| `cashflow/` | Monthly spend curve |
| `floor-health/` | Floor × (quality, status) matrix |
| `decision-summary/` | Three-block executive summary (runs MC internally) |
| `chat/` | AI controls chat (POST: `{"question": "..."}`) |
| `trend-analysis/` | 12-week SPI/CPI history |
| `timelocation/` | Flowline data |

### Run the tests

```bash
cd src
uv run pytest castor/scheduling/tests/ castor/tests/ -q
# 186 tests, ~2 min on the IBS project database
```

---

## Coming Soon — True 4D/5D on the Model

The calculation layer is in place. The colormap infrastructure also exists
today (the viewer accepts a per-element color map from the backend via
`/castor/projects/<pk>/viewer/colormap/`; the current `schedule_status`
mode colors elements green if they have a schedule link, grey if not). The
next phase extends that infrastructure to drive the geometry from live task
status and cost data.

### 1. Progress overlay on the 3D model

Elements colored by **schedule status** as-of the P6 data date: green
(complete), amber (in-progress), red (late/overdue), grey (not started).
The current `schedule_status` colormap shows schedule linkage (linked/not
linked); this screen adds the actual task-status dimension — connecting each
element's color to its linked task's current %-complete and CPM status.
A date-slider lets you step through the timeline and watch status change.

### 2. 4D sequence simulation

Play the construction timeline and watch the model build in schedule order.
Each task's linked IFC elements appear at their planned (or actual) start
date and are highlighted as the playhead moves. The critical path is
highlighted in red on the geometry, so a reviewer can immediately see which
structural sequence is driving the finish date.

### 3. 5D cost on the model

Elements shaded by cost performance: planned cost (PV) vs earned value (EV)
vs actual cost (AC). A floor-by-floor cost waterfall is tied directly to the
geometry, so over-spending on a specific trade or zone is visible spatially
rather than only in a table. The cash flow forecast is anchored to the
geometry-derived quantities.

### 4. Element inspector

Click any IFC element to open a side-panel showing its linked task, current
%-complete, SPI and CPI, total float, and the delay root-cause chain affecting
it. The same panel surfaces any anomaly flags (running long, statistical
outlier) and DCMA violations associated with the task.

### 5. Delay and risk heatmap on the model

DCMA failures, anomaly detections, and delay root-cause origins shown
spatially — a floor plan colored by risk concentration. A project director
can immediately see that floors 5–8 are the hot zone without reading a table.
The heatmap updates when the data date changes.

### 6. Time-space clash detection

Tasks that are scheduled to execute in the same physical zone at the same
time are highlighted as potential clashes. Out-of-sequence work (a finishing
trade starting before the preceding structural work is complete in the same
zone) is flagged in the model view before it becomes a site problem.
