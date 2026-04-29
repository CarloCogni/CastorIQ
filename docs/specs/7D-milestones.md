# 7D Facility Management — Milestone Tracker

Companion to `docs/specs/7D-facility-management.md` (the vision spec). This file is the **execution ledger** — what's shipped, what's in flight, what's next, and the review gate for each block. Keep it current at the start and end of each milestone so any session starts with a clear picture of where we are.

**Last updated:** 2026-04-26

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
| M1 | Asset Register | ✅ Done | ⏳ Pending walk-through |
| M2 | Export Reconciliation v1 | ✅ Done | ⏳ Pending walk-through |
| M3 | Work Orders | ✅ Done | ⏳ Pending walk-through |
| M4 | Occupant Portal | ✅ Done | ⏳ Pending walk-through |
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

## M1 — Asset Register — ✅ Done

**Goal.** The single pane of glass for every physical asset with a serial number or service obligation. Cards, not rows. The hero feature of FM.

**Delivered.**
- `facilities/models/assets.py`: `FacilityAsset`, `AssetInventory`, `Classification`, `ClassificationReference`. `FacilityAsset.ifc_entity` is **nullable** to accommodate orphan assets (see "Beyond spec" below); `(project, ifc_entity)` uniqueness is conditional on the link existing.
- `facilities/services/asset_service.py`: `list_assets` (q, ifc_type, spatial_id, classification_refs, responsible_party, linkage filter, include_decommissioned), `get_asset`, `create_asset` + `bulk_promote`, `update_asset`, `delete_asset`, `bulk_classify`, `bulk_set_responsible_party`, `import_csv` (dry-run + commit), `create_orphan`, `list_promotion_candidates`.
- Views + URLs:
  - `AssetListView` — card grid with filters, HTMX fragment swap on `HX-Request`
  - `AssetDetailView` — hero card with spatial breadcrumb + inline edit form
  - `AssetPromoteView` — drawer listing promotable IFC entities, bulk promote on POST
  - `AssetCreateManualView` — drawer to create an orphan asset (see "Beyond spec")
  - `AssetUpdateView` — HTMX single-field/subset updates
  - `AssetBulkView` — classify / set-responsible-party / delete
  - `AssetCSVImportView` — multipart upload, dry-run preview, commit on confirm
  - Mutation endpoints use `ProjectModifyAccessMixin`; list/detail use `ProjectTabMixin` (inherits `ProjectAccessMixin`).
- Templates:
  - `tabs/_facilities_assets.html`, `tabs/_facilities_asset_detail.html`
  - `components/asset_card.html`, `asset_detail.html`, `asset_grid.html`, `asset_bulk_bar.html`, `asset_filter_bar.html`
  - `components/asset_promote_drawer.html`, `asset_create_manual_drawer.html` (beyond spec)
  - `components/asset_import_modal.html`, `asset_import_result.html`
  - `components/asset_register_help_modal.html` (beyond spec) — inline "how it works" reference next to the Asset Register heading
- Sub-nav inside the Facilities tab: Dashboard | Assets | disabled placeholders (Spaces, Work, Maintenance, Systems, Sensors, Documents, People, Costs, Reports).
- Ask integration: `chat.services.rag_service.RAGService._detect_intent` classifies FM phrasing ("which assets are in Storey-02?", "list assets with expired warranty", …) as `fm_asset_query` and the asset-context block renders key fields. Tests in `facilities/tests/test_fm_intent_routing.py`.
- Admin: `FacilityAssetAdmin` with linkage-aware fieldsets, raw_id_fields for `ifc_entity` / `spatial_container` / `responsible_party`, `AssetInventoryInline`, classification horizontal filter.
- 155 tests passing: `test_asset_model.py`, `test_asset_service.py`, `test_asset_views.py`, `test_asset_csv_import.py`, `test_fm_intent_routing.py` (plus M0 tests untouched).
- Migrations: `facilities.0001_initial` (baseline, earlier commit) → `0002_alter_facilityasset_options_and_more` (orphan fields + initial constraints) → `0003_remove_facilityasset_..._and_more` (relaxed orphan constraint: `name` required, `ifc_type` optional).

**Beyond spec — orphan asset support (added 2026-04-21).** The spec assumed every asset is an IFCEntity overlay. Real FM needs looser coupling for items that aren't modelled — LOD cutoffs (fire extinguishers, signage, loose furniture), post-handover additions, non-building assets (vehicles, consumables), and sub-components of IFC entities. Decision taken this milestone:
- `FacilityAsset.ifc_entity` is nullable. Orphans carry `name` (required) + `ifc_type` (optional free text, not enforced as a real IFC type) + optional `spatial_container` FK + `location_text` fallback.
- Invariant preserved: at most one IFC link per asset (conditional unique); if an orphan later becomes modelled, link the existing row rather than create a duplicate (no dual-existence — see `feedback_no_duplication`).
- New manual-create drawer + "Add manual asset" button on the register and empty state. CSV import accepts orphan rows (global_id blank, name non-empty).
- Asset cards / detail use `display_name` / `display_ifc_type` / `display_spatial_container` and show a "Not in IFC model" badge for orphans. "View in IFC" is hidden for orphans.

**Beyond spec — implicit-owner fallback (added 2026-04-21).** Project owners with no explicit `ProjectRole` were seeing the "Ask a project owner or facilities manager to grant you a role" empty state, which is absurd when they *are* the project owner. `FacilitiesView._role_context` now synthesizes an implicit-owner state — renders the FM dashboard, labels the role-switcher pill as "Project Owner — implicit view" with a link to the People page. No DB writes, no auto-assigned roles; pure view-layer. Stale Django-admin reference in `_default.html` replaced with a link to the in-app People page.

**Review gate (walk-through, pending user).**
1. Open the project → **Facilities** → sub-nav shows **Dashboard | Assets | …placeholders**. Click **Assets**.
2. Browse the card grid. Open the `?` help modal next to the "Asset Register" heading and walk through the five sections (what is an asset, three ways to add, linked vs orphan, CSV format, caveats).
3. Filter by classification, storey (spatial), and IFC type. Confirm counts update.
4. Multi-select a few cards → bulk classify → confirm the reference is attached.
5. **Add assets** → promote drawer → pick two IFC entities → **Promote selected** → return to the grid with the new cards.
6. **Add manual asset** → create an orphan with name only (no type, no location) → confirm the "Not in IFC model" badge on the new card.
7. **Import CSV** → drop a tiny CSV (one linked row with `global_id`, one orphan row with just `name`) → preview → commit → confirm both flavours land.
8. Click into an asset → hero card renders → "View in IFC" link present for linked, absent for orphan.
9. Type *"which assets have an expired warranty?"* in the Ask chat → confirm the FM asset-context block is surfaced.

**Known gaps (non-blocking, tracked in Open debt).**
- **IFC pset ↔ DB field overlap.** `FacilityAsset.manufacturer` / `model_number` / `serial_number` / `warranty_end` / `commissioning_date` overlap with `Pset_ManufacturerOccurrence.*`, `Pset_Warranty.*`, and the equivalent COBie psets. The promotion flow does NOT seed DB columns from psets today — dual sources of truth. Conflicts with the `feedback_no_duplication` principle; needs a follow-up pass. See Open debt #6.
- Manual orphan→linked reconciliation UI (when an orphan later shows up in the IFC model) is not built. The data model supports it; the UI waits.

**Files touched (M1 work).**
- new: `src/facilities/models/assets.py` (earlier commit); extended 2026-04-21 with orphan fields, conditional unique + check constraints, `display_*` + `is_orphan` properties, `clean()`
- new: `src/facilities/services/asset_service.py` (earlier commit); extended with `linkage` filter, `create_orphan`, mixed linked+orphan CSV
- new: `src/facilities/views.py` asset views (earlier commit); added `AssetCreateManualView`, implicit-owner fallback in `_role_context`
- new: `src/facilities/admin.py` asset registrations; fieldsets updated for orphans
- new: `src/facilities/urls.py` asset routes; added `assets_create_manual`
- new: `src/facilities/templates/facilities/tabs/_facilities_assets.html`, `_facilities_asset_detail.html`
- new: `src/facilities/templates/facilities/components/asset_card.html`, `asset_detail.html`, `asset_grid.html`, `asset_bulk_bar.html`, `asset_filter_bar.html`, `asset_promote_drawer.html`, `asset_import_modal.html`, `asset_import_result.html`, `asset_create_manual_drawer.html`, `asset_register_help_modal.html`, `facilities_subnav.html`
- modified: `src/chat/services/rag_service.py` (FM intent routing + asset context block)
- modified: `src/facilities/templates/facilities/tabs/_facilities.html` (sub-nav include, implicit-owner render gate)
- modified: `src/facilities/templates/facilities/components/dashboards/_default.html` (People page link)
- modified: `src/facilities/templates/facilities/components/dashboards/_header.html` ("Project Owner" greeting)
- modified: `src/facilities/templates/facilities/components/role_switcher.html` (implicit-owner pill)
- migrations: `facilities/0001_initial`, `0002_alter_facilityasset_options_and_more`, `0003_remove_facilityasset_..._and_more`
- tests: `facilities/tests/test_asset_model.py`, `test_asset_service.py`, `test_asset_views.py`, `test_asset_csv_import.py`, `test_fm_intent_routing.py`, `test_facilities_view.py`, `factories.py`

---

## M2 — Export Reconciliation v1 — ✅ Done

**Goal.** FM writes stay in the DB, IFC is reconciled on explicit Export. First version handles asset edits (property/classification/pset) only — sensor refs + WO summaries deferred.

**Delivered.**
- `facilities/models/exports.py`: `FMDelta`, `ExportJob`, `ExportProfile`. Migration `facilities.0004_exports_m2` (+ follow-up `0005` softening `ExportJob.ifc_file` to `SET_NULL` for audit survival). `FacilityAsset.name` widened to optional override on linked assets — orphan invariant preserved.
- `facilities/services/export_reconciliation_service.py` — peer of `ModificationService`, no LLM coupling, pure ifcopenshell + Git. `plan(profile=None)` collapses last-write-wins per `natural_key`; `preview()` renders a template-friendly snapshot; `export(ifc_file, user, profile, emitter)` runs the full pipeline (plan → snapshot → apply → validate → commit) emitting phase events and stamping each applied delta with the resulting commit hash.
- `facilities/services/ifc_mappers/` — `asset_field_map`, `classification_mapper`, `dispatcher` — one mapper per FM-certified op (`SET_PROPERTY`, `ADD_PSET`, `SET_CLASSIFICATION`, `SET_ATTRIBUTE`). Reuses the writeback Tier1/Tier2 writers (extracted to `ifc_processor` in commit `dd22dd8`).
- Views: `ExportPreviewView`, `ExportApplyView` + WebSocket `ExportConsumer` on `ws/projects/<id>/fm/export/` streaming `plan → snapshot → apply → validate → save → commit` with cancel support.
- UI: amber dirty banner in the project shell (OOB-refreshable via `_fm_dirty_banner.html`), Export IFC modal with expandable per-entity subtable showing operation / target / old → new / when / who.
- **Open debt #6 resolved bidirectionally.** `bulk_promote` reads `Pset_ManufacturerOccurrence` / `Pset_Warranty` / `Pset_AssetCommon` from `IFCEntity.properties` and seeds DB columns; `update_asset` emits `FMDelta`s that export replays back into those psets. DB is authoritative post-promotion.
- Shared `diff_data` shape with writeback so the History tab renders FM exports with no template fork; `tier=2` flags them ORANGE.
- Tests: `test_fm_delta_model.py`, `test_delta_emission.py`, `test_export_plan.py`, `test_export_apply.py`, `test_promote_seeding.py`. Green alongside the M1 + M0 suites.

**Review gate (walk-through, pending user).**
1. Open the project → **Facilities** → **Assets**. Edit a few asset fields covered by the certified ops (manufacturer, warranty end, serial number, classification). Confirm the amber `⚠ N FM updates not in IFC — last export X days ago` banner appears in the project header.
2. Click **Export IFC** → preview modal renders with the per-entity subtable (operation, target, old → new, who, when) and a profile selector. Confirm the conflict count = 0 in the simple case; bump it by editing the same field twice in a row and confirm the older delta is shown as superseded.
3. Hit **Export** → WebSocket streams the plan → snapshot → apply → validate → save → commit phases live. Single Git commit lands with the deterministic message.
4. Verify in the History tab that the export shows up as a tier=2 ORANGE entry with the same diff shape as a writeback proposal.
5. Re-ingest the IFC file Castor just exported — confirm no duplicate FM data is created (idempotent on re-ingest, per spec §7.4).

**Files touched (M2 work).**
- new: `src/facilities/models/exports.py` (`FMDelta`, `ExportJob`, `ExportProfile`)
- new: `src/facilities/services/export_reconciliation_service.py`
- new: `src/facilities/services/ifc_mappers/{__init__.py,asset_field_map.py,classification_mapper.py,dispatcher.py}`
- new: `src/facilities/consumers.py` (`ExportConsumer`); `src/facilities/routing.py`
- new: tests `test_fm_delta_model.py`, `test_delta_emission.py`, `test_export_plan.py`, `test_export_apply.py`, `test_promote_seeding.py`
- migrations: `facilities.0004_exports_m2`, `facilities.0005_alter_exportjob_ifc_file`
- modified: `src/facilities/services/asset_service.py` (delta emission on update; pset seeding on promote), `src/facilities/views.py` (export views), `src/facilities/admin.py`, `src/facilities/templates/facilities/components/export_ifc_modal.html`, `export_ifc_preview.html`
- writeback prep: tier writers extracted to `src/ifc_processor/` (commit `dd22dd8`); `ModificationService` got a `skip_commit` hook so Export drives the Git commit itself.

---

## M3 — Work Orders — ✅ Done

**Goal.** CMMS core. From "noticed something wrong" to "signed off" without leaving Castor.

**Delivered (5 sub-phases, ~415 tests passing).**
- **3.A — Models + migration + admin** (`facilities/models/work.py`, migration `0006_work_orders`): `WorkOrder` (10-stage `WorkOrderStatus` IntegerChoices, auto-numbered `WO-NNNNN` per project, dual-anchor FKs to `FacilityAsset` + `IFCSpatialElement`, `is_overdue` property), `WorkOrderStatusEvent` (append-only, `save()` rejects updates with `ImmutableError`), `WorkOrderAttachment`, `Permit` (project-scoped, M2M to WO, `is_expiring_soon` 90-day window), `ActionRequest`. 38 model tests.
- **3.B — `WorkOrderService` + state machine** (`facilities/services/workorder_service.py` + sibling `permit_service.py` + `action_request_service.py`): single `_TRANSITIONS` dict keyed by `(from_status, name)`, role gates with sentinel tokens (`requester` / `assignee`), `_apply_transition` writes a status event + calls `_broadcast`. Skip-triage path for engineer-self-reports. Full bulk methods. AR-to-WO escalation lives on `WorkOrderService` for cross-domain transactionality. **No FMDelta emission** per spec §7.3. 66 service tests (parametrized transition matrix + role-gate matrix + bulk atomicity + escalation).
- **3.C — Views + templates + URLs + sub-nav unlock**: 23 new routes (list / kanban / calendar / detail / create / update / single transition dispatcher / bulk / attachments / permits CRUD + link / action-requests CRUD + triage + dismiss + escalate). 25 new templates: `_facilities_work*.html`, kanban with vanilla HTML5 drag/drop, server-rendered HTMX month calendar, `_work_detail_body.html` with status timeline + transition buttons + attachments + permit linking, `permit_*` + `action_request_*` components. Sub-nav unlocked Work / Permits / Requests entries. 27 view tests.
- **3.D — `WorkOrderConsumer` + per-project channel group** (`fm-wo-{project_id}`): push-only WS, transition broadcasts via `channel_layer.group_send` with **server pre-rendered** kanban-card HTML in the payload (one server render per transition, not per-client). `WorkOrderService._broadcast` now wired to `get_channel_layer()`. Inline JS on kanban template subscribes + reconnects with exponential backoff. 8 consumer tests including the load-bearing two-tab fan-out assertion.
- **3.E — Intent-to-WO (FMIntentService + FMIntentProposal)** (`facilities/services/fm_intent_service.py` + `fm_intent_prompts.py`, migration `0007_fm_intent_proposal`): two-pass LLM (classifier → planner), `_invoke_with_wallclock` enforces hard caps via `ThreadPoolExecutor` per `feedback_ollama_hard_timeout`. `confirm()` loops `WorkOrderService.create_work_order` + `submit/triage/assign/schedule` per draft so every WO writes a full 5-event status chain (no DB-level bulk write). `FMIntentProposal` is the audit row, double-linked to created WOs via `WorkOrder.source_intent_batch_id`. AI batch button on kanban toolbar. 16 LLM-mocked tests including the bypass guard.

**Review gate (walk-through, pending user).**
1. Open the project → **Facilities** → sub-nav now shows **Dashboard | Assets | Work | Permits | Requests | …placeholders**. Click **Work**.
2. Filter by status / priority / category in the list view; HTMX swap updates the grid.
3. Click **Kanban** → drag a card from a column to the next valid column → confirm transition lands and audit timeline updates.
4. Open the project in a second browser tab also on the kanban; trigger a transition in tab 1 → confirm tab 2 receives the WS push (`fm-wo-{project_id}` group) and the card swaps in place.
5. Click **Calendar** → navigate prev/next month; WO chips appear on their `scheduled_start` day cells.
6. Open a WO's detail page → status timeline lists every event with actor + role snapshot + timestamp; attach a photo; link an existing permit.
7. Submit *"Schedule filter replacement on all rooftop AHUs next Tuesday, assign to ACME HVAC, priority 2"* via the **AI batch** button → review modal → confirm → N WOs land in `Scheduled`, all sharing one `source_intent_batch_id` (visible in admin via `FMIntentProposal`).
8. Log in as OCCUPANT → submit an `ActionRequest` via the report-an-issue drawer; log in as FM → triage → escalate → confirm WO is created with `source_action_request` set and the AR moves to `escalated`.
9. Create a Permit → link it to a WO → revoke it → confirm WO detail shows the revoked status.
10. Run `cd src && uv run pytest facilities/tests/ -v -x` — all green; `uv run ruff check . && uv run ruff format .` — clean.

**Beyond spec / scope decisions.**
- Permits + Action Requests ship with **full CRUD UI** (per user decision) rather than the originally-suggested "models-only + minimal AR drawer" — the patterns will be reused (not replaced) by M4 (Occupant Portal) and M6 (Documents).
- Intent-to-WO uses a **separate `FMIntentService`** mirroring `ModificationService.propose()` shape, NOT a tier-2 extension of writeback (writeback's `Tier2Writer` is hardcoded to IFC mutations and would poison the FM contract). Audit trail persists on every outcome — applied / rejected / failed / pending.
- Kanban uses **vanilla HTML5 drag/drop** (no SortableJS) and the calendar is **server-rendered HTMX** (no FullCalendar) — both per CLAUDE.md "no JS frameworks".
- View module split (originally planned as `views/asset.py` etc.) **deferred** — added the new view classes to `views.py` directly per CLAUDE.md "don't refactor beyond what the task requires." Split can land as its own follow-up if `views.py` exceeds ~1500 LOC after M4–M6.

**Files touched (M3 work).**
- new: `src/facilities/models/work.py`
- new: `src/facilities/services/{workorder_service,permit_service,action_request_service,fm_intent_service,fm_intent_prompts}.py`
- new: 25 templates under `src/facilities/templates/facilities/{tabs,components}/`
- new: 7 test files under `src/facilities/tests/` (`test_workorder_model`, `test_workorder_service`, `test_permit_service`, `test_action_request_service`, `test_workorder_views`, `test_workorder_consumer`, `test_intent_to_wo`)
- migrations: `facilities.0006_work_orders`, `facilities.0007_fm_intent_proposal`
- modified: `src/facilities/{models/__init__.py,services/__init__.py,admin.py,urls.py,views.py,consumers.py,routing.py,tests/factories.py,templates/facilities/components/facilities_subnav.html}`

**Effort.** L (originally 1–2 weeks; landed in one focused build session).

---

## M4 — Occupant Portal — ✅ Done

**Goal.** Mobile-first intake page for TENANT / OCCUPANT roles. No forms — one text box. Tenant types a complaint, an LLM extracts a structured ActionRequest, the tenant confirms, FM triages, status pushes back via WebSocket.

**Delivered (6 sub-phases, ~40 new tests).**
- **4.A — Foundations + role-aware nav.** `ProjectRole.assigned_space` FK to `ifc_processor.IFCSpatialElement` (migration `environments.0005_projectrole_assigned_space`). New `OccupantSpaceService` (TENANT outranks OCCUPANT, deterministic resolution under multiple concurrent rows). `is_occupant_mode` context flag computed in `ProjectTabMixin._compute_occupant_mode` AND `_role_context()` so every project view can gate the nav. Tab strip in `project_detail.html` and Facilities sub-nav both gate to a single "Portal" entry for TENANT/OCCUPANT users without EDITOR+ access. Project sidebar + sidebar-toggle hidden in occupant mode. `OccupantPortalAccessMixin` (write IF EDITOR+ OR active TENANT/OCCUPANT) — scoped to portal endpoints only. `_tenant.html` rebuilt from placeholder cards into a real landing (assigned-space card + intake CTA + recent-requests card). 22 new tests (occupant_space_service: 9, TestOccupantMode in test_facilities_view: 9, plus regression assertions on tab-strip gating + portal context).
- **4.B — Intake: LLM service + draft → confirm → AR create.** Extracted `_invoke_with_wallclock` from `fm_intent_service.py` into shared `facilities/services/_llm_helpers.py::invoke_with_wallclock` (no behaviour change; FM intent service updated import). New `facilities/services/spatial_lookup.py::resolve_space_candidates` — token-overlap search against `IFCEntity.name` + `IFCSpatialElement.long_name`, scoped to `spatial_type='space'`, with assigned-space prior winning ties. Plain `icontains`, no `pg_trgm` extension (project space-count too small to justify). New `OccupantIntakeService.draft / .submit` — single-pass `format_json=True` LLM call, 20s wall-clock cap, 512 max tokens. Server-side clamping is non-negotiable: severity outside enum → `medium`; hallucinated spatial_id → validated against project then dropped to assigned-space; title truncated to 255; description capped to 4000. LLM failure → fallback draft (`fallback_used=True`) so submission still succeeds — Ollama down doesn't block tenants. Migration `facilities.0008_action_request_intake_fields` adds `user_text` + `draft_payload` (JSONField) + `draft_confidence` (PositiveSmallIntegerField) directly on `ActionRequest` — the AR is the audit row, no separate `OccupantIntakeProposal` model. Two new portal views (`OccupantPortalIntakeView` for drawer GET + draft POST, `OccupantPortalConfirmView` for the persist step), draft-token cached in session keyed by uuid4 so confirmations don't double-submit on back-button. `submit()` independently asserts `submitted_by == self.user` — service-layer guard against view-bug regressions. 34 new tests (spatial_lookup: 10, occupant_intake_service: 13, occupant_portal_views: 11). Two new templates: `_portal_intake_drawer.html` + `_portal_intake_review.html`.
- **4.C — WebSocket push: occupant + FM AR-row push.** New `OccupantPortalConsumer` on `ws/projects/<id>/fm/portal/`, joins per-user channel group `fm-portal-{user_id}-{project_id}` — narrow blast radius, only the AR submitter receives status updates. `WorkOrderConsumer` got a sibling `ar_updated` handler so the existing `fm-wo-{project_id}` group fans the AR-row push to FM staff (no third channel group). `ActionRequestService` gained `_broadcast_to_occupant` + `_broadcast_to_fm` hooks called from `create_action_request`, `triage`, `dismiss`. `WorkOrderService.escalate_action_request` calls back into `ActionRequestService._broadcast(ar, "escalated")` so the occupant sees their AR move to ESCALATED in real time. Two new server-rendered HTML fragments — `_portal_status_card.html` (occupant side) and `_ar_row_card.html` (FM side) — pre-rendered once per transition then group_send'd. Inline JS in `_tenant.html` subscribes + reconnects with exponential backoff. 6 new consumer tests including the load-bearing two-tenant group-isolation assertion (uses `receive_nothing` for the negative path).
- **4.D — Mobile CSS polish.** New `.portal-scope` CSS in `core/base.html` — 44px tap targets on every form control, single-column collapse under 600px, larger typography ramp for thumb reach. `_tenant.html` wrapped in `.portal-scope facilities-scope`. New `portal_help_modal.html` — canonical 5-section shape (What is it / Lifecycle / How to file / Who can do what / Caveats); the heading gets the standard `?` help pill per the help-modals convention.
- **4.E — Tests: E2E review-gate sentence.** `test_portal_e2e.py::test_review_gate_sentence_round_trip` exercises the spec's canonical sentence end-to-end with a mocked LLM: TENANT submits *"Meeting room 3-B is cold"* → AR exists with `affected_spatial.entity.name == "Meeting Room 3-B"`, `severity="medium"`, `submitted_by=tenant`, `user_text` and `draft_confidence` stamped → FM escalates with `category="corrective"` → WO carries `source_action_request`, AR moved to ESCALATED, both the tenant's portal landing and the FM Requests page now list the AR.
- **4.F — FM-side Requests mobile collapse.** CSS-only addition in `_facilities_action_requests.html` — `@media (max-width: 600px)` collapses the desktop card grid + filter bar into a single column, closing the §4 day-in-the-life gap (FM scans the queue on her commute).

**Beyond spec / scope decisions.**
- **Photos deferred to phase 2** (per user decision before build). Textarea gets OS-native dictation on iOS/Android for free; `ActionRequestAttachment` model + `<input type="file" capture>` are open-debt #10.
- **No `OccupantIntakeProposal` audit row** — the AR carries everything (`user_text`, `draft_payload`, `draft_confidence`). Portal is 1:1 (one message → one AR), unlike `FMIntentProposal` which fans 1:N. Adding it later is reversible.
- **TENANT/OCCUPANT keep ProjectMembership=VIEWER** — no new permission tier. Onboarding friction is real (two rows per occupant) but tracked as open-debt #8 rather than complicating the access tiers.
- **Two channel groups, not three** — reuse `fm-wo-{project_id}` for FM-side AR pushes (per plan §7), avoiding a third FM consumer.
- **Server-side LLM-output clamping is non-negotiable** — every field returned by the intake LLM is treated as untrusted: severity coerced, spatial_id project-validated, title and description length-capped. Hardens against prompt injection from the textarea.

**Review gate (walk-through, pending user).**
1. In Django admin → **Project Memberships** → add a fresh user as `VIEWER` on a project that has at least one IFC file. In **Project Roles** add a `TENANT` row for that user with `assigned_space` pointing to a SPACE entity — for the demo, name a space "Meeting Room 3-B".
2. Log in as that user. Project header should render only the **Portal** tab (no Ask / Modify / Conflicts / History / Explore / Schedule). The project sidebar is hidden too.
3. Confirm portal landing renders: "Your space" card shows the assigned space, "Submit a new request" card has the teal CTA, "Your recent requests" card shows the empty state.
4. Open the `?` help pill — confirm the five-section help modal renders (What / Lifecycle / How to file / Who / Caveats).
5. Click **New request** → drawer opens with a textarea. Type *"Meeting room 3-B is cold"* → click **Draft request**.
6. Review fragment renders: title, description, severity (medium), location (Meeting Room 3-B preselected). Confirm.
7. Toast fires. Page reloads. AR appears in "Recent requests" with status OPEN.
8. Open a second browser as an FM (EDITOR+) user. Navigate to **Facilities → Requests**. The new AR is there. Click triage → add a note → submit. Tab 1 (tenant) should WS-update the status card to TRIAGED in place.
9. FM clicks **Escalate** → choose category corrective → confirm. Tenant's portal status card updates to ESCALATED. FM Requests page shows the AR moved to ESCALATED, and a new WO appears in the Work list with the AR's title + spatial anchor.
10. Phone test: open the portal URL on a real phone in portrait. Tap targets ≥ 44px, single column, no horizontal scroll. Tap into the textarea — confirm the OS dictation chip works.
11. Open `/facilities/<pk>/facilities/action-requests/` on the same phone as the FM user. Confirm the 4.F single-column collapse renders cleanly.
12. `cd src && uv run pytest facilities/tests/ -v -x` — all 434 tests green.
13. `uv run ruff check . && uv run ruff format .` — clean (M4 files; pre-existing 11 N806 warnings in `environments/tests/test_environments_views.py` are out of scope).

**Files touched (M4 work).**
- new: `src/facilities/services/{occupant_space_service,occupant_intake_service,occupant_intake_prompts,spatial_lookup,_llm_helpers}.py`
- new: `src/facilities/templates/facilities/portal/{_portal_intake_drawer,_portal_intake_review,_portal_status_card}.html`
- new: `src/facilities/templates/facilities/components/{portal_help_modal,_ar_row_card}.html`
- new: 6 test files under `src/facilities/tests/` (`test_occupant_space_service`, `test_spatial_lookup`, `test_occupant_intake_service`, `test_occupant_portal_views`, `test_portal_consumer`, `test_portal_e2e`)
- migrations: `environments.0005_projectrole_assigned_space`, `facilities.0008_action_request_intake_fields`
- modified: `src/environments/models.py` (`ProjectRole.assigned_space`), `src/environments/admin.py` (raw_id_fields + list_display), `src/facilities/models/work.py` (`ActionRequest.user_text` / `draft_payload` / `draft_confidence`), `src/facilities/services/__init__.py` (exports), `src/facilities/services/action_request_service.py` (broadcast hooks), `src/facilities/services/workorder_service.py` (escalation broadcast), `src/facilities/services/fm_intent_service.py` (helper extraction), `src/facilities/views.py` (`_role_context` + `_portal_landing_context` + `OccupantPortalAccessMixin` + portal views), `src/facilities/urls.py` (portal routes), `src/facilities/consumers.py` (`OccupantPortalConsumer` + `fm_portal_group_name` + `WorkOrderConsumer.ar_updated`), `src/facilities/routing.py` (portal WS route), `src/facilities/templates/facilities/components/dashboards/_tenant.html` (real cards + WS subscription + help pill + .portal-scope wrapper), `src/facilities/templates/facilities/components/facilities_subnav.html` (occupant-mode gate), `src/facilities/templates/facilities/tabs/_facilities_action_requests.html` (4.F mobile @media collapse), `src/environments/templates/environments/project_detail.html` (tab-strip + sidebar gates), `src/core/mixins.py` (`ProjectTabMixin._compute_occupant_mode`), `src/core/templates/core/base.html` (`.portal-scope` CSS), `src/facilities/tests/test_facilities_view.py` (`TestOccupantMode` class)

**Effort.** M (originally 3–5 days; landed in one focused build session).

---

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

6. **IFC pset ↔ FacilityAsset DB column overlap** (surfaced 2026-04-21, post-M1). `FacilityAsset.manufacturer`, `model_number`, `serial_number`, `warranty_start`, `warranty_end`, `commissioning_date`, `condition_score` overlap with IFC property sets carried on `IFCEntity.properties` — `Pset_ManufacturerOccurrence.{Manufacturer, ModelLabel, SerialNumber, ProductionYear}`, `Pset_Warranty.{WarrantyStartDate, WarrantyEndDate}`, `Pset_ConditionOfProperty.*`, `Pset_ServiceLife.*`, and the COBie.Component pset family. The promotion flow does **not** seed the DB columns from these psets today, so a freshly-promoted asset's fields start blank even when the IFC file carries the data — and manual edits on the DB side don't sync back. Dual sources of truth, violates `feedback_no_duplication`. Three candidate directions for the follow-up pass:
   - **Auto-populate on promotion** — at `bulk_promote` time, read the relevant psets and copy into DB columns. DB remains authoritative thereafter. Lowest risk, mostly additive.
   - **Read-through fallback** — DB columns are overrides only; `asset.manufacturer` is a property that returns the DB value else the pset value. No duplication, but every filter query against JSONField gets harder.
   - **Pset-first with DB-diffs** — IFC pset is authoritative; DB stores deltas only. Cleanest but biggest touch (filters, display, CSV behaviour, export reconciliation).

   Decision deferred until a concrete use case picks the winner. Touches M1 (Asset Register) and M2 (Export Reconciliation).

7. **Orphan → linked reconciliation** (surfaced 2026-04-21, post-M1). When an orphan asset's physical counterpart later appears in the IFC model, we need a manual link action: "this orphan is now represented by entity X, convert it". Data model supports it (nullable FK); UI not built.

8. **One-click "Add occupant" action** (surfaced 2026-04-26, post-M4). Today seating an occupant requires two admin steps: a `ProjectMembership` row at VIEWER tier AND a `ProjectRole=TENANT/OCCUPANT` row with optional `assigned_space`. For a building with 200 occupants that's 400 admin operations and a near-certain miss rate. Add a People-page button that creates both rows in a single transaction, with a SPACE picker for the seating. Should also surface a warning chip when the unusual combination "TENANT/OCCUPANT role + EDITOR+ access" appears, since that's almost always a misconfiguration.

9. **`OccupantIntakeProposal` audit row** (deferred from M4 by design). Portal is 1:1 (one occupant message → one AR), so the AR carries the full audit footprint (`user_text`, `draft_payload`, `draft_confidence`). If abuse / debug telemetry across rejected drafts is later required (e.g., to spot prompt-injection attempts that never made it to AR creation), add a dedicated proposal row mirroring `FMIntentProposal`.

10. **`ActionRequestAttachment` model + photo capture** (deferred from M4 per user decision). Spec §6.4 wow: photo + voice intake. Phase 2 add: clone the `WorkOrderAttachment` shape; `<input type="file" capture="environment">` next to the textarea; persist photos before AR submission so the FM can review evidence at triage time.

11. **Vision-model intake** (spec §6.4 wow / §11 #4). Phase 3. Photo → vision LLM extracts "water stain on ceiling" → enriches the AR draft alongside the text. Builds on #10.

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
- **2026-04-21** — **M1 (Asset Register) shipped.** Models, services, views, templates, admin, FM intent routing in Ask, 155 tests passing. Two scope extensions beyond the original spec: **(a) orphan assets** — `FacilityAsset.ifc_entity` now nullable, with required `name`, optional free-text `ifc_type`, optional `spatial_container` + `location_text`; manual-create drawer, "Add manual asset" button, relaxed CSV schema, "Not in IFC model" badges, help modal next to the Asset Register heading. Migrations `0002` + `0003`. **(b) implicit-owner fallback** — project owners with no explicit `ProjectRole` now land on the FM dashboard (implicit view) instead of the "ask a role" empty state; stale Django-admin reference replaced with a link to the in-app People page. Added open-debt items #6 (IFC pset ↔ DB column overlap, dual sources of truth) and #7 (orphan → linked reconciliation UI). Review walk-through still pending.
- **2026-04-23** — **M2 (Export Reconciliation v1) shipped** (commit `212e147`). FMDelta queue + ExportReconciliationService (peer of ModificationService) + ifc_mappers + ExportConsumer (WS phase streaming) + dirty banner + Export IFC modal. Migrations `0004_exports_m2` + `0005_alter_exportjob_ifc_file`. Open debt #6 resolved bidirectionally (`bulk_promote` seeds DB columns from psets; `update_asset` emits FMDeltas back to psets — DB authoritative post-promotion). Tier1/Tier2 writers extracted to `ifc_processor` (commit `dd22dd8`). Review walk-through pending.
- **2026-04-26** — **M4 (Occupant Portal) shipped — all six sub-phases.** 4.A foundations: `ProjectRole.assigned_space` (migration `environments.0005`), `OccupantSpaceService` (TENANT-over-OCCUPANT priority), `is_occupant_mode` flag computed in `ProjectTabMixin._compute_occupant_mode` AND `_role_context()`, `OccupantPortalAccessMixin` (write IF EDITOR+ OR active TENANT/OCCUPANT — portal-only), tab-strip + sub-nav + sidebar gated for occupants, `_tenant.html` rebuilt with real cards. 4.B intake: extracted `_invoke_with_wallclock` to shared `_llm_helpers.invoke_with_wallclock`; new `spatial_lookup.resolve_space_candidates` (icontains + token-overlap, no `pg_trgm`); new `OccupantIntakeService.draft / .submit` (single-pass `format_json=True`, 20s wall-clock cap, 512 max tokens, server-side clamping non-negotiable); migration `facilities.0008` adds `ActionRequest.user_text` + `draft_payload` + `draft_confidence`; two new portal views with session-cached draft tokens; `submit()` independently asserts `submitted_by == self.user`. 4.C WebSocket push: new `OccupantPortalConsumer` on `ws/projects/<id>/fm/portal/` with per-user channel group `fm-portal-{user_id}-{project_id}` (narrow blast radius); reused `fm-wo-{project_id}` for FM-side AR pushes via new `WorkOrderConsumer.ar_updated`; `ActionRequestService` gained `_broadcast_to_occupant` + `_broadcast_to_fm` hooks fired from create / triage / dismiss; `WorkOrderService.escalate_action_request` calls back into the AR broadcast. 4.D mobile CSS polish: `.portal-scope` (44px tap targets, single-column ≤600px), `portal_help_modal.html` (canonical 5-section pattern), `?` help pill on the portal landing. 4.E E2E test exercising the canonical *"Meeting room 3-B is cold"* sentence end-to-end with mocked LLM. 4.F FM-side Requests page got a `@media (max-width: 600px)` single-column collapse. **Two scope decisions beyond plan.** (a) Photos deferred to phase 2 (open-debt #10); textarea gets OS-native dictation on iOS/Android for free. (b) No `OccupantIntakeProposal` audit row — AR is 1:1 with the message, so it carries everything (open-debt #9 if ever needed). Total facilities tests: 434 (~+19 net new from M3 baseline). Migrations: `environments.0005_projectrole_assigned_space`, `facilities.0008_action_request_intake_fields`. New open-debt items #8 (one-click "Add occupant"), #9 (`OccupantIntakeProposal`), #10 (`ActionRequestAttachment` + photo capture), #11 (vision-model intake). Review walk-through pending.
- **2026-04-23** — **M3 (Work Orders) shipped — all five sub-phases.** 3.A models + migration + admin (`WorkOrder` 10-stage status / `WorkOrderStatusEvent` append-only / `WorkOrderAttachment` / `Permit` / `ActionRequest`; migration `0006_work_orders`; 38 model tests). 3.B `WorkOrderService` + state-machine table + sibling `permit_service` / `action_request_service`; no FMDelta on transitions per spec §7.3; 66 service tests covering parametrized transition matrix + role-gate matrix + bulk atomicity + AR-to-WO escalation. 3.C 23 new URL routes, 25 new templates, vanilla HTML5 drag/drop kanban + HTMX month calendar, sub-nav unlocked Work / Permits / Requests; 27 view tests. 3.D `WorkOrderConsumer` with per-project channel group `fm-wo-{project_id}`, server pre-rendered HTML in WS payload (one render per transition), inline JS subscribes + reconnects; 8 consumer tests including two-tab fan-out assertion. 3.E `FMIntentService` + `FMIntentProposal` (migration `0007_fm_intent_proposal`), two-pass LLM (classifier + planner) with hard wall-clock caps via `ThreadPoolExecutor` per `feedback_ollama_hard_timeout`, `confirm()` walks every WO through the full `submit/triage/assign/schedule` chain (no DB-level bulk write — every WO writes its 5-event status chain), AI batch button on kanban; 16 LLM-mocked tests including the bypass guard. **Two scope decisions beyond the spec.** (a) Permits + ARs ship with **full CRUD UI** per user decision (not the minimal models-only + AR drawer originally sketched); the patterns will be reused — not replaced — by M4 and M6. (b) Intent-to-WO is a separate `FMIntentService` mirroring `ModificationService.propose()` *shape* only, NOT a tier-2 extension; writeback's `Tier2Writer` is hardcoded to IFC writes and would poison the FM-only contract. Total facilities tests: ~415 (M0+M1+M2+M3) — full suite green. Review walk-through pending.
