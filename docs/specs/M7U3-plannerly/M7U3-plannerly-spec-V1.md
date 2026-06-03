# M7U3 — Plannerly Spec V1 (Castor-Native)

**Status:** brainstorming spec, V1 — to be re-read and implemented in the coming days.
**Supersedes:** `M7U3-plannerly-spec-V0.md` (generic Django scaffold; Castor-agnostic).
**Anchors:** MAICEN M7U3 LS1 slides (six-stage workflow, AI Estimation Illusion, C1010 class example).

---

## 0. Context — why a V1

V0 sketched a standalone Django app with CSV ingestion, OpenAI calls, and a four-app split (`projects/`, `rules/`, `checker/`, `reports/`). It was written without seeing the actual Castor codebase, so it duplicates infrastructure we already have and uses the wrong primitives (CSV instead of IFC, OpenAI instead of Ollama-default + BYOK, four apps instead of one cohesive module).

V1 keeps the assignment intent — **prove model data is trustworthy before generating an estimate** — but rebuilds it on top of Castor. The deliverable becomes a real Castor module, not a toy. The same code that ships the three MSc deliverables (Contract, Ruleset, QA Report) is also the seed of a "Model Assurance" pillar for Castor.

The MSc deadline is 2026-05-31. Implementation effort is real but tractable because most plumbing already exists.

---

## 1. The Conceptual Spine

The slides repeat one phrase three times:

**Requirements → Contract → Verification → Dashboard → Approval → Estimate.**

This is the architecture. Each arrow is a gate: you cannot reach Estimate without passing every prior stage. The whole point — what the lecture calls the **AI Estimation Illusion** — is that an LLM will produce a confident-looking estimate from any model, including a bad one. The platform's job is to make that bad path *impossible*, not just inadvisable.

In Castor terms, this becomes the third verb:

| Verb       | Pipeline                                  | Existing app                    |
|------------|-------------------------------------------|---------------------------------|
| **Ask**    | RAG over model + docs                     | `chat/`                         |
| **Modify** | RSAA write-back to IFC                    | `writeback/`                    |
| **Assess** | Spec → Rules → Verification → Estimate    | **new: `assurance/`**           |

The app is named `assurance/`. It matches the noun/verb register of `chat/writeback/facilities/` and is broader than "verification" — it covers contract authoring, verification, approval, and estimate gating.

---

## 2. What Castor Already Gives Us (do not rebuild)

Everything in this table exists. The V1 module should *consume*, not duplicate.

| Capability                    | Where it lives                                                | How we use it                                                                                |
|-------------------------------|---------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Project + membership          | `environments/models.py` → `Project`, `ProjectMembership`     | Spec is project-scoped. Approval uses OWNER/EDITOR/VIEWER roles.                              |
| Ingested IFC entities         | `ifc_processor/models.py` → `IFCEntity`                       | Verification queries `IFCEntity.objects.filter(ifc_file__project=…, ifc_type__in=…)`.        |
| Properties as JSON            | `IFCEntity.properties` (keys like `Pset_WallCommon.FireRating`, `NetArea`) | Property/quantity rules read this dict directly. No new ETL.                       |
| Element types                 | `IFCElementType`                                              | Type-level rules (e.g. type name presence) read `entity.element_type.name`.                  |
| Classifications               | `facilities/models/assets.py` → `Classification`, `ClassificationReference` | Reuse for assembly codes (C1010, Uniclass, OmniClass, NRM). One taxonomy table per project. |
| Asset overlay                 | `FacilityAsset` (nullable `ifc_entity` FK)                    | Verified entities can be promoted to `FacilityAsset` rows (closes the loop with FM).         |
| LLM service                   | `core/llm.py` → `get_llm(user, purpose, …)`                   | All LLM calls go through this. Default Ollama, BYOK Anthropic/Groq honored automatically.    |
| LLM wall-clock timeout        | `facilities/services/_llm_helpers.py` → `invoke_with_wallclock` | Wrap every `assurance` LLM call.                                                            |
| Structured-JSON pattern       | `writeback/services/slot_extractor.py`, `tier2_planner.py`    | Mirror the "prompt for exact JSON, `json.loads()` + manual validation" idiom. No Pydantic.   |
| Live progress UI              | `writeback/services/emitters.py` + Channels                   | Verification runs are async-friendly; reuse `PipelineEmitter`/WebSocket pattern for live dashboard. |
| Base template + scope classes | `core/templates/core/base.html` (`.facilities-scope`, `.modify-scope`) | Add `.assurance-scope` with a new accent (proposal: amber `#f59e0b`, "caution / verify" register). |
| Help-pill convention          | `facilities/templates/.../*_help_modal.html`                  | Every Assurance tab ships with a `?` pill + modal (CLAUDE.md non-negotiable).                |
| Toast feedback                | `core/http.py` → `trigger_toast`, `toast_response`            | Every POST/PUT/DELETE in Assurance returns visible feedback.                                 |
| Tests + factories             | `pytest`, Factory Boy, `tests/conftest.py` per app            | New `assurance/tests/` with `factories.py` mirroring facilities/writeback conventions.       |

**Things Castor does *not* have yet that this module introduces:**

- PDF export pipeline (greenfield — WeasyPrint proposed, see §9).
- Cost rates / unit pricing data model (greenfield — see §4.8).
- "Quality gate" invariant across modules (greenfield — see §8).

---

## 3. App Architecture

Single new Django app `src/assurance/`, structured like `facilities/`:

```
src/assurance/
├── models/
│   ├── __init__.py
│   ├── spec.py           # ModelSpec, ScopeItem, RequiredField
│   ├── rules.py          # Rule, RuleCheckType
│   ├── runs.py           # CheckRun, CheckResult
│   ├── reports.py        # AssuranceReport, ReportSection
│   ├── approvals.py      # Approval, ApprovalEvent
│   └── estimate.py       # RateCard, RateItem, Estimate, EstimateLine
├── services/
│   ├── __init__.py
│   ├── spec_service.py          # CRUD + AI contract draft
│   ├── rule_service.py          # CRUD + plain-English → Rule LLM call
│   ├── rule_engine.py           # The check runner (sync + async paths)
│   ├── report_service.py        # Narrative generation + PDF
│   ├── approval_service.py      # Sign-off workflow
│   ├── estimate_service.py      # Rate lookup, totaling, gate check
│   └── _llm_helpers.py          # Re-export from core or local thin wrapper
├── consumers.py                 # Channels consumer for live verification progress
├── templates/assurance/
│   ├── tabs/
│   │   ├── _assurance.html              # Tab orchestrator (six stage steps)
│   │   ├── _spec.html                   # Stage 1+2: Requirements + Contract
│   │   ├── _rules.html                  # Stage 3 setup: Rule definition
│   │   ├── _verification.html           # Stage 3 run: Verification engine
│   │   ├── _dashboard.html              # Stage 4: Results dashboard
│   │   ├── _approval.html               # Stage 5: Approval workflow
│   │   └── _estimate.html               # Stage 6: Estimate
│   └── components/
│       ├── spec_help_modal.html
│       ├── rules_help_modal.html
│       ├── verification_help_modal.html
│       ├── dashboard_help_modal.html
│       ├── approval_help_modal.html
│       ├── estimate_help_modal.html
│       ├── rule_card.html
│       ├── check_result_row.html
│       ├── stage_stepper.html           # The 6-step horizontal progress UI
│       ├── pass_fail_pill.html
│       └── report_preview.html
├── views.py
├── urls.py                              # app_name = "assurance"
├── forms.py
├── admin.py
└── tests/
    ├── conftest.py
    ├── factories.py
    └── test_*.py
```

URL registration in `config/urls.py`:

```
path("<uuid:pk>/assurance/", include("assurance.urls"))
```

Routes (illustrative):

```
<pk>/assurance/                                  → stage stepper (entry)
<pk>/assurance/spec/                             → Stage 1+2
<pk>/assurance/rules/                            → Stage 3 setup
<pk>/assurance/verify/                           → Stage 3 run
<pk>/assurance/dashboard/<run_id>/               → Stage 4
<pk>/assurance/approve/<run_id>/                 → Stage 5
<pk>/assurance/estimate/                         → Stage 6
<pk>/assurance/report/<run_id>/                  → printable + PDF
ws/projects/<pk>/assurance/<run_id>/             → live progress consumer
```

Sidebar nav: a single "Assurance" entry, scope-classed amber, sitting between Modify and Facilities.

---

## 4. Data Model

UUID PKs via `UUIDModel` base class. All models log via `logging`, no `print()`. Service-layer rule per CLAUDE.md.

### 4.1 `ModelSpec`

The "contract" for a project's verification scope. One per project; can be revised (versioned).

Fields: `project` (FK), `version` (int), `purpose` (text), `discipline` (choice: Architecture, Structural, MEP, Facade, Civil, Multi), `scope_summary` (text, AI-draftable), `created_by`, `created_at`, `is_active` (bool — only one active version per project), `narrative_notes` (text — manual additions after AI draft).

### 4.2 `ScopeItem`

What this spec covers. One project spec can cover multiple element categories.

Fields: `spec` (FK), `element_category` (text, e.g. "Panel Partition Systems"), `ifc_classes` (array of strings, e.g. `["IfcWall", "IfcWallStandardCase"]`), `classification_ref` (FK to `ClassificationReference`, e.g. C1010), `measurement_logic` (choice: GEOMETRIC, SYSTEM, PROPERTY — matches PDF slide 15), `notes` (text).

### 4.3 `RequiredField`

A field that must exist on each in-scope element for it to be estimatable.

Fields: `scope_item` (FK), `field_key` (string, e.g. `Pset_WallCommon.FireRating`, `NetArea`, `Name`), `display_name` (e.g. "Fire Rating"), `data_type` (choice: STRING, NUMBER, BOOLEAN, ENUM), `description` (text), `is_required_for_estimate` (bool, default True).

The `field_key` matches keys in `IFCEntity.properties` (or special keys like `Name`, `Tag`, `ElementType` for attributes).

### 4.4 `Rule`

A single checkable rule. Maps a requirement to a runnable check.

Fields: `spec` (FK), `scope_item` (FK), `code` (short code, e.g. "C1010-FIRE-01"), `field_key` (matches `RequiredField.field_key`), `check_type` (choice: EXISTS, NOT_EMPTY, EQUALS, NUMERIC, NUMERIC_RANGE, REGEX, IN_ENUM), `expected` (JSON — flexible per check_type), `severity` (choice: ERROR, WARNING, INFO), `description` (text — human prose, also fed to AI), `kind` (choice: AUTOMATED, MANUAL, AI_ASSISTED — slide 23), `is_active` (bool), `created_by`, `created_at`.

`kind` is critical: AUTOMATED runs in the engine, MANUAL renders as a checklist for a human reviewer (slide 24 "Generate checklists"), AI_ASSISTED runs an LLM check against context (e.g. "is the type name semantically consistent with C1010?").

### 4.5 `CheckRun`

One verification pass. Versioned so multiple runs can coexist.

Fields: `spec` (FK), `ifc_file` (FK, optional — null means "across all project IFC files"), `triggered_by`, `started_at`, `finished_at` (nullable while running), `status` (choice: PENDING, RUNNING, COMPLETED, FAILED), `total_elements`, `total_rules`, `total_checks`, `pass_count`, `fail_count`, `warning_count`, `compliance_pct` (computed), `narrative` (text — AI-generated summary, see §6).

### 4.6 `CheckResult`

Per-element, per-rule outcome. Bulk-inserted.

Fields: `run` (FK), `entity` (FK to `IFCEntity`), `rule` (FK), `passed` (bool), `severity_observed` (choice — usually mirrors rule severity but AI_ASSISTED may downgrade), `observed_value` (text), `notes` (text). Indexed on `(run, passed)` for fast dashboard queries.

### 4.7 `Approval`

Sign-off on a `CheckRun` before it gates the estimate.

Fields: `run` (FK), `decision` (choice: APPROVED, REJECTED, APPROVED_WITH_CAVEATS), `approver` (FK to User), `decided_at`, `comment` (text), `compliance_threshold` (float — what % compliance the approver required, e.g. 95.0).

Validation: only users with `ProjectMembership.role` in (OWNER, EDITOR) for the project can approve. Rejections must include a comment.

### 4.8 Rate model

`RateCard` — versioned rate book per project. Fields: `project`, `name`, `version`, `currency` (choice with reasonable defaults — EUR, USD, GBP), `is_active`, `created_at`.

`RateItem` — one line in a rate card. Fields: `rate_card` (FK), `classification_ref` (FK to `ClassificationReference`, e.g. C1010), `unit` (choice: M2, M3, EA, LM, KG), `unit_cost` (decimal), `labour_pct` (decimal — informational), `material_pct` (decimal — informational), `notes` (text).

### 4.9 `Estimate` + `EstimateLine`

`Estimate` — a generated estimate, tied to a passing `CheckRun` (or explicitly marked "unverified"). Fields: `project`, `check_run` (FK, nullable), `rate_card` (FK), `is_verified` (bool — true only if `check_run` exists and approved), `total_cost` (decimal), `total_quantity_summary` (JSON), `generated_at`, `generated_by`, `narrative` (text — AI).

`EstimateLine` — one row. Fields: `estimate` (FK), `scope_item` (FK), `rate_item` (FK), `quantity` (decimal), `unit_cost` (decimal at time of generation — snapshot), `total` (computed, stored), `element_count` (int — how many IFC entities contributed), `notes` (text).

> Decimal precision: 4 decimal places on unit costs, 2 on totals. Aligns with construction cost practice.

### 4.10 Report model

`AssuranceReport` — the bundled deliverable. Fields: `run` (FK), `approval` (FK, nullable), `estimate` (FK, nullable), `pdf_file` (FileField, nullable until generated), `html_render` (text — the rendered HTML used as PDF source), `generated_at`. The single PDF includes the contract, ruleset, verification results, approval line, and estimate.

---

## 5. The Six Stages, As UI Tabs

Each stage has a tab, a help-modal, and explicit "what done looks like" criteria.

### Stage 1 — Requirements (`/spec/`, Requirements panel)

**Purpose:** capture *why* this model needs to support an estimate, who will use it, what discipline and scope.

**Inputs:** project name (from `Project`), purpose (free text), discipline, element category list.

**AI assistance:** "Generate description" (slide 24). User provides one-line brief → LLM drafts `purpose` and `scope_summary`. User edits.

**Done when:** active `ModelSpec` row exists with at least one `ScopeItem`.

### Stage 2 — Contract (`/spec/`, Contract panel — same tab, second view)

**Purpose:** turn the requirements into a written contract section per scope item, listing the required data fields per element category.

**Inputs:** `RequiredField` rows per `ScopeItem`. User can either type them, pick from a "common fields for IfcWall" suggestion list (hardcoded short list per IFC class), or AI-generate from the scope summary.

**AI assistance:** "Draft a doc section for your element" (slide 24). LLM proposes the field list given the element category + classification code + scope summary.

**AI assistance:** "Check your technical writing" (slide 24). LLM red-pens the scope/contract for ambiguity, vague verbs ("enough", "appropriate"), and unenforceable language. This is rendered as a side panel of suggestions, not auto-applied.

**Done when:** every `ScopeItem` has ≥ 1 `RequiredField`.

### Stage 3 — Verification (`/rules/` + `/verify/`)

**Purpose:** turn requirements into runnable rules and execute them against `IFCEntity` rows.

**Two sub-views:**

- **Rules** — list/create rules. Each `RequiredField` auto-generates a "field exists" rule on first save; user can add stricter rules (numeric ranges, enum values, regex). The "Define the assembly code rule" AI (slide 24) takes a plain-English description and produces a structured `Rule` row.
- **Verify** — run the engine. Pick the IFC file (or "all in project"), kick off a `CheckRun`. Live progress via Channels (reuses `PipelineEmitter` pattern from writeback — phases: ENUMERATE → AUTOMATED → AI_ASSISTED → SUMMARIZE).

**Engine logic per rule kind:**

- AUTOMATED: pure Django ORM + Python — for each in-scope entity, read `entity.properties[field_key]` (or attribute lookup) and apply `check_type`. Fast. Bulk-insert `CheckResult` rows.
- MANUAL: emits a checklist line in the report; no automated pass/fail. Result is recorded after a human ticks it in the dashboard.
- AI_ASSISTED: per-entity (or per-cohort, batched) LLM call asking a focused yes/no with rationale. Wrapped in `invoke_with_wallclock`. Cached by entity+rule hash so re-runs are cheap.

**Manual checklist generation:** for MANUAL rules without prose, "Generate checklists" AI drafts the reviewer questions.

**Done when:** at least one `CheckRun` exists with `status=COMPLETED`.

### Stage 4 — Dashboard (`/dashboard/<run_id>/`)

**Purpose:** show, *fast*, whether the model is fit for estimating.

**Layout:**

- Top strip: total elements, pass count, fail count, warning count, compliance %, big PASS/FAIL pill.
- Donut by severity.
- Table per `ScopeItem`: rule code → pass/fail count → click-through to failing elements.
- For each failing element: GUID, ifc_type, name, which rule failed, observed value, "open in Modify" deep link (since Castor has writeback, the user can *fix* a failing property right there — this is a unique Castor superpower the original Plannerly cannot offer).
- Narrative block: AI-generated one-paragraph verdict ("Is the model usable for estimating? Why/why not?").

The dashboard is rendered live during a running `CheckRun` via WebSocket; once `COMPLETED`, it becomes a static report view.

### Stage 5 — Approval (`/approve/<run_id>/`)

**Purpose:** an EDITOR or OWNER signs off (or rejects) the verification result. Compliance threshold is explicit (default 95%; user-overridable).

**UX:** banner at top — "X% compliance, threshold Y%". Three buttons: Approve, Approve with caveats, Reject. Mandatory comment field on Reject. Approval creates an `Approval` row and unlocks Stage 6.

### Stage 6 — Estimate (`/estimate/`)

**Purpose:** convert verified quantities into a cost number.

**Inputs:** active `CheckRun`, active `Approval`, active `RateCard`. The view refuses to render the "verified estimate" path if any of these is missing — this is the **Estimation Lock** (§8).

**Layout (mirrors slide 1 mockup):**

- Hero KPIs: total estimated cost, total quantity, total line items, cost per m².
- Cost breakdown table: per `ScopeItem`, quantity, unit, unit cost, total.
- Donut: cost by classification group.
- Cost trend stub (versioned estimates over time, populates as more runs happen).
- Subtotal, contingency (configurable %, default 10%), grand total.
- "Generate narrative" AI button — drafts the estimate's executive summary.

The same view also offers a "View unverified estimate" toggle — generates an estimate from **all** in-scope elements regardless of verification, behind a red warning banner. This visualizes the AI Estimation Illusion at the UI level: you can see exactly what numbers the illusion would have produced.

---

## 6. AI Touch Points (slide 24, six of them)

All six are implemented, each as a service method with a narrow JSON contract.

| Slide ask                          | Service method                                | Trigger                                  |
|------------------------------------|-----------------------------------------------|------------------------------------------|
| Generate a description             | `spec_service.draft_purpose_and_scope(brief)` | Stage 1 button                           |
| Generate checklists                | `rule_service.draft_manual_checklist(rule)`   | Stage 3, when MANUAL rule has no prose   |
| Document set on each task          | `spec_service.suggest_documents(scope_item)`  | Stage 2 panel (links to `chat/` corpus)  |
| Define assembly code rule          | `rule_service.rule_from_prose(text, scope)`   | Stage 3, plain-English rule input        |
| Draft a doc section for an element | `spec_service.draft_field_list(scope_item)`   | Stage 2, "suggest required fields"       |
| Check your technical writing       | `spec_service.redpen(text)`                   | Stage 2 side panel + Stage 6 narrative   |

Plus two more that the engine itself needs:

| Need                               | Service method                                | Where                                    |
|------------------------------------|-----------------------------------------------|------------------------------------------|
| AI-assisted check                  | `rule_engine.run_ai_check(entity, rule)`      | Stage 3 engine, per AI_ASSISTED rule     |
| Verdict narrative                  | `report_service.write_verdict(run)`           | Stage 4 dashboard + Stage 6 report       |

Every call uses `get_llm(user, purpose=…)` and `invoke_with_wallclock` for hard timeouts. JSON contracts are short and explicit (mirror `slot_extractor.py`). All prompts live in `assurance/services/prompts.py`.

---

## 7. The Three Check Kinds (slide 23) — mapping to engine paths

| Kind           | What it does                                                              | Implementation                                                                       |
|----------------|---------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| AUTOMATED      | Property/attribute presence + value rules.                                | Pure Python; iterate in-scope entities; read `entity.properties[field_key]`.        |
| MANUAL         | Modelling-method questions a human must answer.                           | Checklist UI in dashboard; reviewer ticks results; recorded as `CheckResult` rows.   |
| AI_ASSISTED    | Semantic checks (e.g. "is the type name internally consistent?").         | LLM per entity (or batched); cached by entity+rule hash.                             |

This three-way split is *the* lecture's main schema (slide 23). Reflecting it explicitly in the data model is more honest than the V0 "everything is automatic" assumption.

---

## 8. The Estimation Lock

A hard invariant: **a verified estimate cannot exist without an approved CheckRun for the same project + scope + IFC file generation.**

Enforced at three levels:

1. **Service layer:** `estimate_service.generate(estimate_spec)` raises `EstimationLocked` if the latest `Approval.decision != APPROVED` or `compliance_pct < threshold`.
2. **View layer:** the Estimate tab is grey-disabled until an approval exists; deep-links return 403 with a toast.
3. **PDF report:** if you generate a report on an unverified run, the cover page is stamped "UNVERIFIED — for review only" and the cost section is omitted.

The "view unverified estimate" toggle bypasses the lock *only* in-UI and *only* for the current session, with a red warning banner. The result cannot be persisted as an `Estimate` row — this preserves the audit trail.

This is the structural argument against the AI Estimation Illusion. You can show an unverified estimate, but you cannot save one. Castor remembers only verified numbers.

---

## 9. Reports & PDF Export

**Library:** WeasyPrint (HTML/CSS → PDF). Pure Python, well-maintained, plays nicely with Django templates. New dependency: `uv add weasyprint`. Windows quirks (GTK runtime) — document in `docs/operations/`.

**Template:** `assurance/templates/assurance/report/full_report.html`. Print stylesheet sets page size, margins, headers/footers. Renders in five sections:

1. **Cover** — project, spec version, run ID, date, approver, big PASS/FAIL/UNVERIFIED stamp.
2. **Contract** — `ModelSpec.purpose`, `scope_summary`, per-`ScopeItem` required fields table.
3. **Ruleset** — every `Rule` grouped by `ScopeItem`, with code/field/check/severity/kind.
4. **Verification results** — summary KPIs, donut, per-rule pass/fail table, top 20 failing elements.
5. **Estimate** — line items, breakdown, donut, total. Omitted if unverified.

**For MVP**, render the same template as a printable HTML page (browser "Save as PDF" works) and add WeasyPrint server-side export as a follow-up. This unblocks the assignment without a Windows GTK install dependency.

**The three assignment deliverables map to slices of this report:**

| Deliverable                       | Section(s)            | URL                                                   |
|-----------------------------------|-----------------------|-------------------------------------------------------|
| AI-assisted contract              | 1 + 2                 | `/assurance/report/<run_id>/?section=contract`        |
| AI-generated ruleset              | 3                     | `/assurance/report/<run_id>/?section=ruleset`         |
| Combined QA report                | 1 + 2 + 3 + 4 (+ 5)   | `/assurance/report/<run_id>/`                         |

---

## 10. Sample Demo Scenario (matches lecture, slide 19+22)

The lecture's reference scenario is implemented as a fixture in `assurance/tests/factories.py` and seedable via a management command `seed_assurance_demo`:

| Field                       | Value                                                        |
|-----------------------------|--------------------------------------------------------------|
| Project name                | Office Block QA                                              |
| Discipline                  | Architecture                                                 |
| ScopeItem element category  | Panel Partition Systems                                      |
| ScopeItem ifc_classes       | `["IfcWall", "IfcWallStandardCase"]`                         |
| ScopeItem classification    | C1010 (Uniclass)                                             |
| ScopeItem measurement logic | GEOMETRIC                                                    |
| RequiredFields              | Name, Assembly Code, NetArea, NetVolume, Pset_WallCommon.FireRating, Pset_WallCommon.AcousticRating |
| Demo IFC file               | A small office floor with ~85 walls, ~4 intentionally incomplete (to trigger failures) |
| Expected outcome            | 81 verified, 4 failures (matches slide 25's "81 verified wall elements") |

The demo IFC file is committed to `tests/fixtures/ifc/office_block_qa.ifc` (or generated programmatically with ifcopenshell if checking in a real IFC is too heavy).

---

## 11. Implementation Milestones

Conservative ordering. Each milestone is reviewable in isolation. MSc deadline = M3 completion.

### M0 — App scaffold (1 day)
- Create `src/assurance/` skeleton (models/services/templates/urls/views/forms/admin/tests).
- Add to `INSTALLED_APPS`, wire URL `path("<uuid:pk>/assurance/", include(…))`.
- Sidebar nav entry, `.assurance-scope` accent in `core/base.html`.
- Empty stage stepper UI with six greyed tabs.
- Help modal for the landing tab.

### M1 — Spec + Contract (Stage 1 + 2) (2–3 days)
- Models: `ModelSpec`, `ScopeItem`, `RequiredField` + factories + migrations.
- `spec_service`: CRUD, `draft_purpose_and_scope`, `draft_field_list`, `redpen`.
- Stage 1+2 UI: form + AI-draft buttons + side-panel red-pen suggestions.
- Help modal: Spec.
- Tests: service unit tests, view smoke tests.

### M2 — Rules + Engine (Stage 3) (3–4 days)
- Models: `Rule`, `CheckRun`, `CheckResult` + factories + migrations.
- `rule_service`: CRUD, `rule_from_prose`, `draft_manual_checklist`.
- `rule_engine`: AUTOMATED + MANUAL + AI_ASSISTED paths, sync first.
- Stage 3 UI: rule list, plain-English input, "run" button, progress bar (sync render to start).
- Help modals: Rules, Verification.
- Tests: engine correctness against fixture entities (presence, numeric, regex), AI mock.

### M3 — Dashboard + Report MVP (Stage 4) (2 days) — **MSc deadline target**
- Models: `AssuranceReport`.
- `report_service`: `write_verdict`, HTML render.
- Stage 4 UI: KPIs, donut (Chart.js), failing-element table, "open in Modify" deep link.
- Report view (printable HTML — browser-PDF acceptable for assignment).
- Help modal: Dashboard.
- Demo seed command + fixture IFC.
- Tests: end-to-end run through the demo scenario.

> **Assignment is shippable at end of M3.** The three deliverables are slices of the report endpoint.

### M4 — Approval (Stage 5) (1–2 days)
- Model: `Approval`, `ApprovalEvent`.
- `approval_service` with role checks.
- Stage 5 UI: banner, three buttons, comment box.
- Help modal: Approval.
- Tests.

### M5 — Estimate + Lock (Stage 6) (3–4 days)
- Models: `RateCard`, `RateItem`, `Estimate`, `EstimateLine`.
- `estimate_service`: generate, lock-check, narrative.
- Stage 6 UI: KPIs, breakdown table, donut, unverified-toggle banner.
- Help modal: Estimate.
- Tests: lock invariant, line-total arithmetic, unverified toggle behavior.

### M6 — Live progress + WeasyPrint (2–3 days, post-deadline)
- Channels consumer for `CheckRun` progress, mirroring writeback emitters.
- WeasyPrint pipeline + Windows GTK runbook.
- Persist `AssuranceReport.pdf_file`.

### M7 — Polish + Future Hooks (open-ended)
- Rule library (cross-project reuse).
- Multi-discipline approvals (per-`ScopeItem` sign-off).
- Promotion of verified entities to `FacilityAsset` rows.
- Integration with `chat/` for "Ask Castor about this estimate".

---

## 12. Open Questions / Future Expansions

- **Rate API.** External rate book (e.g. PBS / Spon's). For V1 we stay manual / CSV-uploaded; document a `RateProvider` interface for later.
- **Multi-model clash.** When team A's MEP model contradicts team B's structural (slide 10–12), we want a cross-model rule. Out of scope for V1 but the `Rule` model could grow a `cross_ifc_file` flag.
- **Embedding-based rule suggestion.** Castor already has 1024d entity embeddings. We could cluster entities and propose "rules for elements in this cluster" — a richer AI assistance than the V0 prose-only path.
- **Modify-link feedback loop.** Failures in Stage 4 already deep-link to `writeback/`. A future version could one-click *open a Modify proposal* pre-filled with the failing field. This is the killer feature versus Plannerly.
- **Audit trail / sign-off history.** `ApprovalEvent` is a stub; eventually every state transition lands there.

---

## 13. Risks & Things to Watch

- **WeasyPrint on Windows** — needs GTK. Mitigate by shipping the HTML report path first and PDF as a follow-up. Alternative: server-side Playwright (heavier).
- **AI_ASSISTED checks are slow.** Cap batch size, cache by `(entity_id, rule_id, properties_hash)`, prefer Ollama with `num_predict` caps. Reuse the `invoke_with_wallclock` helper.
- **Properties JSON key drift.** `IFCEntity.properties` keys are sourced from the parser — if the parser changes its key format, rules break. Add a contract test that exercises a representative IFC and asserts a known key set exists.
- **Compliance threshold is a number, not a policy.** Users will set 60% and call it done. The help modal should be unambiguous: this is an *advisory*, not a guarantee.
- **Scope creep into estimating practice.** Per the lecture, *we are not turning users into estimators*. The estimate tab is a demonstration, not a tender-ready output. Help-modal copy must say so explicitly.

---

## 14. Mapping back to the assignment

The MSc M7U3 LS1 brief requires three Plannerly artifacts:

| Plannerly artifact                       | V1 source                                                                |
|------------------------------------------|--------------------------------------------------------------------------|
| Project contract document (AI-assisted)  | `ModelSpec` + `RequiredField` rendered as report sections 1+2.           |
| AI-generated model checking ruleset      | `Rule` rows generated via `rule_from_prose` rendered as report section 3.|
| Combined QA report                       | The full report endpoint (sections 1–4 mandatory, 5 if approved).        |

Defence narrative for the panel (matches the lecture vocabulary): *"Plannerly's role is to make the model trustworthy before any estimate is generated — that is what the six-stage workflow does. Rather than use Plannerly, I built the same six-stage workflow directly into Castor, my own AI-for-BIM platform. The same module that produces the three required deliverables also gates the cost-estimating dashboard against unverified data, which is a tighter expression of the AI Estimation Illusion than Plannerly itself enforces."*

---

## 15. Quick reference — files touched at M3 (assignment-shippable cut)

- `src/assurance/` (new app, full tree above, but only Stage 1–4 services + UI active)
- `src/config/urls.py` — add include
- `src/config/settings/base.py` — `INSTALLED_APPS += ["assurance"]`
- `src/core/templates/core/base.html` — add `.assurance-scope` accent
- `src/core/templates/core/_sidebar.html` (or equivalent) — add nav entry
- `src/assurance/tests/fixtures/ifc/office_block_qa.ifc` — demo file
- `src/assurance/management/commands/seed_assurance_demo.py` — fixture command

---

## 16. Verification (how I'll know it works)

End-to-end demo test (one Python integration test, runnable on CI):

1. Run `seed_assurance_demo` against an empty project → creates project, IFC, spec, scope, fields, rules.
2. Hit `/assurance/verify/` POST → run completes in < 30 s.
3. Assert `CheckRun.pass_count == 81` and `fail_count == 4`.
4. Hit `/assurance/dashboard/<run_id>/` → 200 OK, KPIs in HTML.
5. POST `/assurance/approve/<run_id>/` with `decision=APPROVED` → 200 OK.
6. Hit `/assurance/estimate/` → 200 OK, lines render, totals correct.
7. Hit `/assurance/report/<run_id>/` → HTML report renders with all five sections.

Plus unit tests per service, plus a contract test asserting `IFCEntity.properties` keys haven't drifted.

---

*End V1 spec. Re-read in a few days, then start at M0.*
