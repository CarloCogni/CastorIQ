# Castor 7D — Facility Management Vision & Spec

## 1. Context

Castor already does the hard part: it binds IFC building models to technical documentation through a shared 1024d embedding space, lets users ask questions in natural language (Ask), and lets them propose risk-stratified modifications that round-trip into the IFC file via Git (Modify). That covers design handover well.

What Castor does **not** yet address is what the building does for the next fifty years of its life. The asset register, the work orders, the PM calendar, the warranty expiries, the permits about to lapse, the occupant who can't get their HVAC to turn on — this is 7D Facility Management, and it's where BIM finally pays back the modeling effort or quietly dies on a shared drive.

This spec scopes Castor's entry into 7D with one guiding principle: **the FM team should find Castor so useful that they refuse to go back to spreadsheets + Archibus + a Slack channel.** Everything else is secondary.

---

## 2. Three assumptions challenged before we commit

You gave an architectural direction ("edits live in Castor DB, IFC syncs on export"), a scope boundary ("geometry out of scope"), and an implicit one ("IFC is the source of truth"). All three deserve pressure-testing before the spec hardens around them.

### 2.1 "IFC is the source of truth" — only for durable metadata

This is correct for the physical model: geometry, spatial structure, element types, classifications, permanent psets. It is **wrong** for operational FM data. A work order closed in March 2027 does not belong in the IFC file. A temperature reading from yesterday does not belong there either. A tenant complaint has no business being embedded in a model that gets sent to a structural engineer.

**Recommended partition:**

| Data class | Lives in | Round-trip to IFC? |
|---|---|---|
| Physical model, spatial tree, element types | IFC file (authoritative) | Read at ingest; write at export |
| Asset inventory, warranties, service life, classifications | IFC + Castor DB (mirrored) | Yes — these are durable FM metadata that belong in the model |
| Documents (O&M manuals, certificates, permits) | Castor DB + object storage; `IfcDocumentReference` at export | Reference only; the file itself stays in Castor |
| Work orders, action requests, maintenance events, inspections | Castor DB (authoritative) | **Never.** At most, summaries as psets on the affected element at export (e.g., `LastMaintenanceDate`) |
| Sensor readings, energy meters, IoT streams | External TSDB (TimescaleDB on the same Postgres, recommended) | `IfcDocumentReference` pointer only |
| Occupancy, tenant roster, lease terms | Castor DB | Selective — tenant name and lease end on `IfcOccupant` at export |

This partition gives you a clean mental model: IFC = what the building **is**; Castor DB = what is **happening** to it.

### 2.2 Deferred IFC sync is the right call, with one protocol addition

Current writeback commits each proposal to IFC via Git. For Modify mode (surgical edits to the model), that's fine. For FM, it's a disaster: a facility manager closing forty work orders in an afternoon does not want forty Git commits — or worse, IFC file rewrites at a megabyte each.

**Approved with discipline:** FM writes go to Castor DB immediately. IFC file is reconciled on an explicit `Export IFC` action via a new **Export Reconciliation Service**. See §7 for the protocol — the key is that "export" is a deterministic replay of pending FM deltas against a fresh copy of the IFC, producing one Git commit per export with a bundled diff.

**The cost you're accepting:** Git history for FM becomes coarser. You lose per-proposal IFC diffs for FM operations. You pay this back with a **per-proposal audit trail in the Castor DB** (who did what, when, against which entity) that is richer than Git diffs ever were for FM purposes. This is fine.

**One trap to avoid:** do not let IFC and DB drift silently. Every unsynced FM delta carries a `dirty_since` timestamp. The project header shows "IFC last exported 12 days ago — 47 pending FM updates." The export button is never hidden; staleness is always visible.

### 2.3 "Geometry out of scope" is mostly right, but rename it

FM's real geometry pain isn't geometry — it's **spatial containment changes**. Moving a chair from Room 101 to Room 103 is not geometry; it's `IfcRelContainedInSpatialStructure` rewiring. Same for reassigning a tenant to a different floor, relocating a server rack, or splitting a meeting room assignment.

Castor's scope memo says geometry is out. Keep it. But rename the boundary to make clear that **spatial containment edits are in scope** (this is Tier 3 RED in the current RSAA, and it already works for one-offs). FM needs this to be fast and bulk-capable — Move Management is a first-class workflow (§6.11).

Net: "Castor does not edit geometry. Castor edits meaning, assignment, and relationships." Keep that phrasing.

---

## 3. The stakeholder model (IfcActor, first-class)

Per your direction, V1 models the full stakeholder chain. Users in Castor acquire one or more **roles** per project, each role carrying a distinct permission matrix.

| Role | Typical persona | Can do | Cannot do |
|---|---|---|---|
| `BUILDINGOWNER` | Asset owner, fund manager | See everything; approve capex; close fiscal periods; see costs | Edit physical model, dispatch work orders |
| `FACILITIESMANAGER` | FM lead | Everything in ops; approve work orders; manage vendors; schedule PMs | Close fiscal periods; approve capex > threshold |
| `MAINTENANCEENGINEER` | In-house technician / field team | Self-assign work orders; log completions; update condition; capture photos | Approve others' work; set budgets; modify model |
| `CONTRACTOR` / `SUBCONTRACTOR` | External vendor | View assigned WOs; log work; upload certificates; submit invoices | See other contractors' rates; edit model; see financials beyond own POs |
| `TENANT` / `OCCUPANT` | Building occupant | Submit IfcActionRequest; track own requests; see space they occupy; book shared resources | See the model; see other occupants' data; see costs |
| `AUDITOR` / `CONSULTANT` | Periodic reviewer | Read-only on everything in-scope; comment; export reports | Change any state |

Each user's active role is **project-scoped**, shown in a pill in the navbar, and drives the entire UI (different landing pages, different sidebar trees, different available actions). This is how Castor avoids becoming "a dashboard with 200 buttons" — every user sees the 6 buttons they actually use.

Implementation seam: extend the existing Django auth with a `ProjectRole(user, project, role, valid_from, valid_until)` model, mapped to `IfcActor` + `IfcActorRole` at export.

---

## 4. The "day in the life" test

Before cataloguing features, here is the concrete scenario that V1 must make pleasant. If any bullet requires leaving Castor, V1 fails.

> **07:45** — FM lead opens Castor on her phone during her commute. Landing dashboard: 3 work orders overdue, 2 permits expiring this week, chiller-2 condition score dropped overnight, one occupant request from last night. She swipes to triage.
>
> **08:30** — Monday walk-through. She opens the floor plan on her tablet, taps Storey-02, sees 4 assets flagged red. She walks to AHU-04, scans its QR code, the asset card opens, she sees last service was 11 months ago, the warranty, the manual, two past tickets. She taps the mic: *"Log inspection: exterior fine, slight vibration, next PM in one month."* Castor drafts a condition log, she confirms.
>
> **10:00** — Tenant submits via occupant portal: *"Meeting room 3-B is cold."* The intake LLM classifies it (HVAC, low priority), routes it to the HVAC zone owner, who sees it in their kanban. They assign it to the on-duty engineer.
>
> **14:00** — Auditor needs a 12-month maintenance history for the elevator. FM types: *"Give me the compliance log for ELV-01 for 2026."* Ask mode returns a time-stamped report with permit numbers, contractor names, and uptime. Export to PDF, email to auditor.
>
> **16:30** — End of day. FM clicks **Export IFC**. Castor replays 34 pending FM deltas against the IFC, produces one Git commit: *"FM reconciliation 2026-04-17: 12 assets updated, 22 warranties indexed, 3 classification codes added."* Commit hash goes to the model server.

Every V1 feature below maps back to one of those beats.

---

## 5. Information architecture — the new FM tab

Castor's workspace today is a tab strip: `Ask | Modify | Conflicts | History | Explore | Schedule`. FM adds one tab — **Facilities** — but the tab is a **shell**, not a single page. The workspace behind it has its own second-level nav and is role-aware.

```
Project → Facilities
├── Dashboard         (role-specific landing)
├── Assets            (registry, cards, bulk ops)
├── Spaces            (occupancy, heatmaps, move mgmt)
├── Work              (orders, requests, kanban, calendar)
├── Maintenance       (PM plans, calendars, compliance)
├── Systems           (HVAC, electrical, plumbing — zone/system hierarchies)
├── Sensors           (IfcSensor inventory, live readings, alarms)
├── Documents         (O&M, warranties, permits, certificates)
├── People            (actors, organizations, occupants, vendors)
├── Costs             (budgets, cost schedules, lifecycle)
└── Reports           (KPIs, exports, audit packs)
```

Ask and Modify remain the primary interaction channels — FM users ask questions about FM data ("which ACs haven't been serviced in 12 months?") and FM ops happen inside Modify or inline inside the Facilities tab (the Facilities tab embeds the same proposal/approval pattern when needed, so there is one mental model for "changing things").

**Sidebar changes:** add a Facilities section to the project sidebar, with quick-access counts (open WOs, overdue PMs, expiring warranties). Badge dots match existing status patterns.

**Color-mode:** Facilities gets its own mode color — recommend **teal `#14b8a6`** — distinguishing it from Ask (blue) and Modify (purple). Same tier/severity colors as today elsewhere.

---

## 6. Core functionalities (the features that make this indispensable)

Each subsection describes the user-facing capability first, then briefly notes the IFC binding, then the killer "wow" feature that's hard to find elsewhere.

### 6.1 The Asset Register

**What it is:** The single pane of glass for every physical thing with a serial number or service obligation. Cards, not rows.

Each asset card shows — in one scroll — identification (tag, barcode, GUID), location breadcrumb (Site › Building › Storey › Space), responsible party, manufacturer/model/year, commissioning date, expected service life countdown, current condition score, warranty status with traffic light, last 3 work orders, next 3 scheduled PMs, attached documents, and a thumbnail of recent inspection photos.

**Bulk ops:** multi-select → set classification, add pset, reassign responsible party, bulk import from CSV or from an IFC re-ingest.

**IFC binding:** `IfcAsset` (group) + member `IfcElement` instances; `Pset_AssetInventory`, `Pset_ManufacturerOccurrence`, `Pset_ManufacturerTypeInformation`, `Pset_Warranty`, `Pset_ServiceLife`, `Pset_ConditionCriteria`.

**Wow feature — Asset-from-photo:** tech in the field opens the camera, frames the nameplate of a chiller, Castor's vision layer reads manufacturer, model, serial, extracts from the manual database, auto-creates the asset record with warranty and service life pre-filled. Field creation in <30 seconds.

### 6.2 Work Order Lifecycle (the CMMS core)

**What it is:** From the moment someone notices something is wrong to the moment it is signed off, Castor is the only tool involved.

Stages: `Draft → Submitted → Triaged → Assigned → Scheduled → In Progress → Completed → Verified → Closed`. Each stage has an owning role; each transition emits an event; each event is a WebSocket push to every connected client.

Views: **Kanban** (drag cards across stages), **Calendar** (by scheduled date), **Map** (overlay on floor plan), **List** (filterable, exportable).

Every WO carries: the affected entity/asset, priority (1–4), SLA clock (paused during waiting states), labor log, parts used, cost actual vs estimate, photos before/during/after, digital sign-off (tech, supervisor, requester).

**IFC binding:** `IfcProjectOrder` + `Pset_ProjectOrderMaintenanceWorkOrder` (persisted in DB, summary data replayed into IFC at export).

**Wow feature — Intent-to-WO in one sentence:** FM types *"Schedule filter replacement on all rooftop units next Tuesday morning, assign to ACME HVAC, priority 2."* Castor generates 8 WOs, prefills, opens a batch-review modal. Confirm. Done. This is pure Tier-2 RSAA applied to FM ops — the exact shape Castor already ships for model edits.

### 6.3 Preventive Maintenance Planner

**What it is:** A calendar-driven recurring-task engine where each PM plan generates `IfcTask` instances against `IfcWorkCalendar`-aware schedules, respecting tenant quiet hours, weather constraints, and staff availability.

Plans are defined declaratively: *every 6 months on all `IfcUnitaryEquipment` of type `CHILLER`, excluding the period Jun–Aug, requiring certified HVAC technician, 4h duration.*

Calendar conflict detection is first-class. If an FM tries to schedule a shutdown of the fire pump during occupied hours, Castor refuses with a clear reason referencing `IfcWorkCalendar.ExceptionTimes`.

**IFC binding:** `IfcWorkPlan` → `IfcWorkSchedule` → `IfcTask` + `IfcTaskTime` + `IfcWorkCalendar`.

**Wow feature — PM plans proposed from manuals:** upload the manufacturer's manual for a chiller. The RAG pipeline already covers this document. Castor reads the maintenance schedule section, drafts a PM plan with recommended intervals, presents it as a proposal, FM approves with one click. Applies to any asset type with an attached O&M manual. This is where Ask mode directly generates FM ops.

### 6.4 Action Requests & Occupant Portal

**What it is:** A dramatically scoped-down UI for occupants (`TENANT` role). They see: their space, their recent requests, a single text box to submit a new one.

The text box is **not a form** — it's Ask mode with a narrow system prompt. The LLM extracts category, location (pre-filled from occupant's assigned space), severity, and drafts the `IfcActionRequest`. Occupant confirms. Submission is instant.

Status updates push via WebSocket to a mobile-friendly page. Push notifications optional.

**IFC binding:** `IfcActionRequest` + `Pset_ActionRequest`; escalation to `IfcProjectOrder` via `IfcRelSequence`.

**Wow feature — Photo + voice request:** *"Recording a quick video of the stain on my ceiling. It's been getting worse since the storm last week."* Vision + transcription → structured request with photo evidence. Occupant never filled out a form.

### 6.5 Inspection Workflows

**What it is:** Checklist-driven walk-throughs that produce both `IfcPerformanceHistory` records and condition scores. Templates per asset category (fire-system monthly, HVAC quarterly, elevator annual, etc.).

On-site UX: large tap targets, photo capture per step, offline queue (sync when back online — phase 2), voice-to-text on the notes field, auto-advance through checklist.

Outcomes: condition deltas applied to assets, automatic WO generation for any failed item, compliance record persisted.

**IFC binding:** `IfcPerformanceHistory` linked via `IfcRelAssignsToControl`. Time-series values stored in the TSDB with an `IfcDocumentReference` from IFC.

**Wow feature — Regulatory template library:** Castor ships with pre-built inspection templates mapped to major jurisdictions (NFPA, IBC, CE, UK BSA). Picking a template is a single click; the audit pack it produces is court-admissible formatted.

### 6.6 Space & Occupancy Management

**What it is:** Who is using which room, how much of the time, at what rent, under which lease. Heatmaps from booking/badge data (if available via integration).

Each `IfcSpace` gets an occupancy card: assigned occupant (IfcOccupant relation), capacity, current utilization %, lease metadata (tenant, start/end, renewal clock), function code (office/meeting/restroom/etc.), sq m, and per-sq-m cost allocation.

**Stack plans:** floor plans colored by occupancy, function, vacancy, or cost density. Click-through from map to space detail.

**IFC binding:** `IfcOccupant` + `IfcRelOccupiesSpaces`; occupancy classification via `Pset_SpaceOccupancyRequirements`.

**Wow feature — Consolidation insights:** *"Your 3rd floor is 42% utilized. The 4th floor is 38%. Consolidating freeing the 4th floor would save €180k/year in HVAC and cleaning. Here is a proposed move plan for 23 occupants."* Castor drafts a Move Order as a reviewable proposal.

### 6.7 Vendor & Contractor Management

**What it is:** The directory of every external party that touches the building, with contracts, rates, certifications, insurance validity, and performance history.

Each vendor record: `IfcOrganization` with roles, contacts (`IfcPerson`), framework contract, service areas (which zones/systems they cover), hourly/call-out rates, SLA commitments, expiry of insurance/certification documents (traffic light), past WOs assigned (completion rate, average time, cost variance).

**IFC binding:** `IfcActor` + `IfcOrganization` + `IfcRelAssignsToActor`.

**Wow feature — Auto vendor selection:** for a new WO, Castor ranks eligible vendors by past performance on similar assets, current load, distance, and rate. FM approves or overrides. The ranking is explainable ("ACME HVAC: 94% on-time, €85/h, covers Zone A, certifications current").

### 6.8 Document Hub (O&M, warranties, permits, certificates)

**What it is:** Every PDF that matters, indexed, linked to the thing it describes, searchable by Ask, flagged before expiry.

Classification at ingest: LLM tags each document as `manual | warranty | permit | certificate | drawing | spec | contract | invoice` and proposes links to assets/systems/spaces/organizations. User confirms.

Every permit and certificate has an expiry date. Dashboard surfaces anything expiring in the next 90 days. Email/notification hooks configurable.

**IFC binding:** `IfcDocumentReference` + `IfcDocumentInformation` + `IfcRelAssociatesDocument`. File itself stays in Castor object storage; IFC carries only the reference.

**Wow feature — Warranty-aware WO creation:** when creating a WO on an asset with active warranty, Castor warns: *"This asset is under warranty from ACME until 2028-03-15. Warranty coverage includes 'compressor replacement'. Billing this to in-house labor may be non-recoverable."* Then offers to draft the warranty claim letter instead.

### 6.9 Systems & Zones (functional hierarchies)

**What it is:** The building decomposed by function, not spatial structure. "Show me everything in the HVAC system," "which assets serve Zone 3?", "what is the electrical load on Panel EP-04?"

Tree view per `IfcDistributionSystem` (air, water, power, data, fire, etc.) with drill-down to member elements. Separate tree for `IfcZone` (functional space groupings — typically HVAC zones, security zones, lighting zones).

**IFC binding:** `IfcSystem`, `IfcBuildingSystem`, `IfcDistributionSystem`, `IfcZone`, `IfcRelAssignsToGroup`, `IfcRelServesProduct`.

**Wow feature — Impact analysis:** *"What will be affected if I shut down Chiller-2 for 4 hours?"* Castor traces the distribution system, identifies downstream dependents, lists all spaces served, flags any spaces containing sensitive assets (servers, labs), proposes a shutdown window minimizing occupant disruption.

### 6.10 Sensors, Alarms & Live Data

**What it is:** A sensor inventory (IfcSensor entities with thresholds, calibration dates, linked performance history) and a live readings dashboard. Readings stream from BMS / IoT gateways into the external TSDB (TimescaleDB recommended — lives in the same Postgres, no new infrastructure).

Features: real-time gauge widgets on space/asset cards, threshold configuration, alarm rules (threshold crossed for N minutes → raise `IfcAlarm` event → auto-create WO), anomaly detection (per-sensor baselines, drift alerts).

**IFC binding:** `IfcSensor`, `IfcActuator`, `IfcController`, `IfcAlarm`, `IfcFlowInstrument` as entities; `IfcPerformanceHistory` linked via `IfcRelAssignsToControl`; readings via `IfcRegularTimeSeries` **referenced** from IFC (the readings themselves stay in the TSDB).

**Integration shape:** generic ingestion endpoint (HTTP POST, MQTT bridge as phase 2) accepting `{sensor_guid, timestamp, value}`. Castor does not want to become a BMS — it wants to be a smart layer over one.

**Wow feature — Condition-based PM trigger:** PM plan defined as *"perform filter replacement when pressure-drop sensor SD-104 > 250 Pa for 48 continuous hours OR every 6 months, whichever first."* Castor monitors, triggers WO automatically, tech gets notified. This is the promise of BIM-to-operations nobody ever delivers — Castor can.

### 6.11 Move Management

**What it is:** Bulk spatial reassignment of furniture and occupants. No geometry change — only `IfcRelContainedInSpatialStructure` and `IfcRelOccupiesSpaces` edits.

User flow: select source space → select destination space → pick items (furniture, occupants) → schedule move date → generate move order with packing list, instructions, and space inventory diffs.

**IFC binding:** `IfcProjectOrder.PredefinedType = MOVEORDER`; actual moves encoded as `IfcRelContainedInSpatialStructure` rewiring.

**Wow feature — Drag-and-drop on the floor plan:** drag a chair icon from Room A to Room B on the map view, Castor drafts the move order with everything else pre-filled.

### 6.12 Risk & Safety

**What it is:** Hazard register, near-miss reporting, risk assessments per process/asset. `Pset_Risk` on elements; `IfcMetric` thresholds trigger escalation.

Each risk entry: type, severity, likelihood, affected property, mitigation plan, responsible actor, review date. Dashboard widget: top open risks by score.

**IFC binding:** `IfcRisk` (via `Pset_Risk` on `IfcProcess` or `IfcElement`).

**Wow feature — Auto-risk from inspection findings:** an inspection flags a fire door stuck open. Castor proposes a risk entry (life safety, high severity), links to the affected element, drafts mitigation WOs, notifies the fire officer role — in one action.

### 6.13 Cost Tracking & Lifecycle Financials

**What it is:** OpEx running total per asset, cost per sq m per year, remaining service life vs replacement cost, capex budget vs actuals.

Views: budget dashboard, cost-per-asset leaderboard, lifecycle curve per asset (cumulative maintenance cost vs replacement cost — tells you when to retire), cost allocation by tenant / zone / department.

**IFC binding:** `IfcCostSchedule` → `IfcCostItem` tree; costs attached via `IfcRelAssignsToControl`.

**Wow feature — Replace-vs-maintain recommendation:** Castor flags *"You've spent €14,200 on AHU-07 in 24 months. Replacement cost is €22,000 with 15-year service life. Projected 5-year maintenance at current rate: €35,000. Recommend replacement."* Reviewed and approved by the owner role triggers a `CAPEX` WO and a budget allocation.

### 6.14 Compliance & Audit Calendar

**What it is:** Every permit, certificate, inspection obligation, and regulatory deadline in one calendar. Green if valid, amber in 90 days, red in 30, black if overdue.

Each entry knows the authority (`IfcOrganization.Role = AUTHORITY`), the instrument (`IfcPermit` + `Pset_Permit`), and what it blocks (work that requires it).

**Wow feature — Audit pack one-click:** auditor asks for fire-safety compliance records for 2026. FM clicks *Generate Audit Pack*. Castor bundles every relevant inspection, permit, maintenance record, and supporting document into one versioned, timestamped archive with a SHA-256 hash. Immutable.

### 6.15 Reports, Dashboards & KPIs

**What it is:** Role-specific landing dashboards; configurable KPI widgets; scheduled report emails.

Core KPIs out of the box: PM compliance %, MTBF/MTTR per asset class, work-order backlog, SLA hit rate, cost per sq m, occupancy %, energy use index (when sensors are present), warranty leakage (work billed to in-house that could have been warranty).

**Wow feature — Narrative insights on the dashboard:** widgets don't just show numbers — they show a sentence. *"PM compliance dropped 12% this month, driven primarily by HVAC (8 overdue out of 14). Root cause: ACME HVAC contractor unavailable Apr 8–14. Recommend extending window or adding secondary vendor."* Generated by the LLM from the underlying data.

---

## 7. Deferred sync — the Export Reconciliation Protocol

The central architectural commitment. Every FM write goes to Castor DB first. IFC is reconciled explicitly.

### 7.1 The pending-delta queue

Each FM op produces an `FMDelta` row: `(project, entity_guid, operation, payload, created_at, created_by, applied_to_ifc_at, ifc_commit_hash)`. While `applied_to_ifc_at` is null, the delta is pending.

Project header banner renders `⚠ 47 FM updates not in IFC — last export 12 days ago` whenever pending count > 0. Staleness is never hidden.

### 7.2 Export flow

On `Export IFC`:

1. **Plan phase.** Castor lists all pending deltas, groups by target entity, resolves conflicts (e.g., two warranty updates on the same asset — last-write-wins, user confirms), shows a preview: *34 entities affected, 47 deltas, 12 psets created, 22 psets updated, 3 classifications added, 0 conflicts.*
2. **Snapshot.** Git snapshot of current IFC.
3. **Replay.** Each delta applied via the existing Tier 1/2/3 writers. FM operations mostly slot into existing tiers: `SET_PROPERTY`, `ADD_PSET`, `SET_CLASSIFICATION`, `SET_ATTRIBUTE`, plus new FM-certified ops: `CREATE_IFCASSET_GROUP`, `CREATE_IFCPROJECTORDER`, `CREATE_IFCPERFORMANCEHISTORY`, `WIRE_IFCDOCUMENTREFERENCE`, `SET_IFCOCCUPANT_ASSIGNMENT`.
4. **Validate.** IFC is reopened and checked for structural integrity (same validation pipeline as Tier 2).
5. **Commit.** One Git commit per export with semantic message: *"FM reconciliation 2026-04-17: 34 entities, 47 deltas, by Maria Santos"*. Diff data JSON lists each delta.
6. **Mark applied.** All deltas stamped with `ifc_commit_hash` and `applied_to_ifc_at`.
7. **Rollback.** If any step fails, Git rollback + all deltas remain pending. User sees the failure reason.

### 7.3 Which FM ops do NOT go to IFC

Operational data (work orders mid-lifecycle, sensor readings, occupant requests, inspection in-progress state) never leaves Castor DB. Only **settled, durable** FM data reaches the IFC export:

- Closed-and-verified WOs → summarized as `LastMaintenanceDate`, `LastMaintenanceBy` on the affected element
- Completed inspections → condition score as `Pset_ConditionCriteria.ConditionCriterionAssessment`
- Ratified warranty metadata → `Pset_Warranty`
- Confirmed occupancy assignments → `IfcRelOccupiesSpaces`
- Approved documents → `IfcRelAssociatesDocument` + reference
- Classified assets → `IfcRelAssociatesClassification`

This partition is a configurable "export profile." Default profile is opinionated; owners can tune it.

### 7.4 Round-trip safety

Re-ingesting an IFC that Castor previously exported must not duplicate FM data. At re-ingest, Castor matches entities by GUID, detects psets it wrote on previous export, and treats them as idempotent.

---

## 8. Natural-language surfaces — how FM uses Ask and Modify

Castor's differentiator is that FM ops should be **conversational** where it saves time and **structured** where it prevents errors. The split:

| Interaction | Modality | Example |
|---|---|---|
| Information retrieval | Ask (chat) | *"Which chillers haven't been serviced in 12 months?"* |
| Explanation of state | Ask (chat) | *"Why is this space flagged amber?"* → because lease expires in 45 days, occupant has open request, energy use +18% YoY |
| Single-entity simple edit | Inline on the card | Click warranty expiry → date picker |
| Multi-entity bulk edit | Modify (proposal flow) | *"Add Uniclass EF_25_10 classification to all rooftop AHUs"* |
| Workflow operation (create WO, schedule PM) | Ask-to-form + confirm | Natural language in, structured preview out, user confirms |
| Document reasoning | Ask (chat) | *"Summarize the O&M requirements for AHU-04 from its manual"* |
| Audit / compliance query | Ask with report export | *"12-month compliance log for ELV-01"* → PDF |

**New RAG routing:** add FM intent classifier — queries about work orders, assets, warranties, schedules route to FM-aware retrieval that joins the SQL tables with vector search. Example: "chillers not serviced in 12 months" = SQL aggregation on MaintenanceEvent JOIN Asset, not vector search.

---

## 9. Data model — the new seams (brief, since you asked me to focus elsewhere)

Grouped by responsibility, placed in new Django apps to keep migrations manageable. Each model has UUIDModel base, per-project scoping, and uses the existing audit-trail pattern.

- **`actors`**: `Actor`, `Organization`, `Person`, `ProjectRole` (replaces / extends auth).
- **`assets`**: `FacilityAsset` (FK → IFCEntity), `AssetInventory`, `Classification`, `ClassificationReference`.
- **`work`**: `WorkOrder`, `ActionRequest`, `Permit`, `WorkOrderStatusEvent`, `WorkOrderAttachment`.
- **`maintenance`**: `MaintenancePlan`, `MaintenanceTaskInstance`, `InspectionTemplate`, `InspectionRun`, `ConditionLog`, `WorkCalendar`.
- **`systems`**: `System`, `Zone`, `SystemMembership`, `SystemServesSpace`.
- **`sensors`**: `Sensor`, `Actuator`, `Controller`, `Alarm`, `AlarmRule`, `SensorReading` (TimescaleDB hypertable).
- **`docs`** (extends existing): `DocumentAssociation`, `DocumentExpiry`, doc-type classifier.
- **`costs`**: `CostSchedule`, `CostItem`, `LifecycleCostLedger`.
- **`exports`**: `FMDelta`, `ExportJob`, `ExportProfile`.

Every FM model has a matching **IFC export mapper** registered in the reconciliation service — the mapping is declarative, not ad-hoc.

---

## 10. Frontend surfaces — specific screens to design

In priority order (phase 1 includes items 1–5):

1. **Facilities tab shell** + second-level nav; role-aware landing dashboard widgets (4–6 default widgets per role).
2. **Asset detail card** (hero view — the most-used screen in the system).
3. **Work Order kanban** + calendar + detail modal.
4. **Occupant portal page** (mobile-first, extremely narrow).
5. **Export IFC modal** with delta preview and conflict resolution.
6. Inspection runner (mobile-first, tap-heavy).
7. PM plan editor.
8. Space/occupancy stack plan (floor-plan overlay).
9. Sensor dashboard + alarm rules editor.
10. Document hub with expiry view.
11. Vendor directory + performance.
12. Cost dashboard + lifecycle curves.
13. Compliance calendar + audit pack generator.
14. Move management with floor-plan drag-drop.

Reuse: all lean on existing Castor patterns (HTMX partials, WebSocket streaming for live state, proposal/approval flow for edits, Bootstrap card grid, the spatial tree + right-detail pattern from `_explore.html`). No new frontend framework.

---

## 11. AI / LLM integration opportunities (the creative layer)

These are what differentiate Castor from any CMMS on the market.

1. **Voice field inspection** — whisper → structured condition log.
2. **Nameplate OCR** — phone camera → asset auto-created.
3. **Manual-to-PM-plan** — LLM reads O&M manual, drafts PM schedule. ### this one as well
4. **Tenant-intake classifier** — natural-language occupant request → structured action request.
5. **Warranty-aware WO guard** — detects warranty coverage, drafts claim letter.
6. **Narrative KPI insights** — dashboard widgets speak in sentences, not numbers. ### i like this one, let's implement it
7. **Vendor ranking with explanations** — ranked list + reason for each rank.
8. **Consolidation advisor** — underused spaces → proposed move plan with savings.
9. **Replace-vs-maintain** — cost curve → recommendation. ### i like this one, let's implement it!
10. **Impact analysis** — "what breaks if I shut down X" — traces system graph.
11. **Audit pack narrator** — generates the auditor-facing summary from raw records.
12. **Anomaly explanations** — sensor drift → human-readable root-cause hypothesis with references to past WOs on the same asset.
13. **Inspection photo analyst** — vision model spots obvious defects (corrosion, leaks, cracks), drafts finding.
14. **Compliance-regime bootstrap** — given a jurisdiction + building type, LLM proposes the inspection template library.
15. **Email-to-WO** — forward a tenant complaint email to `wo@your-project.castor` → parsed, request created.

None of these is blocking for V1 core, but each one removes 20–60 minutes of drudgery per week from an FM's schedule. Pick the top 5 for phase 1 and ship the rest in waves.

I would say for the first implementation we could do: 3, 6, 9. we might add more in the future :D

---

## 12. Phasing — what to build when

### Phase 1 (V1 — 8–12 weeks, MVP facility manager loves)

- FM tab shell + role model + role-aware dashboard.
- Asset Register (cards, bulk edit, CSV import, no bulk creation from photos yet).
- Work Order lifecycle (kanban + calendar + detail), IfcProjectOrder binding.
- Occupant portal (mobile-first intake page).
- PM Planner (simple calendar-driven, no condition-based triggers yet).
- Documents v2 (existing RAG + classification + expiry tracking).
- Export IFC reconciliation v1 (Tier 1 + 2 ops, no sensor references yet).
- AI surfaces: intent-to-WO (#6.2 wow), PM from manuals (#6.3 wow), narrative KPIs (#6.15 wow).

### Phase 2 (+6 weeks)

- Inspections (templates, runner, condition scoring, IfcPerformanceHistory binding).
- Systems & Zones (distribution system explorer, impact analysis).
- Vendor directory + auto-ranking.
- Compliance calendar + audit pack generator.
- Move management (no floor-plan drag-drop yet).

### Phase 3 (+8 weeks)

- Sensors + TSDB integration + alarm rules + condition-based PM triggers.
- Cost dashboards + lifecycle curves + replace-vs-maintain advisor.
- Space stack plans on floor plans.
- Move management with drag-drop.
- Vision-model inspection photo analysis.
- Mobile offline mode for inspections.

---

## 13. Integrations that matter (in priority order)

1. **BMS / IoT gateway** (MQTT or HTTP POST endpoint) — makes §6.10 real.
2. **Email** (SMTP in, notifications out) — occupant intake, WO alerts, expiry alerts.
3. **Calendar** (Outlook/Google, iCal export) — PM and inspection schedules on tech calendars.
4. **Accounting** (CSV export for now; QuickBooks/Xero webhook phase 3) — PO and invoice flow.
5. **Badge / access control** (read-only CSV ingest) — real occupancy data for §6.6.
6. **Weather API** — feeds the PM planner's seasonal constraints.
7. **eSignature** (DocuSign webhook) — sign-off flows for high-value permits.

---

## 14. Verification plan (how we know V1 works)

Scenario-driven acceptance tests, each is a "day-in-the-life" slice exercised end-to-end against a seeded project:

- **Scenario A — FM Monday triage:** load project → dashboard shows counts → click overdue WO → triage to in-progress → assign → WebSocket push verified on second client.
- **Scenario B — Occupant request:** log in as tenant → submit via portal with natural language → verify action request created, routed, FM sees it.
- **Scenario C — PM plan from manual:** upload manual → wait for RAG ingestion → trigger "propose PM plan" → verify generated plan matches expected schedule → approve → tasks appear on calendar.
- **Scenario D — Export reconciliation:** accumulate 10 FM ops → hit Export → preview shown → confirm → verify IFC written with expected psets, one Git commit, all deltas marked applied.
- **Scenario E — Ask + FM join:** ask *"Which chillers are overdue for maintenance?"* → RAG routes to SQL-aware retrieval → correct list returned with sources.
- **Scenario F — Role enforcement:** log in as contractor → verify cannot see cost data or other contractors' WOs.

Unit and integration coverage targets: 80% lines on new services, 90% on export reconciliation (it's the scariest code), parametrized tests for each FM-certified operation against a golden IFC fixture.

---

## 15. Open questions (flag before build)

1. **Owning app vs new apps.** A `facilities/` monolithic app or split as in §9? Split is cleaner but adds cross-app imports. Recommendation: split, because `actors/` and `exports/` have reuse beyond FM.
2. **TSDB choice.** TimescaleDB extension on the existing Postgres, or separate InfluxDB? Recommendation: TimescaleDB. One database, same backup story, SQL-native joins with FM tables for alarm rules. No new infra.
3. **Mobile strategy.** Responsive web only for V1, PWA in phase 2, native app not planned. Acceptable? The occupant portal and inspection runner are the only pages that really need mobile-first; rest is tablet-acceptable.
4. **Permissions granularity.** Fixed role matrix (§3) or configurable per-project permission sets? V1 ships fixed. Configurable = phase 3 (rare requirement).
5. **Audit immutability.** Do we need append-only ledger semantics (blockchain-like hash chain of the audit log), or is Postgres + export pack + SHA hash sufficient for regulatory contexts? V1 ships the latter. Heavier requirement is phase 3 if pulled by customer demand.
6. **Multi-tenancy isolation.** Currently per-project. Occupant portal raises the stakes — if a tenant finds a link, must not see another building. Verify Django's existing project-scoping before V1 ships the portal.
7. **Notification channels.** Email in V1 is non-negotiable. SMS, WhatsApp, Teams? Each adds a provider dependency. Recommend email + a single webhook for everything else.

---

## 16. Why this wins

Three reasons Castor will land this where others haven't:

1. **One mental model for change.** Ask asks questions. Modify changes things. FM is just Modify applied to operational data, with the same proposal/approval/audit semantics. Users don't learn a new paradigm — they learn that the same paradigm covers more ground.
2. **Local-first, LLM-native.** Everything runs on-prem via Ollama. No cloud FM vendor can match the data-sovereignty story for regulated buildings (labs, defense, healthcare). And every workflow has LLM assist baked in, not bolted on.
3. **IFC as the long-term memory, not the daily interface.** Castor is fast because the DB is fast. IFC is the durable, portable artifact when you need it. Best of both: CAFM-speed UX + BIM-grade portability.
