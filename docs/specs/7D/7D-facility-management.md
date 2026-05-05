# Castor 7D — Facility Management Vision & Spec

> **Canonical scope:** [`facility_matrix.html`](./facility_matrix.html) — the expert-authored matrix of *"what a facility manager actually needs"*, organized by daily / weekly / monthly usage frequency. Every operational feature in this spec must map to a matrix item. Anything in this document that does not have a matrix anchor is flagged **🔍 Pending review** and listed in the appendix of [`7D-milestones.md`](./7D-milestones.md) for keep / simplify / delete triage.

## 1. Context

Castor already does the hard part: it binds IFC building models to technical documentation through a shared 1024d embedding space, lets users ask questions in natural language (Ask), and lets them propose risk-stratified modifications that round-trip into the IFC file via Git (Modify). That covers design handover well.

What Castor does **not** yet address is what the building does for the next fifty years of its life — the operational FM loop the matrix codifies: fault reporting and dispatch, inspections, documentation, compliance, and the monthly owner-facing summary.

This spec scopes Castor's entry into 7D with one guiding principle: **the FM team should find Castor so useful that they refuse to go back to spreadsheets + Archibus + a Slack channel.** Concretely, that means delivering every Layer 1, Layer 2, and Layer 3 item in the matrix, with Castor's IFC bridge and LLM features as the *delivery layer* — not the goal.

---

## 2. Three assumptions challenged before we commit

You gave an architectural direction ("edits live in Castor DB, IFC syncs on export"), a scope boundary ("geometry out of scope"), and an implicit one ("IFC is the source of truth"). All three deserve pressure-testing before the spec hardens around them.

### 2.1 "IFC is the source of truth" — only for durable metadata

This is correct for the physical model: geometry, spatial structure, element types, classifications, permanent psets. It is **wrong** for operational FM data. A work order closed in March 2027 does not belong in the IFC file. A meter reading from yesterday does not belong there either. A tenant complaint has no business being embedded in a model that gets sent to a structural engineer.

**Recommended partition:**

| Data class | Lives in | Round-trip to IFC? |
|---|---|---|
| Physical model, spatial tree, element types | IFC file (authoritative) | Read at ingest; write at export |
| Asset inventory, warranties, service life | IFC + Castor DB (mirrored) | Yes — durable FM metadata that belongs in the model |
| Documents (O&M manuals, certificates, permits) | Castor DB + object storage; `IfcDocumentReference` at export | Reference only; the file itself stays in Castor |
| Work orders, action requests, inspections | Castor DB (authoritative) | **Never.** At most, summaries as psets on the affected element at export (e.g., `LastMaintenanceDate`) |
| Utility meter readings (matrix L3) | Castor DB (authoritative, regular Postgres) | `IfcDocumentReference` reference only (optional) |
| Sensor readings, IoT streams 🔍 *Pending review* | External TSDB if/when needed | Reference only |
| Occupancy, key issuance, contact directory | Castor DB | Selective — see §7.3 |

This partition gives a clean mental model: IFC = what the building **is**; Castor DB = what is **happening** to it.

### 2.2 Deferred IFC sync is the right call, with one protocol addition

Current writeback commits each proposal to IFC via Git. For Modify mode (surgical edits to the model), that's fine. For FM, it's a disaster: a facility manager closing forty work orders in an afternoon does not want forty Git commits.

**Approved with discipline:** FM writes go to Castor DB immediately. IFC file is reconciled on an explicit `Export IFC` action via the **Export Reconciliation Service**. See §7 for the protocol. **One trap to avoid:** do not let IFC and DB drift silently. The project header banner shows *"IFC last exported 12 days ago — 47 pending FM updates"* whenever pending count > 0. Staleness is never hidden.

### 2.3 "Geometry out of scope" is mostly right, but rename it

FM's real geometry pain isn't geometry — it's **spatial containment changes**. Reassigning a tenant to a different floor, or relocating an asset, isn't geometry; it's `IfcRelContainedInSpatialStructure` rewiring.

Net: *"Castor does not edit geometry. Castor edits meaning, assignment, and relationships."*

---

## 3. The stakeholder model

🔍 **Pending review — see milestones appendix.** The matrix implies ~5 personas: facility manager (the protagonist), technician, tenant, owner (the recipient of the monthly report), contractor (the entry in *quick contacts*). The current implementation ships 9 roles (BUILDINGOWNER, FACILITIESMANAGER, MAINTENANCEENGINEER, CONTRACTOR, SUBCONTRACTOR, TENANT, OCCUPANT, AUDITOR, CONSULTANT). Decision deferred; this section preserves the V1 shape until the trim is approved.

| Role | Persona | Can do | Cannot do |
|---|---|---|---|
| `BUILDINGOWNER` | Asset owner, fund manager | See everything; approve capex; close fiscal periods; see costs | Edit physical model; dispatch work orders |
| `FACILITIESMANAGER` | FM lead | Everything in ops; approve work orders; manage vendors; schedule PMs | Close fiscal periods; approve capex > threshold |
| `MAINTENANCEENGINEER` | In-house technician | Self-assign WOs; log completions; update condition; capture photos | Approve others' work; set budgets; modify model |
| `CONTRACTOR` / `SUBCONTRACTOR` | External vendor | View assigned WOs; log work; upload certificates; submit invoices | See other contractors' rates; edit model; see financials beyond own POs |
| `TENANT` / `OCCUPANT` | Building occupant | Submit `IfcActionRequest`; track own requests; see assigned space | See the model; see other occupants' data; see costs |
| `AUDITOR` / `CONSULTANT` 🔍 | Periodic reviewer | Read-only on everything in-scope; comment; export reports | Change any state |

Each user's active role is project-scoped, shown in a pill in the navbar, and drives the entire UI (different landing pages, different sidebar trees, different available actions).

Implementation seam: `ProjectRole(user, project, role, valid_from, valid_until, assigned_space)` companion to `ProjectMembership`, mapped to `IfcActor` + `IfcActorRole` at export.

---

## 4. The "day in the life" test

Before cataloguing features, here is the concrete scenario that V1 must make pleasant. Each beat below maps to a matrix item.

> **07:45** — FM lead opens Castor on her phone during her commute. Landing dashboard: 3 work orders overdue (L1), 2 permits expiring this week (L2), one occupant request from last night (L1). She swipes to triage.
>
> **08:30** — Monday walk-through. She opens the asset list on her tablet, taps AHU-04, sees last service was 11 months ago (L2 inspection calendar) and the manual. She taps the mic: *"Log inspection: exterior fine, slight vibration, next PM in one month."*
>
> **10:00** — Tenant submits via occupant portal: *"Meeting room 3-B is cold."* (L1 fault reporting). The intake LLM classifies and routes it to the on-duty engineer (L1 technician assignment). Push notification fires (L1).
>
> **11:30** — A contractor on site needs the master key for the electrical room. FM logs the issuance against their badge in the keys & access ledger (L1). When the contractor returns it she taps *Returned*.
>
> **14:00** — Closing a WO: the technician logs labour hours, parts consumed from the spare-parts cabinet (L2 spare parts; L2 cost-per-WO), uploads before/after photos (L2 photo doc, geotagged + timestamped), and signs off on the mobile signature pad (L2 handover record).
>
> **16:30** — End of month. FM clicks **Generate owner report** (L3): what was done, what it cost, what's coming up. Energy (L3) shows the YoY trend chart.
>
> **17:00** — Optional, when needed: FM clicks **Export IFC**. Castor replays the durable FM deltas against the IFC and produces one Git commit. (Castor-specific scaffolding — not in the matrix; see §7.)

Every V1 feature below maps to one of those beats.

---

## 5. Information architecture — the new FM tab

Castor's workspace today is a tab strip: `Ask | Modify | Conflicts | History | Explore | Schedule`. FM adds one tab — **Facilities** — but the tab is a **shell**, not a single page. The workspace behind it is role-aware.

```
Project → Facilities
├── Dashboard          (role-specific landing — owner / FM / technician / tenant)
├── Assets             (registry, cards, bulk ops)
├── Work               (orders + requests + permits, kanban + calendar + list)
├── Maintenance        (PM plans, inspection calendar — M5)
├── Documents          (O&M, warranties, permits, H&S certificates — M6)
├── Inventory          (spare parts + suppliers — M5.5)
├── Access & Keys      (credential ledger + issuance log — M4.5)
├── Contacts           (emergency + contractor directory — M4.6)
├── Utilities          (meter readings + YoY — M6.5)
└── Reports            (owner report, KPIs, audit packs — M7)
```

Sub-tabs flagged for review (not on the matrix): `Spaces / Move`, `Systems & Zones`, `Sensors`, `Costs/LCC`, `Vendors`, `Risk`. These map to the existing spec sections kept as 🔍 *Pending review* below.

**Color-mode:** Facilities gets its own mode color — **teal `#14b8a6`** — distinguishing it from Ask (blue) and Modify (purple).

---

## 6. Core functionalities — organized by matrix layer

Each subsection states the matrix item being delivered, the user-facing capability, and the relevant IFC binding (where applicable). Items not in the matrix are clearly labelled.

### Layer 1 — Daily ("without these, nobody opens the system")

#### 6.L1.1 Fault reporting → `IfcActionRequest`

**Matrix:** *"Tenant photographs the issue, submits it, manager sees it immediately."*

A dramatically scoped-down UI for occupants (`TENANT`/`OCCUPANT`). They see: their assigned space, recent requests, a single text box for new ones. The text box is **not a form** — it's an LLM-assisted intake that extracts category, location (pre-filled from `assigned_space`), severity, and drafts an `ActionRequest`. Occupant confirms. Submission is instant.

Photos: 🔍 *open-debt #10* (deferred from M4). Voice: free via OS-native dictation on iOS/Android textareas.

**IFC binding:** `IfcActionRequest` + `Pset_ActionRequest`; escalation to `IfcProjectOrder` via `IfcRelSequence`.

#### 6.L1.2 Technician assignment → `WorkOrder.assignee_user`

**Matrix:** *"Who is available, where they are, what they already have on their plate."*

Single-select assignment from the WO detail; bulk assignment from the kanban toolbar. Active-load badge (open WO count) per technician. External vendors are free-text in V1 (`assignee_vendor`); promotes to a Vendor model only if/when needed.

#### 6.L1.3 Live work status → `WorkOrderStatus` FSM

**Matrix:** *"Open / in progress / waiting for part / completed."*

🔍 **Pending review (status depth):** the matrix lists 4 states; the implementation ships 10 (`DRAFT → SUBMITTED → TRIAGED → ASSIGNED → SCHEDULED → IN_PROGRESS → COMPLETED → VERIFIED → CLOSED`, plus `CANCELLED`). A simpler FSM (`DRAFT → OPEN → IN_PROGRESS → WAITING → COMPLETED → CANCELLED`) would match the matrix. Decision deferred to the milestones appendix.

Each transition writes an immutable `WorkOrderStatusEvent` (audit chain) and broadcasts via WebSocket on `fm-wo-{project_id}` so every connected client (kanban, calendar, mobile) updates in place.

Views: **Kanban** (drag cards across stages — vanilla HTML5 DnD), **Calendar** (HTMX month grid by `scheduled_start`), **List** (filterable, exportable). Map-overlay view 🔍 *Pending review* (no matrix anchor).

**IFC binding:** `IfcProjectOrder` + `Pset_ProjectOrderMaintenanceWorkOrder` — summary only at export (closed-and-verified WOs become `LastMaintenanceDate` / `LastMaintenanceBy` on the affected element).

#### 6.L1.4 Push notifications → WebSocket + email + escalation timers

**Matrix:** *"New fault, approaching deadline, escalation after X hours without action."*

Transport delivered: WebSocket fan-out via channel groups (`fm-wo-{project_id}` for FM staff, `fm-portal-{user_id}-{project_id}` for the AR submitter — narrow blast radius). Email piggybacks on the same hooks.

**Gap (R4):** the inaction-trigger ("escalation after X hours without action") is **not yet built**. Closes in **M4.7 Notification escalation / SLA timers** — periodic scan that escalates WOs older than priority-driven thresholds and pings the next role up.

#### 6.L1.5 Quick contacts → `ProjectContact` (G3)

**Matrix:** *"Emergency numbers, contractor reachable in one tap."*

**Gap.** Lightweight directory model:

```
ProjectContact
  project (FK)
  name, role_label
  phone, email
  kind = EMERGENCY | CONTRACTOR | UTILITY | OTHER
  sort_order
```

Surface in the mobile portal sidebar and the FM dashboard sidebar; one-tap `tel:` and `mailto:` on mobile. Lands in **M4.6** (or folded into M4 retroactively).

#### 6.L1.6 Keys & access management → `AccessCredential` + `CredentialIssuance` (G2)

**Matrix:** *"Who holds which key, chip access records, key issuance log."*

**Gap.** Two-model sketch:

```
AccessCredential
  project (FK)
  kind = PHYSICAL_KEY | CHIP | FOB | CODE
  identifier (the key tag / chip serial / etc.)
  area_or_asset (FK to IFCSpatialElement or FacilityAsset, nullable)
  notes

CredentialIssuance  (append-only)
  credential (FK)
  holder (FK to User or free-text holder for non-users)
  issued_at, returned_at (nullable while held)
  issued_by (FK to User)
  notes
```

UI: a credential ledger filterable by area / holder; one-click "issue" / "mark returned" actions; "currently held" view for end-of-day audit. Lands in **M4.5** — Layer 1 priority, lands early.

**IFC binding:** none required for V1. Could associate `AccessCredential.area` with the spatial element it gates if IFC consumers need that view.

---

### Layer 2 — Weekly ("skip these and things start falling apart")

#### 6.L2.1 Inspection calendar → PM Planner (M5)

**Matrix:** *"Lifts, boilers, fire extinguishers — alert 30 days before expiry."*

Calendar-driven recurring-task engine. Each PM plan generates `IfcTask` instances against `IfcWorkCalendar`-aware schedules, respecting tenant quiet hours.

Plans defined declaratively: *every 6 months on all `IfcUnitaryEquipment` of type `CHILLER`, requiring certified HVAC technician, 4h duration*. Calendar conflict detection is first-class.

**LLM bonus (AI #3) — PM plans proposed from manuals:** upload a manufacturer's manual; the RAG pipeline already ingests it; Castor reads the maintenance schedule section, drafts a PM plan, FM approves with one click.

**IFC binding:** `IfcWorkPlan` → `IfcWorkSchedule` → `IfcTask` + `IfcTaskTime` + `IfcWorkCalendar`.

#### 6.L2.2 Handover records (with mobile signature) → `WorkOrderAttachment.kind=SIGNOFF`

**Matrix:** *"Service sheet, photo evidence, mobile signature on completion."*

Storage and the SIGNOFF attachment kind ship in M3. **Gap (R3):** the **mobile signature capture UI** is not built — needs an HTMX/canvas signature widget on the WO completion flow at mobile breakpoints. Closes in **M4.8**.

#### 6.L2.3 Spare parts inventory → `Supplier` + `SparePart` + `PartConsumption` (G1)

**Matrix:** *"What's in stock, what to reorder, from which supplier."*

**Gap.** Three-model sketch:

```
Supplier
  project (FK)
  name, contact, payment_terms
  notes

SparePart
  project (FK)
  sku, name
  supplier (FK, nullable)
  current_stock, reorder_threshold
  unit_cost, currency
  compatible_assets (M2M → FacilityAsset)

PartConsumption
  work_order (FK)
  part (FK to SparePart)
  qty, unit_cost_at_time
  recorded_at, recorded_by
```

`PartConsumption` doubles as the materials line of cost-per-WO (G4). UI: stock list with reorder badges (current < threshold → red); part-picker dialog inside the WO completion flow that decrements stock on save. Lands in **M5.5** — needed before M7 owner reports surface spend.

#### 6.L2.4 Cost per work order → `WorkOrderCost` + invoice attachment (G4)

**Matrix:** *"Labour + materials + transport, invoice attached."*

**Gap.** One-model sketch + reuse of attachments:

```
WorkOrderCost
  work_order (FK)
  kind = LABOUR | MATERIALS | TRANSPORT | EXTERNAL
  amount, currency
  hours (nullable, used when kind=LABOUR)
  notes
  recorded_at, recorded_by
```

Materials lines are auto-populated from `PartConsumption` (G1). Invoice files reuse `WorkOrderAttachment` with a new `kind=INVOICE`. Computed `WorkOrder.total_cost` aggregates all four kinds.

UI: cost panel on the WO detail page; "add cost line" inline form. Lands in **M3.F** — small follow-up to M3, unblocks M7 owner report numbers.

#### 6.L2.5 Contracts & deadlines → `Permit` (M3) + `DocumentExpiry` (M6)

**Matrix:** *"Leases, service agreements, expiry dates visible at a glance."*

`Permit` (M3) handles regulatory permits today. The broader contract/lease/service-agreement story lands in **M6 Documents v2** via `DocumentExpiry` with traffic-light dashboard (90 / 30 / overdue).

🔍 **Pending review (Permit ↔ Document overlap):** the matrix mentions permits only as Layer 3 H&S/fire-safety docs, not as workflow gates. Consider collapsing `Permit` into the M6 document flow (a permit becomes a document with expiry + scan), and dropping the `Permit ↔ WorkOrder` M2M unless used.

**IFC binding:** `IfcPermit` + `Pset_Permit` (when retained); `IfcDocumentReference` + `IfcDocumentInformation` for documents.

#### 6.L2.6 Photo documentation (geotagged + timestamped + author) → `WorkOrderAttachment.kind=PHOTO`

**Matrix:** *"Before/after condition, geotagged, timestamped, with author."*

Author + `created_at` ship in M3. **Gap (R2):** **geotag is not captured** — add nullable `latitude` / `longitude` columns to `WorkOrderAttachment` for photos uploaded from mobile (use `navigator.geolocation` on the upload form). Closes in **M4.8** alongside mobile signature.

---

### Layer 3 — Monthly (management & compliance)

#### 6.L3.1 Owner report → Narrative KPIs (M7)

**Matrix:** *"What was done, what it cost, what is coming up next."*

Role-specific landing dashboards with widget narrators. Each widget speaks in sentences, not numbers:

> *"PM compliance dropped 12% this month, driven primarily by HVAC (8 overdue out of 14). Root cause: ACME HVAC contractor unavailable Apr 8–14. Recommend extending window or adding secondary vendor."*

**LLM (AI #6 — user-confirmed wow feature):** the narrators are LLM-generated from the underlying data with traceable citations.

Inputs: WO totals, PM compliance %, SLA hit rate, cost-per-WO aggregates (G4), spare-parts spend (G1), utility YoY (G5). Lands in **M7 Narrative KPIs**.

#### 6.L3.2 H&S and fire safety docs → `DocumentExpiry` (M6)

**Matrix:** *"All valid certificates in one place, scanned originals attached."*

LLM doc-classifier at ingest tags each upload as `manual | warranty | permit | certificate | drawing | spec | contract | invoice`; `DocumentExpiry` carries the expiry date. Dashboard widget surfaces anything expiring in the next 90 / 30 / overdue (traffic light).

**IFC binding:** `IfcDocumentReference` + `IfcDocumentInformation` + `IfcRelAssociatesDocument`. File itself stays in Castor object storage; IFC carries only the reference.

#### 6.L3.3 Energy & utilities (YoY trend) → `UtilityMeter` + `MeterReading` (G5)

**Matrix:** *"Electricity, water, gas — year-on-year consumption trend."*

**Gap — and a deliberate simplification.** The matrix asks for monthly aggregated meter readings with a YoY comparison chart, not real-time IoT. Two-model sketch on regular Postgres (no TimescaleDB):

```
UtilityMeter
  project (FK)
  kind = ELECTRICITY | WATER | GAS | HEAT
  label, unit (kWh / m³ / etc.)
  supplier (free text or FK to Supplier from G1)
  account_number

MeterReading
  meter (FK)
  period_start, period_end
  value (decimal)
  source = MANUAL | CSV | BMS
  recorded_by, recorded_at
  notes
```

UI: per-meter chart widget — 12-month rolling, current vs. prior year overlay. Manual entry form + CSV import. BMS source is a hook for future automation, not a V1 dependency. Lands in **M6.5** — lightweight V1, replaces the Sensor stack as the L3 entry point.

The full real-time `Sensor` / `Actuator` / `Alarm` stack moves to a *future / customer-driven* annex (§9.B below). The expert's actual ask is met by `UtilityMeter`.

---

### Annex A — Castor-specific scaffolding (kept; **not** in the matrix because the matrix is BIM-agnostic)

These features are not in the expert matrix because the expert is BIM-agnostic. They are **Castor's reason to exist** and stay as durable scaffolding around the matrix-driven core. None is flagged for deletion.

- **§6.A.1 Asset Register (M1).** Cards-not-rows registry of every physical thing with a serial number or service obligation. Hero card shows identification, location breadcrumb, manufacturer/model, commissioning date, warranty status, last 3 WOs, next 3 PMs. Bulk ops (classify, set responsible party, CSV import). Orphan support (`ifc_entity` nullable) for items not modelled in IFC. Foundation for every Layer 1 / 2 / 3 feature above. **IFC binding:** `IfcAsset` group + member `IfcElement`; `Pset_AssetInventory`, `Pset_ManufacturerOccurrence`, `Pset_Warranty`, `Pset_ServiceLife`.
- **§6.A.2 Export Reconciliation (M2).** FMDelta queue + `ExportReconciliationService` + `ifc_mappers/`. The matrix is BIM-agnostic; this is what makes Castor a BIM-FM tool rather than a CMMS. See §7.
- **§6.A.3 Intent-to-WO LLM batch (M3.E).** *"Schedule filter replacement on all rooftop AHUs next Tuesday, assign to ACME HVAC, priority 2"* → preview modal → confirm → N WOs. Two-pass LLM (classifier + planner) with hard wall-clock caps. Castor's wedge; not in matrix.
- **§6.A.4 Classifications (Uniclass et al.).** Used by the IFC export pipeline (`SET_CLASSIFICATION` op). Removing it breaks export. Keep.
- **§6.A.5 Ask integration with FM intent.** RAG router classifies *"which chillers haven't been serviced in 12 months?"* as an FM SQL-aware retrieval, not a vector search. Retains the natural-language surface FM staff already use elsewhere in Castor.

### Annex B — Pending review (not in the matrix; preserved here until triaged)

🔍 **All items in this annex are listed in the [Review Candidates appendix of `7D-milestones.md`](./7D-milestones.md).** Code stays put for now; deletion happens only after explicit user sign-off, item by item.

- **§6.B.1 Spaces / Occupancy / Move Management.** `IfcSpace` occupancy cards, lease metadata, stack plans, drag-and-drop on floor plans. Not in matrix.
- **§6.B.2 Vendor & Contractor management (auto-ranking, performance history).** Matrix mentions contractors only via *quick contacts* (L1). The vendor-ranking story is Castor-specific; consider pruning.
- **§6.B.3 Systems & Zones (functional hierarchies, impact analysis).** *"What breaks if I shut down chiller-2"* — Castor wow but not in matrix.
- **§6.B.4 Sensors / Actuators / Alarms / TimescaleDB.** Real-time IoT. Replaced for V1 by the lightweight `UtilityMeter` (G5). Heavy stack is a *future / customer-driven* annex.
- **§6.B.5 Risk & Safety register (`Pset_Risk`, hazard register).** Not in matrix.
- **§6.B.6 Cost dashboards / lifecycle financials / `LifecycleCostLedger` / replace-vs-maintain (M8 AI #9).** The matrix has *owner report* (L3) which needs *some* cost data, but the LCC math + replace-vs-maintain recommendation is enterprise-FM territory the expert didn't list. Trim to a simple "EUR spent vs replacement cost" cell on the owner report; keep the AI #9 hook for later.
- **§6.B.7 Inspection runner (mobile, offline, regulatory template library).** The L2 *inspection calendar* is in the matrix; the deeper offline-mobile + jurisdiction-mapped templates (NFPA / IBC / CE / UK BSA) are not. Decide how much of M5 is matrix-driven vs aspirational.

---

## 7. Deferred sync — the Export Reconciliation Protocol (Castor scaffolding, **kept**)

The central architectural commitment. Every FM write goes to Castor DB first. IFC is reconciled explicitly. **Not in the matrix** — this exists because Castor is bidirectional with BIM; without it, durable FM metadata never reaches the model. Keep.

### 7.1 The pending-delta queue

Each FM op produces an `FMDelta` row: `(project, entity_guid, operation, payload, created_at, created_by, applied_to_ifc_at, ifc_commit_hash)`. While `applied_to_ifc_at` is null, the delta is pending. Project header banner renders *"⚠ N FM updates not in IFC — last export X days ago"* whenever pending count > 0.

### 7.2 Export flow

On `Export IFC`:

1. **Plan phase.** List pending deltas, group by entity, collapse last-write-wins per `natural_key`, render preview.
2. **Snapshot.** Git snapshot of current IFC.
3. **Replay.** Each delta applied via the existing Tier1/Tier2 writers (extracted to `ifc_processor`). FM ops slot into `SET_PROPERTY`, `ADD_PSET`, `SET_CLASSIFICATION`, `SET_ATTRIBUTE`.
4. **Validate.** IFC reopened and checked for structural integrity.
5. **Commit.** One Git commit with semantic message: *"FM reconciliation 2026-04-17: 34 entities, 47 deltas, by Maria Santos"*.
6. **Mark applied.** All deltas stamped with `ifc_commit_hash` + `applied_to_ifc_at`.
7. **Rollback.** Any failure → Git rollback + deltas stay pending.

WebSocket streams `plan → snapshot → apply → validate → save → commit` phases live to the FM running the export.

### 7.3 Which FM ops do NOT go to IFC

Operational data (mid-lifecycle WOs, meter readings, occupant requests, in-progress inspections, key issuance, contacts, spare-parts stock) **never leaves Castor DB**. Only **settled, durable** FM data reaches the IFC export:

- Closed-and-verified WOs → summarized as `LastMaintenanceDate`, `LastMaintenanceBy` on the affected element
- Completed inspections → condition score on `Pset_ConditionCriteria`
- Ratified warranty metadata → `Pset_Warranty`
- Confirmed occupancy assignments → `IfcRelOccupiesSpaces`
- Approved documents → `IfcRelAssociatesDocument` + reference
- Classified assets → `IfcRelAssociatesClassification`

This partition is a configurable "export profile." Default is opinionated.

### 7.4 Round-trip safety

Re-ingesting an IFC that Castor previously exported must not duplicate FM data. At re-ingest, Castor matches entities by GUID, detects psets it wrote on previous export, and treats them as idempotent.

---

## 8. Natural-language surfaces — how FM uses Ask and Modify (Castor scaffolding, **kept**)

Castor's differentiator is that FM ops should be **conversational** where it saves time and **structured** where it prevents errors. The split:

| Interaction | Modality | Example |
|---|---|---|
| Information retrieval | Ask (chat) | *"Which chillers haven't been serviced in 12 months?"* (matrix L2 inspection calendar) |
| Single-entity simple edit | Inline on the card | Click warranty expiry → date picker |
| Multi-entity bulk edit / WO batch | LLM intake → preview → confirm | *"Schedule filter replacement on all rooftop AHUs next Tuesday"* (matrix L1 WO creation) |
| Occupant intake | Portal textarea + LLM draft | *"Meeting room 3-B is cold"* (matrix L1 fault reporting) |
| Audit / compliance query 🔍 | Ask with report export | *"12-month compliance log for ELV-01"* — flag unless owner-report-driven |

**FM intent classifier:** queries about WOs, assets, warranties, schedules route to FM-aware retrieval that joins SQL with vector search. *"Chillers not serviced in 12 months"* = SQL aggregation on `WorkOrder` JOIN `FacilityAsset`, not vector search.

---

## 9. Data model — by matrix layer

Models grouped by the matrix item they back. `UUIDModel` base, project-scoped, audit-trail patterns. New entities are marked 🆕; existing are 🟢 *shipped*; review-pending are 🔍.

### 9.A Matrix-aligned (build / keep)

**Layer 1**
- 🟢 `ActionRequest` — fault reporting (L1.1). M3 + M4 intake fields.
- 🟢 `WorkOrder` + `WorkOrderStatusEvent` + `WorkOrderAttachment` — assignment, status, photos (L1.2 / L1.3 / L2.6). M3.
- 🆕 `ProjectContact` — quick contacts (L1.5). G3 / M4.6.
- 🆕 `AccessCredential` + `CredentialIssuance` — keys & access (L1.6). G2 / M4.5.
- 🆕 *(no model — service hook)* SLA / escalation timer scan → existing notification transport (L1.4 R4). M4.7.

**Layer 2**
- 🟢 `FacilityAsset` (+ orphan support) — foundation for every L2 item. M1.
- 🟢 `Permit` — L2.5 contracts/deadlines (review pending; possibly fold into Document). M3.
- 🆕 `WorkOrderCost` + `WorkOrderAttachment.kind=INVOICE` — cost-per-WO (L2.4). G4 / M3.F.
- 🆕 `Supplier` + `SparePart` + `PartConsumption` — spare parts (L2.3). G1 / M5.5.
- 📋 `MaintenancePlan` + `MaintenanceTaskInstance` + `WorkCalendar` — inspection calendar (L2.1). M5.
- 📋 `DocumentAssociation` + `DocumentExpiry` + doc-type classifier — contracts/leases + H&S certs (L2.5 / L3.2). M6.
- 🆕 *photo geotag fields* — `WorkOrderAttachment.latitude` / `longitude` (L2.6 / R2). M4.8.
- 🆕 *mobile signature capture UI* — no new model; ties to existing `WorkOrderAttachment.kind=SIGNOFF` (L2.2 / R3). M4.8.

**Layer 3**
- 🆕 `UtilityMeter` + `MeterReading` — energy/utilities YoY (L3.3). G5 / M6.5.
- 📋 *(narrative widget framework)* — owner report (L3.1). M7.

### 9.B Castor scaffolding (kept; not in matrix)

- 🟢 `ProjectRole` — stakeholder model. Trim 🔍 *Pending review*.
- 🟢 `Classification` + `ClassificationReference` — used by IFC export.
- 🟢 `FMDelta` + `ExportJob` + `ExportProfile` — Export Reconciliation pipeline.
- 🟢 `FMIntentProposal` — Intent-to-WO audit.
- 🟢 `AssetInventory` 🔍 — currently sparse financial fields; consider deletion unless owner report needs them.

### 9.C Review pending (not in matrix; deferred or candidate for deletion)

- 🔍 `System` / `Zone` / `SystemMembership` / `SystemServesSpace` — functional hierarchies (§6.B.3).
- 🔍 `Sensor` / `Actuator` / `Controller` / `Alarm` / `AlarmRule` / `SensorReading` (TSDB) — full IoT stack (§6.B.4). Replaced by `UtilityMeter` for V1.
- 🔍 `CostSchedule` / `CostItem` / `LifecycleCostLedger` — LCC + replace-vs-maintain (§6.B.6 / M8).
- 🔍 *Move-management entities* — spatial reassignment workflow (§6.B.1).
- 🔍 *Risk register entities* — `Pset_Risk` driven (§6.B.5).
- 🔍 *Vendor model* — vendor directory + auto-ranking (§6.B.2). Currently `WorkOrder.assignee_vendor` is free text.

Every retained FM model has a matching **IFC export mapper** registered in `ifc_mappers/`.

---

## 10. Frontend surfaces — by matrix priority

Phase order driven by daily / weekly / monthly criticality, not feature complexity:

**Layer 1 (build first / closed first)**
1. Facilities tab shell + role-aware landing — 🟢 M0
2. Asset detail card — 🟢 M1 (foundation; matrix L2 items hang off it)
3. Work Order list + kanban + calendar + detail — 🟢 M3
4. Occupant portal page (mobile-first) — 🟢 M4
5. Keys & access ledger — 🆕 M4.5
6. Quick contacts card — 🆕 M4.6
7. SLA / escalation surface (badges on overdue WOs + notification) — 🆕 M4.7
8. Photo geotag + mobile signature pad — 🆕 M4.8

**Layer 2**
9. WO cost panel + invoice attachment — 🆕 M3.F
10. PM Planner + inspection calendar — 📋 M5
11. Spare-parts inventory + part-picker on WO completion — 🆕 M5.5
12. Document Hub with expiry traffic-light dashboard — 📋 M6

**Layer 3**
13. Utility meters + YoY chart — 🆕 M6.5
14. Owner report (narrative KPI widgets) — 📋 M7
15. H&S / fire safety compliance dashboard (fold into M6) — 📋 M6

**Castor scaffolding (continuous)**
16. Export IFC modal with delta preview — 🟢 M2

🔍 *Pending review:* inspection runner mobile UX (M5 sub-feature), space stack plans (§6.B.1), sensor dashboard (§6.B.4), vendor directory (§6.B.2), cost lifecycle curves (§6.B.6).

All surfaces lean on existing Castor patterns: HTMX partials, WebSocket streaming for live state, Bootstrap card grid, the spatial tree + right-detail pattern from `_explore.html`. No new frontend framework.

---

## 11. AI / LLM features (chosen carefully)

Three for V1, picked in the original spec and reaffirmed against the matrix:

1. **Manual-to-PM (AI #3)** — supports L2 *inspection calendar*. RAG reads O&M manuals → drafts PM plan → FM approves. M5.
2. **Narrative KPIs (AI #6)** — supports L3 *owner report*. Widgets speak in sentences. M7.
3. **Replace-vs-maintain (AI #9)** 🔍 — partly supports L3 *owner report* (cost narrative). The deeper LCC math + capital recommendation is enterprise-FM territory not in the matrix; trim to a simple "EUR spent vs replacement cost" cell on the owner report and decide later if the full advisor is worth M8 effort.

Plus the already-shipped LLM features that map to the matrix:

- **Tenant-intake classifier (L1.1)** — `OccupantIntakeService.draft / .submit`. M4. Shipped.
- **Intent-to-WO batch (L1.2 / L1.3)** — `FMIntentService`. M3.E. Shipped.
- **FM intent routing in Ask (Castor scaffolding)** — RAG classifier sends FM queries to SQL-aware retrieval. M1. Shipped.

🔍 **Other LLM ideas in the original spec (vendor ranking, consolidation advisor, anomaly explanations, vision inspection, audit-pack narrator, email-to-WO, nameplate OCR, voice field inspection, compliance bootstrap)** are not in the matrix and not in V1 scope. Each is in the milestones appendix as a `flag` candidate.

---

## 12. Phasing — see [`7D-milestones.md`](./7D-milestones.md)

Milestone-level ordering and status (M0–M7 with the new M3.F / M4.5 / M4.6 / M4.7 / M4.8 / M5.5 / M6.5 inserts) lives in the milestones doc. Summary:

- **Shipped:** M0 scaffold, M1 Asset Register, M2 Export Reconciliation, M3 Work Orders, M4 Occupant Portal.
- **Layer 1 closures (next):** M3.F WO cost capture, M4.5 Access & Keys, M4.6 Quick Contacts, M4.7 SLA timers, M4.8 photo geotag + mobile signature.
- **Layer 2 closures:** M5 PM Planner, M5.5 Spare Parts, M6 Documents v2.
- **Layer 3 closures:** M6.5 Utility Meters & YoY, M7 Owner Report (Narrative KPIs).
- **Pending review:** M8 Replace-vs-maintain (trim to owner-report cell vs full advisor — decide later).

---

## 13. Integrations that matter (in priority order)

1. **Email** (SMTP in/out) — occupant intake confirmations, WO alerts, expiry alerts. L3 owner report sent monthly.
2. **Calendar** (Outlook/Google iCal export) — PM and inspection schedules on tech calendars (L2).
3. **CSV import / export** — meter readings (G5), spare-parts stock (G1), assets (M1). Lowest-friction integration shape.
4. **Accounting** (CSV export V1; webhook later) — invoice flow (L2.4) → owner report (L3.1).
5. **Badge / access control read-only ingest** — chip access events feeding `CredentialIssuance` / occupancy data. Phase 2.
6. **eSignature** — sign-off flows for high-value permits. Phase 2.
7. **BMS / IoT gateway** 🔍 — hooked into `MeterReading.source=BMS` for utility automation. Heavy sensor stack stays out of V1.

---

## 14. Verification plan

Scenario-driven acceptance, each a "day-in-the-life" slice end-to-end:

- **Scenario A — FM Monday triage (L1):** load project → dashboard shows counts → click overdue WO → triage → assign → WS push verified on second client.
- **Scenario B — Occupant request (L1):** tenant submits via portal → verify AR created, routed, FM sees it, status pushes back.
- **Scenario C — Key issuance (L1):** issue a key to a contractor → check ledger → mark returned → check audit log.
- **Scenario D — WO completion with cost + photos (L2):** technician closes a WO → labour line + parts consumed + invoice file + before/after geotagged photos + mobile signature → verify everything persists and rolls into total_cost.
- **Scenario E — Manual-to-PM (L2):** upload AHU manual → trigger "Propose PM plan" → verify schedule → approve → tasks on calendar.
- **Scenario F — Document expiry (L3):** upload a permit PDF → auto-classifies → expiry traffic light surfaces at T-90 / T-30 / overdue.
- **Scenario G — Owner report (L3):** end of month → click *Generate report* → narrative widgets render with cost + spare-parts spend + utility YoY.
- **Scenario H — Export IFC (Castor scaffolding):** accumulate 10 FM deltas → Export → preview → confirm → verify IFC written, one Git commit, deltas marked applied.

Coverage targets: 80% lines on new services, 90% on export reconciliation. Parametrized tests for each FM-certified op against a golden IFC fixture.

---

## 15. Open questions

1. **ProjectRole trim.** 9 → 5? Decide before M4.5 lands so the Access & Keys role gating doesn't bake in the wider matrix.
2. **WorkOrder status depth.** 10 stages vs the matrix's 4? Decide before M4.7 (SLA timers) — simpler FSM = simpler escalation rules.
3. **Permit ↔ Document overlap.** Fold `Permit` into M6 docs flow, drop the WO M2M? Decide before M6 lands.
4. **AssetInventory financials.** Currently sparse fields (`original_value`, `current_value`, `depreciated_value`). Owner report might need them; if not, delete.
5. **M8 LCC + replace-vs-maintain advisor.** Full advisor or "EUR spent vs replacement" cell only? Decide after M7 ships.
6. **Sensor stack.** Keep in spec as future / customer-driven, or strip entirely? V1 ships `UtilityMeter` only.
7. **Audit immutability.** Postgres + SHA-256 export hash sufficient, or hash-chain ledger? Postgres + SHA V1.
8. **Notification channels.** Email + WebSocket V1; SMS / WhatsApp / Teams via webhook only.

---

## 16. Why this wins

Three reasons Castor will land this where others haven't:

1. **Matrix-driven, not feature-store-driven.** Every Layer 1 / 2 / 3 item the expert listed has a home in the spec. No invented features the FM doesn't actually use.
2. **Local-first, LLM-native.** Everything runs on-prem via Ollama. No cloud FM vendor matches the data-sovereignty story for regulated buildings (labs, defense, healthcare). LLM assist is baked in (occupant intake, intent-to-WO, manual-to-PM, narrative KPIs), not bolted on.
3. **IFC as long-term memory, not the daily interface.** Castor is fast because the DB is fast (matrix uses the DB exclusively for Layer 1 + 2). IFC is the durable, portable artifact when you need it (the export pipeline). Best of both: CAFM-speed UX + BIM-grade portability.
