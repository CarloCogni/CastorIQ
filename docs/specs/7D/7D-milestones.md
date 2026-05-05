# 7D Facility Management — Milestone Tracker

Companion to [`7D-facility-management.md`](./7D-facility-management.md) (the vision spec) and [`facility_matrix.html`](./facility_matrix.html) (the canonical operational scope). This file is the **execution ledger** — what's shipped, what's in flight, what's next, and the review gate for each block.

**Last updated:** 2026-05-04 — restructured around the expert matrix's daily / weekly / monthly priority lens.

---

## Architecture recap (locked)

- **Single `facilities/` Django app**, internally partitioned by subpackage (`models/assets.py`, `models/work.py`, `services/`, `services/ifc_mappers/`, …). Subpackages can be extracted later if shapes stabilize.
- **Role model (`ProjectRole`) lives in `environments/`** — companion to `ProjectMembership`. Multi-role per user per project, validity windows. 🔍 *9-role enum pending review against the matrix's ~5 personas — see appendix.*
- **Export Reconciliation Service** sits on top of existing writeback — does NOT replace it. FM ops queue as `FMDelta` rows, Export replays them against the existing tier executors, one bundled Git commit per export. **Castor scaffolding, kept.**
- **Facilities color:** teal `#14b8a6` — distinct from Ask blue (`#3b82f6`) and Modify purple (`#8b5cf6`).
- **AI features chosen for V1:** #3 Manual-to-PM-plan (M5), #6 Narrative KPIs (M7), #9 Replace-vs-maintain 🔍 *(matrix only implies a cost cell on the owner report; full LCC advisor pending review)*.

---

## Milestone status — matrix-aligned ordering

Ordering follows the matrix's Layer 1 → Layer 2 → Layer 3 priority lens: deliver the daily-critical path first, then weekly operations, then monthly management. Effort legend: S = 1–2 days, M = 3–5 days, L = 1–2 weeks.

| # | Milestone | Layer | Status | Reviewed |
|---|-----------|-------|--------|----------|
| M0 | Scaffold + Facilities tab + ProjectRole + role-aware landing | — | ✅ Done | ⏳ Pending walk-through |
| M1 | Asset Register | foundation | ✅ Done | ⏳ Pending walk-through |
| M2 | Export Reconciliation v1 (Castor scaffolding) | — | ✅ Done | ⏳ Pending walk-through |
| M3 | Work Orders (with Permits + Action Requests + Intent-to-WO) | L1 + L2 | ✅ Done | ⏳ Pending walk-through |
| **M3.F** | **WO cost capture (G4)** | **L2** | 🆕 Not started | — |
| M4 | Occupant Portal | L1 | ✅ Done | ⏳ Pending walk-through |
| **M4.5** | **Access & Keys (G2)** | **L1** | 🆕 Not started | — |
| **M4.6** | **Quick Contacts (G3)** | **L1** | 🆕 Not started | — |
| **M4.7** | **Notification escalation / SLA timers (R4)** | **L1** | 🆕 Not started | — |
| **M4.8** | **Photo geotag + mobile signature UI (R2 + R3)** | **L2** | 🆕 Not started | — |
| M5 | PM Planner + Manual-to-PM (AI #3) | L2 | ⏳ Not started | — |
| **M5.5** | **Spare Parts & Procurement (G1)** | **L2** | 🆕 Not started | — |
| M6 | Documents v2 (incl. H&S certificates dashboard) | L2 + L3 | ⏳ Not started | — |
| **M6.5** | **Utility Meters & YoY (G5)** | **L3** | 🆕 Not started | — |
| M7 | Owner Report — Narrative KPIs (AI #6) | L3 | ⏳ Not started | — |
| ~~M8~~ | ~~Replace-vs-maintain (AI #9)~~ 🔍 | — | Pending review | — |

---

## M0 — Scaffold + Facilities tab + role model — ✅ Done

**Goal.** Prove the skeleton everything else hangs off: new app, tab in the navbar, role architecture, role-aware landing.

**Delivered.**
- `facilities/` Django app with subpackage layout: `models/`, `services/`, `ifc_mappers/`, `templates/`, `tests/`, `urls.py`, `views.py`, `apps.py`, `admin.py`.
- `environments.ProjectRole` model: `(user, project, role, valid_from, valid_until)`, 9 role choices. Multi-role per user per project. Helper classmethod `active_for(user, project, moment=None)`. 🔍 *9 → ~5 trim pending — see appendix.*
- Migration `environments.0003_projectrole` applied.
- Admin: `ProjectRoleAdmin` (list + filters + date hierarchy), `ProjectRoleInline` on `ProjectAdmin`.
- `facilities.services.role_service.ProjectRoleService` — `active_roles()`, `resolve_active(session)`, `set_active(session, role_id)`. Session key: `facilities_active_role_id`.
- `facilities.views.FacilitiesView` (extends `ProjectTabMixin`) — renders role-aware landing via `ROLE_DASHBOARD_TEMPLATES` map.
- `facilities.views.RoleSwitchView` — HTMX `POST` endpoint that swaps the tab body.
- Templates: `tabs/_facilities.html`, `components/role_switcher.html`, dashboards (`_owner.html`, `_fm.html`, `_engineer.html`, `_contractor.html`, `_tenant.html`, `_auditor.html`, `_default.html`, `_header.html`, `_placeholder_card.html`).
- Facilities tab in `project_detail.html` navbar with `.tab-facilities` teal active state (CSS var `--castor-facilities` added to `core/base.html`).
- URL routes: `facilities:tab`, `facilities:role_switch`.
- 30 tests passing.

**Review gate (walk-through, pending user).**
1. Django admin → Project → **Project Memberships** inline → add the target user with permission **Editor** (or **Viewer**).
2. **Project Roles** inline → add **two rows** (e.g., FACILITIESMANAGER + AUDITOR). Save.
3. Log in as that user → open project → click **Facilities** tab.
4. Confirm: teal tab is active; role-switcher pill shows both roles; switching swaps the dashboard without a full page reload.

---

## M1 — Asset Register — ✅ Done

**Goal.** The single pane of glass for every physical asset with a serial number or service obligation. Cards, not rows. Foundation for every Layer 1 / 2 / 3 feature.

**Delivered.**
- `facilities/models/assets.py`: `FacilityAsset`, `AssetInventory` 🔍, `Classification`, `ClassificationReference`. `FacilityAsset.ifc_entity` is **nullable** (orphan support). `(project, ifc_entity)` uniqueness conditional on the link existing.
- `facilities/services/asset_service.py`: `list_assets`, `get_asset`, `create_asset` + `bulk_promote`, `update_asset`, `delete_asset`, `bulk_classify`, `bulk_set_responsible_party`, `import_csv`, `create_orphan`, `list_promotion_candidates`.
- Views + URLs: `AssetListView`, `AssetDetailView`, `AssetPromoteView`, `AssetCreateManualView`, `AssetUpdateView`, `AssetBulkView`, `AssetCSVImportView`. Mutation endpoints use `ProjectModifyAccessMixin`.
- Templates: card grid, hero detail, promote/manual/import drawers, bulk bar, filter bar, help modal.
- Sub-nav inside Facilities: Dashboard | Assets | (placeholders for the rest, unlocked as milestones land).
- Ask integration: `chat.services.rag_service.RAGService._detect_intent` classifies FM phrasing as `fm_asset_query`.
- Admin: `FacilityAssetAdmin` with linkage-aware fieldsets, `AssetInventoryInline`, classification horizontal filter.
- 155 tests passing.
- Migrations: `facilities.0001_initial` → `0002_alter_facilityasset_options_and_more` → `0003_remove_facilityasset_..._and_more`.

**Beyond spec — orphan asset support (added 2026-04-21).** `FacilityAsset.ifc_entity` nullable; orphans carry `name` (required) + `ifc_type` (free text) + optional `spatial_container` FK + `location_text`. At-most-one IFC link per asset. Manual-create drawer + CSV orphan rows + "Not in IFC model" badge.

**Beyond spec — implicit-owner fallback (added 2026-04-21).** Project owners with no explicit `ProjectRole` land on the FM dashboard with a "Project Owner — implicit view" pill rather than the empty state.

**Review gate (walk-through, pending user).**
1. Open project → **Facilities** → **Assets**. Open the `?` help modal.
2. Filter by classification, storey, IFC type. Confirm counts update.
3. Multi-select cards → bulk classify.
4. **Add assets** → promote drawer → pick IFC entities → **Promote selected**.
5. **Add manual asset** → create an orphan with name only → confirm the "Not in IFC model" badge.
6. **Import CSV** → mixed linked + orphan rows → preview → commit.
7. Click into an asset → hero card → "View in IFC" link present for linked, absent for orphan.
8. Type *"which assets have an expired warranty?"* in the Ask chat → FM context block surfaces.

**Known gaps (non-blocking, tracked in Open debt).**
- IFC pset ↔ DB column overlap (debt #6, resolved bidirectionally in M2).
- Manual orphan→linked reconciliation UI (debt #7).

---

## M2 — Export Reconciliation v1 — ✅ Done (Castor scaffolding)

**Goal.** FM writes stay in the DB; IFC is reconciled on explicit Export. First version handles asset edits (property/classification/pset).

**Not in the expert matrix** — this exists because Castor is a bidirectional BIM-FM tool. Without it, durable FM metadata never reaches IFC. **Recommendation: keep.** See appendix.

**Delivered.**
- `facilities/models/exports.py`: `FMDelta`, `ExportJob`, `ExportProfile`. Migrations `0004_exports_m2` + `0005_alter_exportjob_ifc_file`.
- `facilities/services/export_reconciliation_service.py` — peer of `ModificationService`, no LLM coupling, pure ifcopenshell + Git. `plan` collapses last-write-wins per `natural_key`; `preview` renders a template snapshot; `export` runs plan → snapshot → apply → validate → commit, stamping each delta with the resulting commit hash.
- `facilities/services/ifc_mappers/` — `asset_field_map`, `classification_mapper`, `dispatcher`. One mapper per FM-certified op. Reuses Tier1/Tier2 writers (extracted to `ifc_processor`).
- Views: `ExportPreviewView`, `ExportApplyView` + WebSocket `ExportConsumer` on `ws/projects/<id>/fm/export/` streaming the phases with cancel support.
- UI: amber dirty banner in the project shell (OOB-refreshable), Export IFC modal with expandable per-entity subtable.
- **Open debt #6 resolved bidirectionally.** `bulk_promote` reads psets and seeds DB columns; `update_asset` emits `FMDelta`s that export replays back. DB is authoritative post-promotion.
- Shared `diff_data` shape with writeback so the History tab renders FM exports with no template fork; `tier=2` flags them ORANGE.
- Tests: `test_fm_delta_model.py`, `test_delta_emission.py`, `test_export_plan.py`, `test_export_apply.py`, `test_promote_seeding.py`.

**Review gate (walk-through, pending user).**
1. **Facilities → Assets**. Edit fields covered by certified ops (manufacturer, warranty end, serial, classification). Confirm the amber banner appears.
2. **Export IFC** → preview modal renders with per-entity subtable. Bump conflict count by editing the same field twice; confirm older delta shows superseded.
3. Hit **Export** → WS streams phases live. Single Git commit lands.
4. History tab → export shows up as tier=2 ORANGE entry.
5. Re-ingest the exported IFC → confirm idempotent (no duplicate FM data).

---

## M3 — Work Orders — ✅ Done

**Goal.** CMMS core. From "noticed something wrong" to "signed off" without leaving Castor.

**Matrix coverage:** L1 *fault reporting* (via `ActionRequest`), L1 *technician assignment* (`assignee_user`), L1 *live work status* (`WorkOrderStatus` FSM 🔍), L1 *push notifications* (WebSocket — escalation timer pending in M4.7), L2 *photo documentation* (geotag pending in M4.8), L2 *handover records* (signature UI pending in M4.8), L2 *contracts & deadlines* (via `Permit` 🔍).

**Delivered (5 sub-phases, ~415 tests passing).**
- **3.A — Models + migration + admin** (`facilities/models/work.py`, migration `0006_work_orders`): `WorkOrder` (10-stage `WorkOrderStatus` 🔍, auto-numbered `WO-NNNNN`, dual-anchor FKs to `FacilityAsset` + `IFCSpatialElement`, `is_overdue` property), `WorkOrderStatusEvent` (append-only, `save()` rejects updates), `WorkOrderAttachment`, `Permit` 🔍 (M2M to WO, 90-day expiry window), `ActionRequest`. 38 model tests.
- **3.B — `WorkOrderService` + state machine**: single `_TRANSITIONS` dict, role gates, skip-triage path for engineer self-reports, full bulk methods, AR-to-WO escalation. **No FMDelta on transitions** per spec §7.3. 66 service tests.
- **3.C — Views + templates + URLs + sub-nav unlock**: 23 routes, 25 templates, vanilla HTML5 drag/drop kanban, server-rendered HTMX month calendar, status timeline, attachment + permit linking. 27 view tests.
- **3.D — `WorkOrderConsumer` + per-project channel group `fm-wo-{project_id}`**: server pre-rendered HTML in WS payload (one render per transition). 8 consumer tests including two-tab fan-out.
- **3.E — Intent-to-WO (`FMIntentService` + `FMIntentProposal`, migration `0007`)**: two-pass LLM (classifier → planner), hard wall-clock caps via `ThreadPoolExecutor`, `confirm()` walks every WO through full submit/triage/assign/schedule chain. 16 LLM-mocked tests.

**Review gate (walk-through, pending user).**
1. Open **Facilities** → sub-nav now has **Dashboard | Assets | Work | Permits | Requests | …**.
2. Filter by status / priority / category in the list view.
3. **Kanban** → drag a card across columns → confirm transition + audit timeline.
4. Open project in second tab on the same kanban; trigger transition in tab 1 → confirm WS push to tab 2.
5. **Calendar** → navigate prev/next month; WO chips on `scheduled_start` cells.
6. WO detail page → status timeline + attach photo + link permit.
7. **AI batch** button → *"Schedule filter replacement on all rooftop AHUs next Tuesday, assign to ACME HVAC, priority 2"* → review → confirm → N WOs land in Scheduled with shared `source_intent_batch_id`.
8. Log in as OCCUPANT → submit AR → log in as FM → triage → escalate → WO created.
9. Create a Permit → link to WO → revoke.
10. `cd src && uv run pytest facilities/tests/ -v -x` and `uv run ruff check . && uv run ruff format .`.

**Beyond spec / scope decisions.**
- Permits + Action Requests ship with full CRUD UI (per user decision). 🔍 *Permit retention pending review — see appendix.*
- Intent-to-WO is a separate `FMIntentService`, NOT a tier-2 extension of writeback.
- Kanban uses vanilla HTML5 DnD; calendar is server-rendered HTMX (no JS frameworks).

---

## M3.F — WO Cost Capture (G4) — 🆕 Not started

**Layer 2 — Weekly.** Closes matrix L2 *cost per work order: labour + materials + transport + invoice attached.*

**Goal.** Capture per-WO operational cost as the technician completes the WO, so the M7 owner report has real numbers.

**Deliverables.**
- `facilities/models/work.py`: `WorkOrderCost` (work_order FK, kind=LABOUR|MATERIALS|TRANSPORT|EXTERNAL, amount, currency, hours nullable, notes, recorded_at, recorded_by). Computed `WorkOrder.total_cost` aggregating all kinds.
- `WorkOrderAttachment.kind` choices extended with `INVOICE`.
- `WorkOrderCostService` (or extend `WorkOrderService`): `add_cost_line`, `remove_cost_line`, `recompute_total`. Materials lines auto-populated from `PartConsumption` once M5.5 lands (until then, manual entry only).
- View + template: cost panel on WO detail page; inline "add cost line" form. Invoice file upload reuses the existing attachment pattern.
- Tests: cost CRUD, total_cost aggregation across kinds, currency consistency check, integration with `WorkOrderService.complete()`.
- Migration: `facilities.0009_work_order_cost`.

**Review gate.** Open a WO → add labour line (hours + amount) → materials line (ad-hoc until M5.5) → transport line → upload invoice file → confirm `total_cost` rolls up correctly on the WO list and detail.

**Effort.** S–M (2–3 days).

---

## M4 — Occupant Portal — ✅ Done

**Goal.** Mobile-first intake page for TENANT / OCCUPANT roles. Closes matrix L1 *fault reporting* end-to-end (occupant side).

**Delivered (6 sub-phases, ~40 new tests, 434 total).**
- **4.A — Foundations + role-aware nav.** `ProjectRole.assigned_space` FK to `IFCSpatialElement` (migration `environments.0005`). `OccupantSpaceService` (TENANT outranks OCCUPANT). `is_occupant_mode` flag computed in `ProjectTabMixin._compute_occupant_mode` and `_role_context()`. `OccupantPortalAccessMixin`. Tab strip + Facilities sub-nav + sidebar gated for occupants. `_tenant.html` rebuilt with real cards.
- **4.B — Intake.** Extracted `_invoke_with_wallclock` to shared `_llm_helpers.invoke_with_wallclock`. New `spatial_lookup.resolve_space_candidates` (icontains + token-overlap, no `pg_trgm`). New `OccupantIntakeService.draft / .submit` (single-pass `format_json=True`, 20s wall-clock cap, server-side clamping non-negotiable). Migration `facilities.0008` adds `ActionRequest.user_text` + `draft_payload` + `draft_confidence`. Two portal views + session-cached draft tokens.
- **4.C — WebSocket push.** `OccupantPortalConsumer` on `ws/projects/<id>/fm/portal/` with per-user channel group `fm-portal-{user_id}-{project_id}` (narrow blast radius). Reused `fm-wo-{project_id}` for FM-side AR pushes. `ActionRequestService._broadcast_to_occupant` + `_broadcast_to_fm`.
- **4.D — Mobile CSS polish.** `.portal-scope` (44px tap targets, single-column ≤600px). `portal_help_modal.html` (canonical 5-section). `?` help pill on portal landing.
- **4.E — E2E test** exercising *"Meeting room 3-B is cold"* sentence end-to-end with mocked LLM.
- **4.F — FM-side Requests mobile collapse** — `@media (max-width: 600px)`.

**Beyond spec / scope decisions.**
- Photos deferred (open-debt #10). OS-native dictation works on iOS/Android textareas for free.
- No `OccupantIntakeProposal` audit row — AR is 1:1 (open-debt #9).
- TENANT/OCCUPANT keep `ProjectMembership=VIEWER` (open-debt #8 tracks the one-click "Add occupant" UX gap).
- Two channel groups, not three.
- Server-side LLM-output clamping on every field returned by intake (severity, spatial_id, title, description).

**Review gate (walk-through, pending user).**
1. Admin → add user as VIEWER → add `TENANT` role with `assigned_space` ("Meeting Room 3-B").
2. Log in. Project header should show only **Portal** tab; sidebar hidden.
3. Portal landing renders: assigned space card, intake CTA, recent requests empty state.
4. Open `?` help pill → confirm 5-section modal.
5. **New request** → type *"Meeting room 3-B is cold"* → **Draft request** → review fragment → confirm.
6. Open second browser as FM → **Facilities → Requests** → triage AR → tab 1 updates in place.
7. FM **Escalate** → category corrective → confirm. Tenant's status card → ESCALATED. New WO appears.
8. Phone test: portal URL on a real phone in portrait. 44px tap targets, single column, no horizontal scroll. OS dictation chip works.
9. `cd src && uv run pytest facilities/tests/ -v -x` — 434 green. `uv run ruff check . && uv run ruff format .` — clean.

---

## M4.5 — Access & Keys (G2) — 🆕 Not started

**Layer 1 — Daily.** Closes matrix L1 *keys & access: who holds which key, chip access records, key issuance log.*

**Goal.** End-of-day audit answers "who has the master key right now?" without spreadsheet hunting.

**Deliverables.**
- `facilities/models/access.py`: `AccessCredential` (project FK, kind=PHYSICAL_KEY|CHIP|FOB|CODE, identifier, area_or_asset nullable FK, notes), `CredentialIssuance` (credential FK, holder=User-or-free-text, issued_at, returned_at nullable, issued_by, notes — append-only via `save()` guard like `WorkOrderStatusEvent`).
- `AccessService`: `issue(credential, holder, issued_by, notes)`, `mark_returned(issuance, by, at=None)`, `currently_held(project)`, `held_by(user_or_text)`.
- Views + URLs + templates: credential ledger list (filter by area, holder, kind), drawer to issue / mark returned, "currently held" view for end-of-day audit.
- Sub-nav unlock: **Access & Keys** entry between **Permits** and **Requests** (or under Documents — TBD on review).
- Help modal `access_help_modal.html` (5-section pattern).
- Tests: model invariants (immutability of returned issuance — `returned_at` set-once?), service round-trips, view permissions, ledger filtering.
- Migration: `facilities.0010_access_credential`.

**Review gate.** Issue a key to a contractor → check ledger lists "currently held" → mark returned → audit log shows both events with timestamps and actors.

**IFC binding.** None required for V1. Could associate `AccessCredential.area_or_asset` with the spatial element it gates if downstream consumers need it.

**Effort.** M (3–5 days).

---

## M4.6 — Quick Contacts (G3) — 🆕 Not started

**Layer 1 — Daily.** Closes matrix L1 *quick contacts: emergency numbers, contractor reachable in one tap.*

**Goal.** Mobile-first directory of emergency + contractor + utility numbers; one-tap dial / mail.

**Deliverables.**
- `facilities/models/contacts.py`: `ProjectContact` (project FK, name, role_label, phone, email, kind=EMERGENCY|CONTRACTOR|UTILITY|OTHER, sort_order, notes).
- `ContactService`: simple CRUD + ordered list per kind.
- Views + URLs + templates: contacts page (grouped list by kind, EMERGENCY first), inline create/edit drawer for FM. Sidebar widget on portal + FM dashboard with the top 4 emergency contacts.
- One-tap rendering: `<a href="tel:...">` and `<a href="mailto:...">` with `.btn-tap` class (44px target on mobile).
- Help modal `contacts_help_modal.html`.
- Tests: model + service + view permissions; list ordering by kind then sort_order.
- Migration: `facilities.0011_project_contact`.

**Review gate.** FM creates 5 contacts (1 emergency, 2 contractors, 2 utilities) → verify portal sidebar shows the emergency entry top → tap a contractor on a phone → device launches dialer.

**Effort.** S (1–2 days).

---

## M4.7 — Notification escalation / SLA timers (R4) — 🆕 Not started

**Layer 1 — Daily.** Closes the matrix L1 push-notification ask: *"escalation after X hours without action."*

**Goal.** WOs that sit too long auto-escalate. Currently transitions broadcast — inaction does not.

**Deliverables.**
- `facilities/models/work.py`: SLA threshold per priority (configurable; defaults: P1=4h, P2=24h, P3=72h, P4=168h). Could live as a project-level setting `WorkOrderSLAConfig`, or as constants until configurability is needed.
- `facilities/services/escalation_service.py`: `scan_overdue(project, now)` returns WOs whose age in current status exceeds threshold; `escalate(wo)` advances or notifies the next role up (FACILITIESMANAGER if assignee inactive, BUILDINGOWNER if FM inactive).
- Periodic runner: Django management command `escalate_workorders` → cron / scheduler runs every N minutes per project.
- Notification: WS push to FM channel + email + audit row in `WorkOrderStatusEvent` (`actor=system`, `note="auto-escalated after X hours"`).
- Optional: surface badges on overdue WOs in kanban / list.
- Tests: threshold matrix per priority, no double-escalation, audit row written, idempotent re-runs.
- Migration: `facilities.0012_workorder_sla` (if config table is added).

**Review gate.** Create a P1 WO → freeze the clock 5 hours → run the management command → confirm an audit row + WS push + WO appears as escalated.

**Effort.** M (3–5 days).

---

## M4.8 — Photo geotag + mobile signature UI (R2 + R3) — 🆕 Not started

**Layer 2 — Weekly.** Closes matrix L2 *photo documentation* (geotag missing) and L2 *handover records with mobile signature*.

**Goal.** A photo uploaded from the field carries lat/long + author + timestamp. A WO completion captures a tap-drawn signature without leaving Castor.

**Deliverables.**
- `WorkOrderAttachment.latitude` + `longitude` (nullable Decimal). Mobile upload form requests `navigator.geolocation` and posts the coords; desktop upload leaves them null.
- HTMX/canvas signature widget (`<canvas>` + tap/mouse events → base64 PNG → uploaded as `WorkOrderAttachment.kind=SIGNOFF`). Bootstrap modal shape; "Clear" + "Save" buttons.
- Wired into the WO `Complete` transition: signature is **required** for that step on mobile breakpoints (configurable; can soft-warn instead).
- Display: photo gallery shows a small map pin when geotagged (link opens Google/Apple maps with the coord).
- Tests: model field nullability, WS form accepts missing geo, signature persistence + retrieval round-trip.
- Migration: `facilities.0013_attachment_geo`.

**Review gate.** Upload a photo from a real phone → confirm lat/long stamped + map link works. Complete a WO on mobile → signature pad opens → tap-draw → save → confirm `SIGNOFF` attachment created with the canvas image.

**Effort.** M (3–5 days).

---

## M5 — PM Planner + Manual-to-PM (AI #3) — ⏳ Not started

**Layer 2 — Weekly.** Closes matrix L2 *inspection calendar.*

**Goal.** Calendar-driven recurring tasks. Bonus: Castor reads O&M manuals and proposes PM plans.

**Deliverables.**
- `facilities/models/maintenance.py`: `MaintenancePlan`, `MaintenanceTaskInstance`, `InspectionTemplate`, `InspectionRun`, `ConditionLog`, `WorkCalendar`.
- Recurrence engine (every-N-period, quiet-hour-aware, `IfcWorkCalendar`-respecting).
- Calendar view for PM plans.
- **AI #3 wow:** "Propose PM plan from manual" — picks a manual from the doc store → RAG reads maintenance schedule section → drafts plan → FM approves → tasks on calendar.
- Tests: recurrence math, conflict detection (quiet hours, calendar exceptions), manual-to-PM prompt + parsing.

🔍 **Pending review (matrix scope):** the deeper offline-mobile inspection runner + jurisdiction-mapped regulatory template library (NFPA / IBC / CE / UK BSA) are not in the matrix — decide before M5 lands whether they're V1 or trim to "calendar + recurrence + manual-to-PM" only.

**Review gate.** Create a PM plan manually → verify calendar. Upload an AHU manual → trigger "Propose PM" → verify generated plan matches expected schedule → approve → tasks appear.

**Effort.** L (1–2 weeks).

---

## M5.5 — Spare Parts & Procurement (G1) — 🆕 Not started

**Layer 2 — Weekly.** Closes matrix L2 *spare parts inventory: what's in stock, what to reorder, from which supplier.*

**Goal.** End-of-WO part consumption decrements stock; reorder badges fire when stock < threshold; M7 owner report has a real materials-spend number.

**Deliverables.**
- `facilities/models/inventory.py`: `Supplier`, `SparePart`, `PartConsumption`. (See spec §6.L2.3 for fields.)
- `InventoryService`: `list_parts`, `restock`, `set_reorder_threshold`, `consume(work_order, part, qty)` (transactional with `WorkOrderCost` materials line in M3.F).
- Views + URLs + templates: stock list with reorder badges, supplier directory, part-picker drawer inside the WO completion flow.
- Optional CSV import for initial stock + supplier seeding.
- Help modal `inventory_help_modal.html`.
- Tests: stock decrement on consumption, reorder badge logic, consumption rollback on WO cancellation, supplier CRUD.
- Migration: `facilities.0014_inventory`.

**Review gate.** Seed two suppliers + five parts → set reorder threshold → close a WO consuming 3 of part-A → confirm stock decremented + reorder badge if threshold crossed → confirm `WorkOrderCost` materials line auto-created.

**Effort.** L (1 week).

---

## M6 — Documents v2 — ⏳ Not started

**Layer 2 + Layer 3.** Closes matrix L2 *contracts & deadlines* and L3 *H&S and fire safety docs.*

**Goal.** Every PDF that matters is classified, linked, and expiry-tracked.

**Deliverables.**
- Extend existing `documents` app: `DocumentAssociation` (doc ↔ asset/system/space/org), `DocumentExpiry`, doc-type classifier (manual, warranty, permit, certificate, drawing, spec, contract, invoice).
- LLM classifier at ingest (reuses existing pipeline).
- Expiry dashboard widget with traffic-light thresholds (90 / 30 / overdue) — surfaced on FM dashboard and the dedicated **Documents** tab.
- Compliance view: filter by `kind=certificate` for the H&S story (matrix L3.2).
- Tests: classifier output, expiry calculation, widget rendering, traffic-light thresholds.

🔍 **Pending review (Permit overlap):** decide whether to fold `Permit` (M3) into the M6 document flow (a permit becomes a `Document` with expiry + scan + optional WO M2M) or keep them separate. Recommend folding — see appendix.

**Review gate.** Upload a permit PDF → auto-classifies as permit → expiry surfaces on dashboard at T-90 / T-30 / overdue. Upload a fire-safety certificate → appears on the H&S compliance view.

**Effort.** M (3–5 days).

---

## M6.5 — Utility Meters & YoY (G5) — 🆕 Not started

**Layer 3 — Monthly.** Closes matrix L3 *energy & utilities: electricity, water, gas — year-on-year consumption trend.*

**Goal.** Lightweight V1 of the energy story. Monthly meter readings, YoY chart. Replaces the heavy Sensor / TimescaleDB stack as the L3 entry point.

**Deliverables.**
- `facilities/models/utilities.py`: `UtilityMeter` (project FK, kind=ELECTRICITY|WATER|GAS|HEAT, label, unit, supplier nullable FK to G1's Supplier, account_number), `MeterReading` (meter FK, period_start, period_end, value Decimal, source=MANUAL|CSV|BMS, recorded_by, recorded_at, notes). **Regular Postgres**, no TimescaleDB.
- `MeterService`: list / create meter, log reading (manual or CSV row), `monthly_series(meter, year)` for chart data, `yoy_overlay(meter, year_a, year_b)`.
- Views + URLs + templates: per-meter chart widget (12-month rolling, current vs prior year overlay) — reuse Chart.js if already in the bundle, else server-render an SVG. Manual entry form. CSV import (one row per meter+period+value).
- Tests: aggregation math, YoY overlay correctness, CSV import edge cases (gaps, duplicates), unit consistency.
- Migration: `facilities.0015_utilities`.

**Review gate.** Create a meter → enter 24 monthly readings (12 prior year, 12 current) → chart shows current vs prior year overlay correctly → CSV import 12 more rows → re-render.

**BMS hook.** `MeterReading.source=BMS` is a future-automation seam, not a V1 dependency. Keep the field; don't build the integration.

**Effort.** M (3–5 days).

---

## M7 — Owner Report (Narrative KPIs, AI #6) — ⏳ Not started

**Layer 3 — Monthly.** Closes matrix L3 *owner report: what was done, what it cost, what is coming up.*

**Goal.** Role-specific dashboard widgets that speak in sentences. End-of-month report email.

**Deliverables.**
- `facilities/services/narratives/`: one narrator per widget (PM compliance, WO backlog, SLA hit rate, cost per sq m, occupancy %, warranty leakage, *"EUR spent vs replacement"* per asset 🔍 — pulls from M3.F + M5.5 + M6.5).
- Widget framework — replaces M0 placeholder cards on each role dashboard.
- Scheduled report email hook (basic — Django management command + monthly cron).
- Tests: narrator prompt + parsing, widget rendering, fallback on sparse data, email assembly.

🔍 **Replace-vs-maintain (M8 / AI #9):** rather than the full M8 LCC advisor, M7 ships a simple *"€X spent on AHU-07 in 24 months; replacement €Y; recommend monitoring/replacement"* cell driven by M3.F costs. The full LLM advisor remains in the appendix as a candidate.

**Review gate.** Open FM dashboard → see narrative sentences like *"PM compliance dropped 12% this month, driven by HVAC — 8 overdue out of 14. Root cause: ACME HVAC unavailable Apr 8–14."* Trigger monthly email → confirm delivery to BUILDINGOWNER.

**Effort.** M–L (5–8 days; depends on widget count).

---

## ~~M8 — Replace-vs-maintain (AI #9)~~ — 🔍 Pending review

**Original goal.** Cost-curve-driven recommendation on whether to keep maintaining an asset or replace it.

**Status.** The matrix doesn't ask for capital-lifecycle decisions — *owner report* (L3) is operational, not strategic. M7 ships a simpler *"EUR spent vs replacement"* cell that meets the matrix ask.

**Decision needed.** Keep M8 as a stretch advisor (LCC ledger + curve + LLM recommendation with sources), trim it down, or drop it?

See appendix for the recommendation.

---

## Open debt

1. **~~Rationalize the three access mechanisms.~~ RESOLVED 2026-04-18.** Two-layer model: `ProjectMembership` (OWNER / EDITOR / VIEWER) + `ProjectRole` (functional). All access decisions via `environments.services.access_service.ProjectAccessService`.

2. **WebSocket mid-session re-check** (still open). Three consumers (`chat.AskConsumer`, `writeback.ProposalConsumer`, `documents.OCRConsumer`) re-check access only at connect time. Stash `membership_id + updated_at` at connect, re-check before each write-side `receive_json`.

3. **Feature-flag the Facilities tab** until M0 review gate is passed.

4. **Seed script** to create a demo project with 2–3 users + varied permissions + functional roles.

5. **ADMIN permission tier** — deferred to post-alpha.

6. **IFC pset ↔ FacilityAsset DB column overlap.** Resolved bidirectionally in M2 (promote seeds psets → DB; updates emit FMDeltas back). Three candidate directions still on the table for the broader "which is authoritative" pass; decision deferred until a concrete use case picks the winner.

7. **Orphan → linked reconciliation** — when an orphan asset's IFC counterpart appears later, no UI exists to link the existing row. Data model supports it.

8. **One-click "Add occupant" action** — today seating an occupant requires `ProjectMembership` + `ProjectRole=TENANT` + optional `assigned_space`. People-page button creating both rows in a single transaction.

9. **`OccupantIntakeProposal` audit row** — deferred from M4 by design. Add only if abuse / debug telemetry across rejected drafts becomes useful.

10. **`ActionRequestAttachment` model + photo capture** — deferred from M4. Phase 2: clone `WorkOrderAttachment` shape; `<input type="file" capture="environment">`. Builds toward open-debt #11.

11. **Vision-model intake** — phase 3. Photo → vision LLM extracts *"water stain on ceiling"* → enriches the AR draft. Builds on #10.

---

## Appendix — Review Candidates (not in expert matrix)

Everything currently spec'd or coded that has **no anchor in `facility_matrix.html`**. Split by whether it is **already in the codebase** (Group A, real triage decisions with code impact) or **spec-only / never built** (Group B, mostly trim-the-spec decisions). Within each group, ordered by **actionability** — safest cuts first, conditional decisions last.

**Triage status legend:** `pending` (not yet decided) · `decided-keep` · `decided-simplify` · `decided-delete` · `done`. Update the status column inline as decisions land; record the rationale in the Changelog.

**No code is deleted automatically.** Code stays put until each row is signed off and a dedicated follow-up commit lands.

---

### Group A — Shipped code (in `src/facilities/` — real triage with code impact)

11 items. Ordered: safe-to-delete → worth-simplifying → keep-as-Castor-scaffolding → conditional. The Verdict column is my recommendation; flip it to `decided-...` once you sign off.

#### A.1 — Safe to delete (low risk, low coupling)

| # | Item | Where it lives | Why not in matrix | Verdict | Status |
|---|------|---------------|-------------------|---------|--------|
| A1 | `AssetInventory` financial fields (`original_value`, `current_value`, `depreciated_value`, `total_replacement_cost`, `acquisition_date`) | `src/facilities/models/assets.py` — model registered but sparse / unpopulated; no view reads it | Matrix has no asset depreciation / valuation concept | **Delete** the model. If the M7 owner report ends up needing the *"EUR spent vs replacement"* cell, keep `total_replacement_cost` only and drop the rest. | `pending` |
| A2 | `condition_score` on `FacilityAsset` (0–100) | `src/facilities/models/assets.py`; index `(project, condition_score)` | Matrix doesn't mention condition scoring | **Delete** unless M5 PM trigger logic ends up needing it. Currently used only for non-load-bearing prioritization heuristics. | `pending` |

#### A.2 — Worth simplifying (real reduction, manageable touch)

| # | Item | Where it lives | Why not in matrix | Verdict | Status |
|---|------|---------------|-------------------|---------|--------|
| A3 | `ProjectRole` 9-role enum | `src/environments/models.py` + `src/facilities/views.py` + dashboards (8 templates) | Matrix implies ~5 personas: manager, technician, tenant, owner, contractor | **Simplify to 5.** Drop AUDITOR + CONSULTANT outright; collapse SUBCONTRACTOR into CONTRACTOR; collapse OCCUPANT into TENANT (`assigned_space` is already the discriminator). Decide before M4.5 lands so Access & Keys gating doesn't bake in the wider matrix. | `pending` |
| A4 | 10-stage `WorkOrderStatus` FSM (DRAFT / SUBMITTED / TRIAGED / ASSIGNED / SCHEDULED / IN_PROGRESS / COMPLETED / VERIFIED / CLOSED / CANCELLED) | `src/facilities/models/work.py`; touches `WorkOrderService._TRANSITIONS`, `WorkOrderConsumer` payloads, kanban columns, AR escalation chain | Matrix lists 4 states: open / in-progress / waiting for part / completed | **Simplify to 6:** DRAFT → OPEN → IN_PROGRESS → WAITING → COMPLETED → CANCELLED. Decide before M4.7 (SLA timers) — simpler FSM = simpler escalation rules. | `pending` |
| A5 | Role-specific dashboards (8 templates: `_owner`, `_fm`, `_engineer`, `_contractor`, `_tenant`, `_auditor`, `_default`, `_header`) | `src/facilities/templates/facilities/components/dashboards/` | Matrix is single-persona ("facility manager") | **Trim** to whatever survives A3. Auditor / consultant dashboards delete first; SUBCONTRACTOR / OCCUPANT collapse with their parents. | `pending` (blocked by A3) |
| A6 | `Permit` model with M2M to `WorkOrder` + `permit_number` + kind + `valid_from` / `valid_until` + status enum | `src/facilities/models/work.py` | Matrix mentions permits only as Layer 3 H&S/fire-safety scans, not as workflow gates | **Simplify** — fold into M6 Documents v2. A permit becomes a `Document` with `kind=permit` + `expiry_date` + scan attachment + optional WO link. Drops a model, unifies expiry tracking. Decide before M6 lands. | `pending` (blocked by M6) |

#### A.3 — Keep (Castor scaffolding — deliberate value-add over generic CMMS)

| # | Item | Where it lives | Why not in matrix | Verdict | Status |
|---|------|---------------|-------------------|---------|--------|
| A7 | `FMDelta` + `ExportReconciliationService` + `ifc_mappers/` + `ExportConsumer` (M2 — 80+ tests) | `src/facilities/models/exports.py`, `src/facilities/services/export_reconciliation_service.py`, `src/facilities/services/ifc_mappers/` | Matrix is BIM-agnostic | **Keep.** Bidirectional BIM-FM bridge — Castor's reason to exist. Without it, durable FM metadata never reaches the model. Annotated as Castor-specific in `7D-facility-management.md` §6.A.2. | `decided-keep` |
| A8 | `FMIntentService` + `FMIntentProposal` (Intent-to-WO LLM batch, M3.E) | `src/facilities/services/fm_intent_service.py`; migration `0007_fm_intent_proposal` | Matrix doesn't mention LLM-assisted batch creation | **Keep.** LLM wedge over generic CMMS. Already shipped + hardened (wall-clock caps, audit trail). Annotated as Castor-specific in `7D-facility-management.md` §6.A.3. | `decided-keep` |
| A9 | `Classification` + `ClassificationReference` (Uniclass etc.) | `src/facilities/models/assets.py` + `ifc_mappers/classification_mapper.py` + Tier1/2 writers | Matrix never mentions classification | **Keep.** Drives IFC export `SET_CLASSIFICATION` ops; deleting breaks round-trip integrity. Annotated as Castor-specific in `7D-facility-management.md` §6.A.4. | `decided-keep` |
| A10 | `WorkOrderStatusEvent` immutable audit chain (custom `save()` raising `ImmutableError` on update) | `src/facilities/models/work.py` | Matrix doesn't mandate immutable audit | **Keep.** One-line guard, regulatory-friendly, no user-facing complexity. Survives the A4 FSM trim unchanged. | `decided-keep` |

#### A.4 — Conditional (decide alongside another milestone)

| # | Item | Where it lives | Why not in matrix | Verdict | Status |
|---|------|---------------|-------------------|---------|--------|
| A11 | `WorkOrderCategory` enum (CORRECTIVE / PREVENTIVE / INSPECTION / INSTALLATION / DECOMMISSION) | `src/facilities/models/work.py` | Matrix doesn't categorize WOs | **Probably keep** — useful for the M7 owner-report category breakdown. Confirm once M7 design firms up; if the report doesn't break down by category, drop. | `pending` (blocked by M7) |

#### Group A summary (recommended cuts)

- **2 safe deletes** (A1 financial fields, A2 condition_score) — lowest-risk start. Do first.
- **4 simplifications** (A3 roles, A4 FSM, A5 dashboards, A6 Permit fold-in) — real signal, ordered by milestone gating.
- **4 keeps** (A7 export pipeline, A8 intent service, A9 classifications, A10 immutable events) — Castor's wedge.
- **1 conditional** (A11 WO category) — wait for M7.

---

### Group B — Spec-only (never built — trim-the-spec decisions)

9 items. These never landed in code; the appendix entry is purely about removing or downgrading them in `7D-facility-management.md` so future work doesn't accidentally re-spawn them.

| # | Item | Where it lives in the spec | Why not in matrix | Verdict | Status |
|---|------|---------------------------|-------------------|---------|--------|
| B1 | Sensor / Actuator / Controller / Alarm / AlarmRule / `SensorReading` (TimescaleDB hypertable) — full IoT stack | `7D-facility-management.md` Annex B (§6.B.4) | Matrix asks for monthly meter readings + YoY (L3), not real-time IoT | **Move to "future / customer-driven" annex** in the spec. Replaced by lightweight `UtilityMeter` (G5 / M6.5). Stack returns only when a customer pays for BMS integration. | `pending` |
| B2 | Replace-vs-maintain LCC analysis (M8 / AI #9) — `LifecycleCostLedger`, `CostSchedule`, `CostItem` | `7D-facility-management.md` §6.B.6; M8 in this file | Matrix has no capital-lifecycle decisions; *owner report* (L3) is operational | **Trim** to a simple *"EUR spent vs replacement"* cell on the M7 owner report (achievable from M3.F costs + A1's `total_replacement_cost` if kept). Full LCC + curve math + recommender pending sign-off. | `pending` |
| B3 | Spaces / Occupancy / Move Management (`IfcSpace` cards, lease metadata, stack plans, drag-and-drop on floor plans, `Pset_SpaceOccupancyRequirements`) | `7D-facility-management.md` §6.B.1 | Matrix has no space-management or move-order surface; L1 occupant assignment already covered by `ProjectRole.assigned_space` | **Defer.** No concrete user demand; high-effort low-matrix-value floor-plan UI. Park as future / customer-driven. | `pending` |
| B4 | Vendor & Contractor management with auto-ranking + performance history (`IfcOrganization`, framework contracts, hourly rates, SLA, certifications) | `7D-facility-management.md` §6.B.2 | Matrix mentions contractors only via *quick contacts* (L1.5 → M4.6) | **Trim significantly.** M4.6 `ProjectContact` is the only "vendor directory" V1 needs. Auto-ranking + performance history → future phase. | `pending` |
| B5 | Systems & Zones (`System`, `Zone`, `SystemMembership`, `SystemServesSpace`, distribution-system explorer, "what breaks if I shut down chiller-2" impact analysis) | `7D-facility-management.md` §6.B.3 | Matrix has no functional-hierarchy view | **Defer.** Castor wow with no matrix anchor. Park as future / customer-driven (strong demo material when needed). | `pending` |
| B6 | Risk & Safety register (`Pset_Risk`, hazard register, near-miss reporting) | `7D-facility-management.md` §6.B.5 | Matrix doesn't mention risk register; H&S is *certificates in one place* (L3.2) | **Drop from V1.** L3.2 cert dashboard (M6) covers the matrix ask. Heavier risk register is enterprise-FM territory. | `pending` |
| B7 | Map-overlay view of Work Orders on a floor plan | `7D-facility-management.md` §6.L1.3 (annotated); never built | Matrix doesn't ask for a map view; kanban + calendar + list cover it | **Drop from V1.** Removes the route from M3.C scope retroactively. | `pending` |
| B8 | Inspection runner — offline mobile + jurisdiction-mapped regulatory-template library (NFPA / IBC / CE / UK BSA) | `7D-facility-management.md` §6.B.7 / M5 sub-feature | Matrix L2 *inspection calendar* asks only for schedule + 30-day expiry alert | **Trim M5** to "calendar + recurrence + manual-to-PM" only for V1; offline mobile + template library defer until customer asks. | `pending` |
| B9 | Unselected LLM ideas — vendor ranking, consolidation advisor, anomaly explanations, vision inspection, audit-pack narrator, email-to-WO, nameplate OCR, voice field inspection, compliance bootstrap | `7D-facility-management.md` §11 | Matrix doesn't list LLM features | **Defer all.** Three confirmed for V1 (AI #3 manual-to-PM, AI #6 narrative KPIs, AI #9 trimmed to a cell). Each deferred idea returns only if a customer flow demands it. | `pending` |

---

### How to use this appendix

Walk Group A first — those rows have code consequences. For each row:

1. Decide **keep / simplify / delete** and update the **Status** column inline (`pending` → `decided-...`).
2. Add a one-line entry to the Changelog with the rationale.
3. Land spec edits + code changes in a dedicated follow-up commit (one row at a time, easy to revert).
4. When the change ships, flip the status to `done`.

Group B is purely spec-clean-up — those decisions only edit `7D-facility-management.md` (no code). Cheaper to walk.

**Suggested order for the first triage session:** A1 → A2 (the two safe deletes — gets a quick win, validates the workflow) → A11 (defer with M7) → A3 (role trim, unblocks A5 + M4.5) → A4 (FSM trim, unblocks M4.7) → B1–B9 in bulk (spec-only sweep). A6 (Permit fold-in) waits for M6.

---

## Rules of the road

- Each milestone is independently mergeable, independently demo-able, independently reviewable.
- Review gates are non-optional — the user walks through the "Review gate" section in the browser before the next milestone opens.
- Each milestone ships with tests (80% lines on new services, 90% on export reconciliation).
- No rework allowed — if a later milestone forces shape changes in an earlier one, stop and re-scope.
- Every new Django service reads `.claude/skills/django-service.md` first; every HTMX interaction reads `.claude/skills/htmx-patterns.md`; every test reads `.claude/skills/testing-skill.md`.
- After every milestone: `cd src && uv run pytest facilities/tests/ -v -x`, then `uv run ruff check . && uv run ruff format .`.
- **Every milestone must declare which matrix layer it serves (or `Castor scaffolding`).** No more invisible scope drift.

---

## Changelog

- **2026-04-17** — File created; M0 shipped, review walk-through pending. Open debt documented.
- **2026-04-18** — Access-model consolidation shipped. `ProjectMembership` rewritten with OWNER / EDITOR / VIEWER tiers + partial unique index on OWNER. `Project.collaborators` dropped. `Project.owner` switched to `on_delete=PROTECT`. `ProjectAccessService` + `ProjectModifyAccessMixin` are the single source of truth.
- **2026-04-21** — **M1 (Asset Register) shipped.** 155 tests. Two scope extensions: orphan assets, implicit-owner fallback. Open-debt items #6 and #7 added.
- **2026-04-23** — **M2 (Export Reconciliation v1) shipped** (commit `212e147`). FMDelta queue + ExportReconciliationService + ifc_mappers + ExportConsumer + dirty banner + Export IFC modal. Migrations `0004` + `0005`. Open debt #6 resolved bidirectionally. Tier1/Tier2 writers extracted to `ifc_processor`.
- **2026-04-23** — **M3 (Work Orders) shipped — all five sub-phases.** ~415 tests. Permits + ARs ship with full CRUD UI; Intent-to-WO is a separate `FMIntentService`; vanilla HTML5 DnD kanban; per-project `fm-wo-{project_id}` channel group with server-pre-rendered HTML in WS payload.
- **2026-04-26** — **M4 (Occupant Portal) shipped — six sub-phases.** 434 tests total. `ProjectRole.assigned_space`, `OccupantSpaceService`, `OccupantIntakeService` (server-side LLM-output clamping non-negotiable), `OccupantPortalConsumer` (per-user channel group, narrow blast radius), `.portal-scope` mobile CSS, E2E *"Meeting room 3-B is cold"* test. Migrations `environments.0005` + `facilities.0008`. New open-debt items #8, #9, #10, #11.
- **2026-05-04** — **Specs restructured around `facility_matrix.html`** (the expert-authored daily/weekly/monthly priority matrix). `7D-facility-management.md` reorganized: §6 Core functionalities now grouped by Layer 1 / 2 / 3 with G1–G5 model sketches inserted (Spare parts, Keys & access, Quick contacts, WO cost, Utility meters); IoT/sensor stack moved to *future / customer-driven* annex; non-matrix entities annotated 🔍 *Pending review*. This file (`7D-milestones.md`): milestone list re-ordered using the matrix's daily/weekly/monthly lens — new entries **M3.F** (WO cost capture), **M4.5** (Access & Keys), **M4.6** (Quick Contacts), **M4.7** (SLA timers / escalation), **M4.8** (photo geotag + mobile signature), **M5.5** (Spare Parts), **M6.5** (Utility Meters & YoY). M8 (Replace-vs-maintain) demoted to 🔍 *Pending review* — replaced by a simpler "EUR spent vs replacement" cell on the M7 owner report. **No code deleted.** New "Review Candidates" appendix listed 20 items for separate triage. Code-side action items are gated on per-row sign-off.
- **2026-05-04 (later)** — **Review Candidates appendix restructured** for clarity. Items now split into **Group A — Shipped code** (11 items in `src/facilities/`, real triage with code impact) and **Group B — Spec-only** (9 items, never built, trim-the-spec only). Group A subdivided by recommendation: A.1 *Safe to delete* (A1 `AssetInventory` financials, A2 `condition_score`), A.2 *Worth simplifying* (A3 ProjectRole 9→5, A4 WorkOrderStatus 10→6, A5 role-specific dashboards, A6 Permit fold-in to M6 Documents), A.3 *Keep — Castor scaffolding* (A7 export pipeline, A8 FMIntentService, A9 Classifications, A10 immutable status events) marked `decided-keep`, A.4 *Conditional* (A11 WorkOrderCategory, blocked by M7). New status column on every row (`pending` / `decided-...` / `done`) for incremental triage tracking. Suggested first-session order: A1 → A2 → A11 (defer) → A3 → A4 → bulk B1–B9 spec sweep; A6 waits for M6.
