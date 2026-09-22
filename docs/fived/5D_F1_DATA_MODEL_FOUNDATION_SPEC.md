# 5D-F1 — Castor 5D Data Model Foundation (Locked Spec)

**Status:** Locked for implementation planning  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `534198afceae54dfe90cc8a858f8081ad43aa169`  
**Document type:** Product / architecture contract (not an inventory of runtime files)  
**This document does not authorize:** product code, models, migrations, UI, cost/BOQ/EVM, commit, or push by itself.

**Founder push policy:** Keep local. Do not push until the full 5D Data Model workstream is ready and the founder explicitly approves.

**Planning source:** Castor 5D Data Model Foundation — Planning Report (discovery).

**Contract label for this foundation:** `fived-data-model-f1-v1` (spec only; runtime contracts come later).

---

## 1. Locked product definition

### What Castor 5D Data Model Foundation is

A **versioned, project-scoped preparation layer** that:

- is based on **Quantities Preparation** rows;
- holds **classification / package / work package** linkage slots;
- may later optionally align to **schedule identity** (Task / WBS / bindings);
- is designed to later support **BOQ / cost-code / rate** workflows **after** explicit later stages.

It is a structured preparation substrate — not a commercial deliverable.

### What it is not

- BOQ generation  
- QS-certified takeoff  
- Cost estimate  
- Budget approval  
- Procurement package  
- Payment valuation  
- EVM  
- 5D readiness claim  
- Automatic classification approval  
- IFC writeback  
- Modify proposal  
- Ask / RAG answer  

---

## 2. Current foundation state

| Item | Status |
|------|--------|
| **Stage 0** — Quantity Preparation Builder + `qty-prep-export-v1` | **Complete** |
| **Classification C1** — Registry Foundation | **Complete** (`534198a`) |
| C1 models | `ClassificationSchema`, `ClassificationNode`, `ProjectClassificationSchema` only |
| C1 assignments / mappings / rules | **None** |
| Quantities mapping values | **Session / manual only** (not DB taxonomy authority) |
| Local branch vs origin | **Ahead 3** (kept local) |
| Ahead commits | `8ec6d89` legacy QTO label · `1a7089e` prep export · `534198a` classification C1 |

---

## 3. Non-reuse decisions

Explicitly **do not**:

- Build the new 5D foundation on **`QTOCache`**.
- Use **`QTOExportView`** / legacy `/export/` as 5D export.
- Use **EVM engines** (`compute_evm`, related Controls/EVM services) as commercial 5D.
- Treat **`Task.cost`** as quantity × rate truth.
- Use **FM `AssetInventory`** (or other FM money fields) as construction 5D.
- Treat free-text demo codes (`CLASS-DEMO-*`, `PKG-DEMO-*`, `WP-DEMO-*`, etc.) as **authority**.
- Change **`qty-prep-export-v1`** in F1 or F2.

Safe reuse (consume, don’t colonize): Quantities prep runtime/export **semantics** and boundary language; C1 registry + reserved `boq_cost_later`; schedule identity as **alignment targets** later.

---

## 4. Boundary stages

| Stage | Content | Status |
|-------|---------|--------|
| **0** | Quantity preparation only | **Done** |
| **1** | 5D data model rows + classification/package/work linkage slots + versioning | **Next** (F2+) |
| **2** | BOQ / cost-code mapping, **no rates** | Later |
| **3** | Rate source attachment, no certified estimate | Later |
| **4** | Cost calculation preview | Later |
| **5** | Reviewed estimate package | Later |
| **6** | EVM / payment integration | Much later |

F1 locks the definition. F2 begins Stage 1 persistence only — still no rates, BOQ generation, EVM, or QS certification.

---

## 5. Recommended first row grain (locked)

- **First MVP grain = Quantities preparation `row_key`.**  
  Shape today: `v1|{grain}|{ifc_class}|{type_name_or_-}|{quantity_basis_or_-}`.
- Store **`source_row_key`** plus denormalized **class / type / basis / quantity** (and related prep fields) for audit.
- A **version is an immutable snapshot**.
- A **new version** is required when inputs change (basis, schema, source intents, session overlays, rebuild) — do not silently mutate a published/active version’s rows.
- **Do not** start with IFC entity (`GlobalId`) grain.
- **Do not** start with schedule **Task** grain.
- **Do not** start with **BOQ line** grain.

Entity expansion and Task-level commercial loading are deferred.

---

## 6. Proposed future domain model (describe only — do not implement in F1)

Prefer a **new app** (likely `src/fived/`) for Stage 1+ persistence.

### 6.1 `FiveDDataModel`

**Purpose:** Project container for a named 5D preparation model.

| Concern | Guidance |
|---------|----------|
| Core fields | project FK, name, description, status (`draft` \| `active` \| `archived`), contract_version, created_by (nullable if compatible), timestamps |
| Include | Project scoping, lifecycle status |
| Exclude now | Rates, cost totals, certified / QS / 5D-ready flags, EVM fields |

### 6.2 `FiveDModelVersion`

**Purpose:** Immutable snapshot of prep inputs + rows.

| Concern | Guidance |
|---------|----------|
| Core fields | model FK; version_label; source (`qty_prep_session` \| `qty_prep_export` \| `rebuild`); settings_snapshot JSON; boundary JSON; status; hash / source reference; created_by/at |
| Versioning | **Append-only**; no silent overwrite of frozen versions |
| Exclude now | Approval workflow UI; cost calculation results |

### 6.3 `FiveDModelRow`

**Purpose:** One structural row inside a version (prep `row_key` grain).

| Concern | Guidance |
|---------|----------|
| Identity | version FK, `source_row_key` |
| Quantity evidence | ifc_class, type_name, quantity_basis, quantity_source, unit_basis, total_quantity, quantity provenance |
| Structure slots | classification / package / work package slots (nullable node refs later + free-text fallback + origin) |
| Schedule | optional task / WBS refs **later** |
| Process | completeness flags; session review snapshot; notes; status e.g. `draft` \| `incomplete` \| `structurally_ready` |
| **Forbidden now** | `unit_rate`, `extended_cost`, certified/approved **cost** state |

### 6.4 Deferred entities

- `BOQMapping` / cost-code row mapping  
- `RateSource` / rate books  
- `CostEstimate`  
- `FiveDExport` artifact registry  
- Entity-level expansion rows  
- Schedule cost loading  

---

## 7. Relationship to Classification

- **C1 registry alone is not enough** for authoritative 5D mapping.
- **C2 / C3 / C3.1** are needed later for schema-backed selections and persisted assignments.
- Free-text / manual **session** values may be **snapshotted as weak provenance only** (`origin` such as `manual_session`).
- **Package Mapping** must stay separate from **BOQ / cost-code** mapping.
- **`boq_cost_later`** is reserved / future only — not current BOQ readiness, not rates, not 5D claims.

---

## 8. Relationship to Quantities

- **F2** may consume the current Quantities **runtime / session** output (same semantic surface as the Builder).
- **F2** should preserve: settings snapshot, schema fields, source intents, session reviews, session manual mappings.
- **F2** must **not** alter `qty-prep-export-v1`.
- **F2** must **not** change Quantities UI except a later explicit “Create 5D snapshot” action **if separately approved**.
- **F2** must **not** use `QTOCache`.

---

## 9. Relationship to 4D

- Schedule links are **alignment targets only**.
- `TaskEntityBinding` / `Task` / `P6WBSNode` may be referenced later (F4+).
- **No EVM.**
- **No cost-loaded schedule.**
- **No earned value.**
- **No** use of `Task.cost` as estimate truth.

---

## 10. Future export / evidence strategy

Future 5D export is a **new** contract (e.g. `fived-export-v1`), separate from `qty-prep-export-v1`.

Suggested archive contents:

- `BOUNDARY.txt` — same family of boundaries as Quantities prep export (not BOQ, not QS-certified, not cost estimate, not 5D-ready, not writeback, not Modify proposal).
- `model_version.json` — version metadata, settings snapshot refs, schema adoption refs.
- `rows.csv` / `rows.json` — structural rows + provenance.
- Unresolved register.
- Classification / schema references.
- Schedule references if present.
- **No rates / cost** until an explicit later stage **and** contract bump.

---

## 11. Minimal slices

| Slice | Scope |
|-------|--------|
| **5D-F1** | Locked spec doc only (**this document**) |
| **5D-F2** | Persist `FiveDDataModel` + `FiveDModelVersion` + rows from current prep session |
| **5D-F3** | Completeness register for classification / package / work slots |
| **5D-F4** | 4D alignment view — no EVM |
| **5D-F5** | BOQ / cost-code reserved mapping — no rates |
| **5D-F6** | 5D evidence ZIP export |
| **5D-F7** | Rate source planning only |

Do not jump to rates, cost calculation, BOQ generation, or EVM.

---

## 12. Founder decisions locked for F1 / F2

| Decision | Lock |
|----------|------|
| Persistence | **Persistent versioned snapshot** recommended for F2 |
| First row grain | Prep **`row_key`** |
| App placement | **New app** recommended for 5D foundation |
| Package vs BOQ/cost | **Separate** — package/work ≠ BOQ/cost-code |
| vs Classification C3.1 | F2 **may proceed before** C3.1; free-text/manual session values = **weak provenance** |
| F2 commercial scope | **No** rates / cost / EVM / BOQ generation |
| Git | **Keep local**; no push until full 5D Data Model workstream ready + explicit founder approval |

---

## 13. 5D-F2 implementation checklist (later — not F1)

When founder authorizes F2 implementation:

- [ ] Create new app (likely `src/fived/`)
- [ ] Add models / admin / migrations / tests
- [ ] Snapshot service from current quantity prep runtime (not QTOCache)
- [ ] No `QTOCache` dependency
- [ ] No `qty-prep-export-v1` change
- [ ] No rates / cost fields or calculations
- [ ] No BOQ generation
- [ ] No EVM
- [ ] No Ask / Modify / writeback / ModificationProposal
- [ ] No C2 / C3 assignment implementation as part of F2
- [ ] No Quantities UI change unless separately approved
- [ ] Commit only when founder asks; push only when founder explicitly approves after workstream readiness

---

## 14. 5D-F2 tests checklist (later — not F1)

- [ ] Model / version creation  
- [ ] Append-only version behavior  
- [ ] Row uniqueness per version by `source_row_key`  
- [ ] Settings snapshot copied  
- [ ] Session review snapshot copied  
- [ ] Manual mapping snapshot copied with origin / `manual_session` (weak provenance)  
- [ ] No `unit_rate` / `extended_cost` fields  
- [ ] No `QTOCache` dependency  
- [ ] Quantities export regression (`qty-prep-export-v1`)  
- [ ] Classification C1 regression  
- [ ] No ModificationProposal / writeback side effects  

---

## 15. Recommendation

This locked **5D-F1** spec is ready. Next authorized step is **5D-F2 implementation planning / implementation** (new app + versioned snapshot from Quantities prep session), still within Stage 1 boundaries.

Until then: **no models, no migrations, no product UI, no commit, no push.**
