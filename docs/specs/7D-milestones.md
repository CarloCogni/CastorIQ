# 7D Facility Management — Milestone Tracker

Companion to `docs/specs/7D-facility-management.md` (the vision spec). This file is the **execution ledger** — what's shipped, what's in flight, what's next, and the review gate for each block. Keep it current at the start and end of each milestone so any session starts with a clear picture of where we are.

**Last updated:** 2026-04-17

---

## Architecture recap (locked)

- **Single `facilities/` Django app**, internally partitioned by subpackage (`models/assets.py`, `models/work.py`, `services/`, `services/ifc_mappers/`, …). Subpackages can be extracted into their own apps later if shapes stabilize.
- **Role model (`ProjectRole`) lives in `environments/`** — companion to `ProjectMembership`. Multi-role per user per project, validity windows.
- **Export Reconciliation Service** (future, M2) sits on top of existing writeback — does NOT replace it. FM ops queue as `FMDelta` rows, Export replays them against the existing tier executors, one bundled Git commit per export.
- **Facilities color:** teal `#14b8a6` (spec §5) — distinct from Ask blue (`#3b82f6`) and Modify purple (`#8b5cf6`).
- **AI features chosen for V1:** #3 Manual-to-PM-plan, #6 Narrative KPIs, #9 Replace-vs-maintain (from spec §11).

---

## Milestone status

| # | Milestone | Status | Reviewed |
|---|-----------|--------|----------|
| M0 | Scaffold + Facilities tab + ProjectRole + role-switcher + role-aware landing | ✅ Done | ⏳ Pending walk-through |
| M1 | Asset Register | ⏳ Not started | — |
| M2 | Export Reconciliation v1 | ⏳ Not started | — |
| M3 | Work Orders | ⏳ Not started | — |
| M4 | Occupant Portal | ⏳ Not started | — |
| M5 | PM Planner + Manual-to-PM (AI #3) | ⏳ Not started | — |
| M6 | Documents v2 | ⏳ Not started | — |
| M7 | Narrative KPIs (AI #6) | ⏳ Not started | — |
| M8 | Replace-vs-maintain (AI #9) | ⏳ Not started | — |

Effort legend: S = 1–2 days, M = 3–5 days, L = 1–2 weeks.

---

## M0 — Scaffold + Facilities tab + role model — ✅ Done

**Goal.** Prove the skeleton everything else hangs off: new app, tab in the navbar, role architecture, role-aware landing. End with the user able to log in under different roles and see different content.

**Delivered.**
- `facilities/` Django app with subpackage layout: `models/`, `services/`, `ifc_mappers/`, `templates/`, `tests/`, `urls.py`, `views.py`, `apps.py`, `admin.py`.
- `environments.ProjectRole` model: `(user, project, role, valid_from, valid_until)`, 9 role choices (BUILDINGOWNER, FACILITIESMANAGER, MAINTENANCEENGINEER, CONTRACTOR, SUBCONTRACTOR, TENANT, OCCUPANT, AUDITOR, CONSULTANT). Multi-role per user per project. Helper classmethod `active_for(user, project, moment=None)`.
- Migration `environments.0003_projectrole` applied.
- Admin: `ProjectRoleAdmin` (list + filters + date hierarchy), `ProjectRoleInline` on `ProjectAdmin`.
- `facilities.services.role_service.ProjectRoleService` — `active_roles()`, `resolve_active(session)`, `set_active(session, role_id)`. Session key: `facilities_active_role_id`.
- `facilities.views.FacilitiesView` (extends `ProjectTabMixin`) — renders role-aware landing via `ROLE_DASHBOARD_TEMPLATES` map.
- `facilities.views.RoleSwitchView` — HTMX `POST` endpoint that swaps the tab body.
- Templates:
  - `tabs/_facilities.html` — tab body with role-switcher bar + dashboard include
  - `components/role_switcher.html` — pill + dropdown (teal)
  - `components/dashboards/` — `_owner.html`, `_fm.html`, `_engineer.html`, `_contractor.html`, `_tenant.html`, `_auditor.html`, `_default.html`, `_header.html`, `_placeholder_card.html`
- Facilities tab in `project_detail.html` navbar with `.tab-facilities` teal active state (CSS var `--castor-facilities` added to `core/base.html`).
- URL routes: `facilities:tab` → `/facilities/<uuid:pk>/facilities/`, `facilities:role_switch` → `/facilities/<uuid:pk>/facilities/role/switch/`.
- 30 tests passing: `test_role_model.py` (9), `test_role_service.py` (11), `test_facilities_view.py` (10).

**Review gate (walk-through, pending user).**
1. Log into Django admin at `/admin/`. Open the target project (`Environments › Projects`) → **Project Memberships** inline → add the target user with permission **Editor** (or **Viewer**). Alternatively: log in as the project OWNER, open the project, choose **People** from the sidebar dropdown, and add the user by email.
2. Scroll to **Project Roles** inline at the bottom of the Project page in admin. Add **two rows**, one per functional role (e.g., one FACILITIESMANAGER, one AUDITOR). Save.
3. Log in as that user. Open the project → click the **Facilities** tab.
4. Confirm: teal Facilities tab is active; role-switcher pill shows both roles; switching swaps the dashboard without a full page reload.

**Known gotchas (fix in follow-up, not blocking M0).**
- **~~Three overlapping access concepts~~** — resolved 2026-04-18 by the permission-consolidation PR. Access now lives on `ProjectMembership.permission` only. `ProjectRole` stays as the functional-role layer. See Open debt for details.
- **ProjectRole admin change form edits one role at a time.** To give a user two functional roles, either (a) use the Project admin page's inline and add multiple rows, or (b) create two separate ProjectRole entries from the list view. This is correct — one row = one role assignment. (The People page shows these read-only for now; full CRUD lands in M1+.)

**Files touched.**
- new: `src/facilities/` (whole app)
- new: `src/environments/migrations/0003_projectrole.py`
- modified: `src/environments/models.py`, `src/environments/admin.py`, `src/environments/tests/factories.py`
- modified: `src/config/settings/base.py` (INSTALLED_APPS), `src/config/urls.py` (include)
- modified: `src/environments/templates/environments/project_detail.html` (tab added)
- modified: `src/core/templates/core/base.html` (CSS var + `.tab-facilities` rule)

---

## M1 — Asset Register — ⏳ Not started

**Goal.** The single pane of glass for every physical asset with a serial number or service obligation. Cards, not rows. The hero feature of FM.

**Deliverables.**
- `facilities/models/assets.py`: `FacilityAsset` (FK → `ifc_processor.IFCEntity`), `AssetInventory`, `Classification`, `ClassificationReference`.
- Service layer: `facilities/services/asset_service.py` (list, filter, bulk tag, bulk classify, CSV import).
- Views + URLs: `AssetListView` (card grid with filters), `AssetDetailView` (the hero card), `AssetBulkView`, `AssetCSVImportView`.
- Templates:
  - `tabs/_facilities_assets.html` — sub-tab body listing asset cards
  - `components/asset_card.html`, `components/asset_detail.html`, `components/asset_bulk_bar.html`
- Sub-nav inside the Facilities tab: `Dashboard | Assets | (placeholders for next milestones)`.
- Ask integration: FM intent routing for asset queries ("which assets are in Storey-02?") — light-touch, reuses existing `chat` service.
- Tests: model constraints, service happy/edge/failure, view access control, CSV import parsing, bulk ops.

**Review gate.** Open Facilities → Assets → browse cards; filter by classification / storey / type; multi-select + bulk-tag; import a small CSV; click into an asset and see the hero card with IFC back-reference.

**Dependencies / prerequisites.**
- Facilities sub-nav scaffold (add it in M1, not a separate prep step).
- ~~Resolve access-model debt first~~ — done 2026-04-18.
- Asset mutation endpoints must use `ProjectModifyAccessMixin` (EDITOR+); read-only endpoints use `ProjectAccessMixin`.

**Effort.** L (1–2 weeks).

---

## M2 — Export Reconciliation v1 — ⏳ Not started

**Goal.** FM writes stay in the DB, IFC is reconciled on explicit Export. First version handles asset edits (property/classification/pset) only — sensor refs + WO summaries deferred.

**Deliverables.**
- `facilities/models/exports.py`: `FMDelta(project, entity_guid, operation, payload, created_at, created_by, applied_to_ifc_at, ifc_commit_hash)`, `ExportJob`, `ExportProfile`.
- `facilities/services/export_reconciliation_service.py` — ★ the critical one. Plans → snapshots → replays deltas against existing tier executors → single `GitService.commit_modification()` call with bundled diff.
- `facilities/services/ifc_mappers/` — one mapper per FM-certified op: `SET_PROPERTY`, `ADD_PSET`, `SET_CLASSIFICATION`, `SET_ATTRIBUTE` (reuse existing writeback handlers); new FM ops (`CREATE_IFCASSET_GROUP`, `WIRE_IFCDOCUMENTREFERENCE`, etc.) come online in later milestones.
- Export IFC modal with preview (affected entities, conflict resolution UI).
- Project header banner: `⚠ N FM updates not in IFC — last export X days ago`.
- Small extension to `writeback.ModificationService`: `skip_commit=True` hook so Export drives the Git commit itself.
- Tests: delta creation on asset edit, export plan rendering, replay-against-snapshot, rollback on failure, idempotency on re-ingest.

**Review gate.** Edit an asset attribute → see dirty banner → click Export → preview → confirm → verify one new Git commit + correct IFC pset/attribute writes + all deltas marked applied.

**Effort.** M (3–5 days).

---

## M3 — Work Orders — ⏳ Not started

**Goal.** CMMS core. From "noticed something wrong" to "signed off" without leaving Castor.

**Deliverables.**
- `facilities/models/work.py`: `WorkOrder`, `ActionRequest`, `Permit`, `WorkOrderStatusEvent`, `WorkOrderAttachment`. Lifecycle: Draft → Submitted → Triaged → Assigned → Scheduled → In Progress → Completed → Verified → Closed.
- `facilities/services/workorder_service.py` — transitions, assignment, SLA clock.
- `facilities/consumers.py` — `WorkOrderConsumer` for live status push (reuses existing channels setup).
- Views: kanban board, calendar, list (filterable/exportable), detail modal.
- **AI wow (§6.2):** Intent-to-WO — natural-language input → Tier-2 RSAA plan → batch-review modal → confirm → N WOs created. Reuses existing `ModificationService` shape with a new "FM batch" tier-2 flow.
- Tests: state machine, WS push, intent-to-WO prompt + parsing (LLM mocked).

**Review gate.** Type *"Schedule filter replacement on all rooftop AHUs next Tuesday, assign to ACME HVAC, priority 2"* → batch review → confirm → 8 WOs on kanban, WS pushed to a second browser tab.

**Effort.** L (1–2 weeks).

---

## M4 — Occupant Portal — ⏳ Not started

**Goal.** Mobile-first intake page for TENANT / OCCUPANT roles. No forms — one text box.

**Deliverables.**
- Tenant landing (mobile-first): assigned space, recent requests, one text box.
- Intake view: text → Ask with narrow system prompt → draft `ActionRequest` → occupant confirms → routed to WO queue.
- Status-update page with WS push.
- Tests: role-based landing, intake prompt + parsing, `ActionRequest` creation + routing to WO.

**Review gate.** Log in as TENANT → submit *"Meeting room 3-B is cold"* → see request listed → FM sees it triaged to the HVAC queue.

**Effort.** M (3–5 days).

---

## M5 — PM Planner + Manual-to-PM (AI #3) — ⏳ Not started

**Goal.** Calendar-driven recurring tasks. Bonus: Castor reads O&M manuals and proposes PM plans.

**Deliverables.**
- `facilities/models/maintenance.py`: `MaintenancePlan`, `MaintenanceTaskInstance`, `InspectionTemplate`, `InspectionRun`, `ConditionLog`, `WorkCalendar`.
- Recurrence engine (every-N-period, quiet-hour-aware, `IfcWorkCalendar`-respecting).
- Calendar view for PM plans.
- **AI wow (§6.3):** "Propose PM plan from manual" — picks a manual from the doc store → RAG reads maintenance schedule section → drafts plan → FM approves → tasks on calendar.
- Tests: recurrence math, conflict detection (quiet hours, calendar exceptions), manual-to-PM prompt + parsing.

**Review gate.** Create a PM plan manually → verify calendar. Upload an AHU manual → trigger "Propose PM" → verify generated plan matches expected schedule → approve → tasks appear.

**Effort.** L (1–2 weeks).

---

## M6 — Documents v2 — ⏳ Not started

**Goal.** Every PDF that matters is classified, linked, and expiry-tracked.

**Deliverables.**
- Extend existing `documents` app: `DocumentAssociation` (doc ↔ asset/system/space/org), `DocumentExpiry`, doc-type classifier (manual, warranty, permit, certificate, drawing, spec, contract, invoice).
- LLM classifier at ingest (reuses existing pipeline).
- Expiry dashboard widget with traffic-light thresholds (90/30/overdue).
- Tests: classifier output, expiry calculation, widget rendering.

**Review gate.** Upload a permit PDF → auto-classifies as permit → expiry surfaces on dashboard at T-90 / T-30 / overdue.

**Effort.** M (3–5 days).

---

## M7 — Narrative KPIs (AI #6) — ⏳ Not started

**Goal.** Role-specific dashboard widgets that speak in sentences, not numbers.

**Deliverables.**
- `facilities/services/narratives/` — one narrator per widget type (PM compliance, WO backlog, SLA hit rate, cost per sq m, occupancy %, warranty leakage).
- Widget framework (replaces placeholder cards from M0 dashboards).
- Scheduled report email hook (basic).
- Tests: narrator prompt + parsing, widget rendering, fallback when data is sparse.

**Review gate.** Open FM dashboard → see numbers **and** narrative sentences like *"PM compliance dropped 12% this month, driven by HVAC — 8 overdue out of 14. Root cause: ACME HVAC unavailable Apr 8–14."*

**Effort.** M (3–5 days).

---

## M8 — Replace-vs-maintain (AI #9) — ⏳ Not started

**Goal.** Cost-curve-driven recommendation on whether to keep maintaining an asset or replace it.

**Deliverables.**
- `facilities/models/costs.py`: `CostSchedule`, `CostItem`, `LifecycleCostLedger`.
- Lifecycle curve computation per asset.
- LLM recommendation panel with source citations.
- Tests: cost aggregation, curve math, recommendation prompt + parsing.

**Review gate.** Flag a heavily-maintained asset → see recommendation (*"€14,200 spent on AHU-07 in 24 months. Replacement €22,000 with 15-yr service life. Projected 5-yr maintenance: €35,000. Recommend replacement."*) with sources.

**Effort.** M (3–5 days).

---

## Open debt

1. **~~Rationalize the three access mechanisms.~~ RESOLVED 2026-04-18.**
   Consolidated onto a two-layer model:
   - **Access tier** — `ProjectMembership` (OWNER / EDITOR / VIEWER), one row per (user, project). Partial unique index enforces a single OWNER per project.
   - **Functional role** — `ProjectRole` unchanged (multi-role per user, validity windows, FM-only).

   `Project.collaborators` M2M dropped. `Project.owner` FK kept as a denormalized cache of the OWNER membership row and switched from `on_delete=CASCADE` to `on_delete=PROTECT`. All access decisions go through `environments.services.access_service.ProjectAccessService` — `user_has_access()` method deleted. New `ProjectModifyAccessMixin` for EDITOR+ endpoints. People page (`/projects/<pk>/people/`) ships full CRUD for access tier + read-only FM roles; full FM-role CRUD deferred to M1+.

2. **WebSocket mid-session re-check** (still open). The three consumers (`chat.AskConsumer`, `writeback.ProposalConsumer`, `documents.OCRConsumer`) re-check access only at connect time. If an OWNER demotes a user mid-session, the open WebSocket stays authorized until disconnect. Next sprint: stash `membership_id + updated_at` at connect, re-check before each write-side `receive_json`.

3. **Feature-flag the Facilities tab** until M0 review gate is passed in the browser. Currently visible to every user with project access.

4. **Seed script or dev-only management command** to create a demo project with 2–3 users and varied permissions + functional roles, for the review gates in M1+.

5. **ADMIN permission tier** (deferred to post-alpha). Three tiers (OWNER / EDITOR / VIEWER) today — widen when a real second admin axis emerges (billing, integrations, secrets).

---

## Rules of the road

- Each milestone is independently mergeable, independently demo-able, independently reviewable.
- Review gates are non-optional — the user walks through the "Review gate" section for the milestone in the browser before the next one opens.
- Each milestone ships with tests (80% lines on new services, 90% on export reconciliation).
- No rework allowed — if a later milestone forces shape changes in an earlier one, stop and re-scope.
- Every new Django service reads `.claude/skills/django-service.md` first; every HTMX interaction reads `.claude/skills/htmx-patterns.md`; every test reads `.claude/skills/testing-skill.md`.
- After every milestone: `cd src && uv run pytest facilities/tests/ -v -x`, then `uv run ruff check . && uv run ruff format .`.

---

## Changelog

- **2026-04-17** — File created; M0 shipped (code + tests), review walk-through pending. Open debt documented.
- **2026-04-18** — Access-model consolidation shipped. `ProjectMembership` rewritten with OWNER / EDITOR / VIEWER permission tiers + partial unique index on OWNER. `Project.collaborators` dropped. `Project.owner` switched to `on_delete=PROTECT`. `ProjectAccessService` + `ProjectModifyAccessMixin` are the single source of truth for access decisions. People page (`projects:people`) added — full access-tier CRUD, read-only functional roles. Migration `0004_permission_consolidation` is reversible. M1 (Asset Register) now unblocked.
