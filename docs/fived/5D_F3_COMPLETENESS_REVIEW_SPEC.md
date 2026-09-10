# 5D-F3 Completeness Review Spec (Locked)

**Status:** Locked for implementation planning  
**Slice:** 5D-F3 — Completeness Register / Version Review  
**Contract (review payload):** `fived-completeness-f3-v1`  
**Depends on:** 5D-F2 Versioned Snapshot Foundation (`fived-snapshot-f2-v1`)  
**Branch context:** `integration/df-foundation-main-sync` @ `b12211d` (keep local; no push until founder explicitly approves after full 5D Data Model workstream)

This document locks product boundaries and the service contract for F3.  
It does **not** implement code.

---

## 1. F3 product goal

5D-F3 is a **read-only Completeness Register / Version Review** for a frozen `FiveDModelVersion`.

It summarizes:

- structural / mapping / quantity-evidence gaps
- row status breakdown (persisted F2 structural status)
- missing quantity basis / source
- missing classification / package / work slots
- provenance strength
- `manual_session` weak provenance
- representative unresolved rows
- boundary non-claims

It does **not**:

- generate BOQ
- certify QS takeoff
- calculate cost
- attach rates
- calculate EVM
- claim 5D readiness
- approve anything
- write back to IFC
- create Modify proposals / `ModificationProposal` rows
- rebuild Quantities runtime
- mutate F2 snapshots

Allowed language: structural completeness, mapping completeness, quantity evidence completeness, snapshot review, unresolved preparation gaps, classification/package/work coverage.

Forbidden claim language: BOQ ready, 5D ready, cost ready, estimate ready, QS approved, certified, approved (commercial), earned value, payment/procurement readiness.

---

## 2. Scope

F3 implementation (when authorized) is:

| In scope | Out of scope |
|----------|----------------|
| Service-only computation | Public 5D UI |
| Focused unit tests | Quantities button / tab |
| Compute on demand from frozen rows | Formal export ZIP (→ F6 later) |
| JSON-safe dict return | DB writes / mutations |
| | Migrations / new models |
| | Admin helper (unless separately approved later) |
| | Rates / cost / BOQ / EVM / QS / writeback |
| | C2/C3 classification assignments |
| | Schedule / 4D alignment (→ F4) |
| | Version diff (plan only; not F3 impl) |

---

## 3. Service contract

**Future file:** `src/fived/services/completeness_service.py`  
**Future class:** `FiveDCompletenessService`  
**Future method:** `build_version_review(version: FiveDModelVersion) -> dict`

**Contract version:** `fived-completeness-f3-v1`

Return a JSON-safe dict containing:

| Key | Purpose |
|-----|---------|
| `contract_version` | `fived-completeness-f3-v1` |
| `version` | id, label, status, source, content_hash, created_at (as needed) |
| `data_model` | id, name |
| `project` | id, name |
| `boundary` | copy of version `boundary_snapshot` (plus F3 non-claims reinforcement as needed) |
| `settings_ref` | subset: at least `schema_includes`, `source_mappings` from `settings_snapshot` |
| `row_count` | integer |
| `status_counts` | counts of F2 row `status` values |
| `missing_counts` | open-gap counts by dimension |
| `provenance_counts` | weak / empty / not_mapped / deferred / other as applicable |
| `quantity_evidence_counts` | quantity evidence complete vs open gaps |
| `slot_coverage` | classification / package_mapping / work_package coverage |
| `issue_register` | capped sample of unresolved rows |
| `categories` | aggregate category counts / rates |
| `non_claims` | explicit non-claim block |
| `generated_at` | optional ISO-8601 timestamp |

No side effects. Pure read + aggregate.

---

## 4. Truth source

F3 reads **only**:

- `FiveDModelVersion`
- `FiveDModelRow` (via `version.rows`)

F3 must **not** call or depend on:

- `build_qty_prep_session_ui` / Quantities prep runtime rebuild
- `QTOCache`
- `QTOExportView`
- legacy Excel / QTO export
- Ask / RAG
- Modify / writeback
- EVM engines
- `Task.cost` / FM asset values

Row-level flags and values are authoritative.  
`unresolved_register_snapshot` is **optional diagnostic only** (prep register key names may differ, e.g. `missing_package_boq_mapping`); do not treat it as truth over row fields.

---

## 5. Persistence decision (locked)

- Compute **on demand only**
- **No** `FiveDCompletenessSnapshot` model
- **No** cached completeness fields on `FiveDModelVersion`
- **No** readiness / approval / certified fields
- **No** migrations for F3

Frozen rows already make recompute deterministic.

---

## 6. UI decision (locked)

- **No** public 5D UI in F3
- **No** Quantities UI changes in F3
- **No** “Create 5D Snapshot” button in F3
- **No** admin helper in F3 MVP unless separately approved
- F3 MVP = **service + tests only**

---

## 7. Completeness logic (locked)

### 7.1 Source of truth

Use **row-level fields** on `FiveDModelRow`.

### 7.2 Schema includes gating

Respect `settings_snapshot.schema_includes` for mapping slots:

- classification → typically `classification_code` include key (as stored in settings)
- package → `package_boq_mapping` include key in Quantities settings maps to row `package_mapping` fields
- work package → `work_package`

Only **included** slots participate in mapping open-gap and `mapping_slots_complete` logic.  
Excluded slots are not counted as missing.

### 7.3 Quantity evidence — open gap if

- `basis_unresolved` is true, **or**
- `missing_quantity_source` is true, **or**
- `quantity_basis` is blank

### 7.4 Included mapping slot — open gap if

For each included slot:

- corresponding `missing_*` flag is true, **or**
- value is blank when include=true

Slots / fields:

| Slot | Value field | Missing flag |
|------|-------------|--------------|
| Classification | `classification_code` | `missing_classification` |
| Package | `package_mapping` | `missing_package_mapping` |
| Work package | `work_package` | `missing_work_package` |

### 7.5 Filled but weak

A slot is **filled but weak** when:

- value is non-blank, **and**
- origin and/or `*_source_intent` indicates `manual_session` / `manual_field`

Weak ≠ open gap. Weak ≠ strong/schema-backed assignment.

### 7.6 Session review

- Informational only
- Blank `session_review_status` is **not** a structural gap by default
- Count separately as reviewed vs unreviewed

### 7.7 Categories (row-level)

| Category | Meaning |
|----------|---------|
| `quantity_evidence_complete` | no quantity-evidence open gaps |
| `mapping_slots_complete` | no open gaps on **included** mapping slots |
| `session_reviewed` | `session_review_status` non-blank |
| `no_open_structural_gaps` | quantity evidence complete **and** mapping slots complete |

**Wording lock:** use `no_open_structural_gaps` — **not** product “ready.”  
Do **not** market F2 persisted `status=structurally_ready` as BOQ / cost / 5D readiness. That F2 enum means: no open structural gaps at snapshot time only.

### 7.8 Issue register

- Include rows with **any** open structural gap (quantity and/or included mapping slots)
- Cap sample — **recommended max 25**
- Sort by `ifc_class`, then `source_row_key`
- Each entry should include at least: `source_row_key`, `ifc_class`, `type_name`, `issues` (list of gap codes), relevant origins / source intents

---

## 8. Boundary language (locked)

Every review payload must reinforce:

- This is a **preparation completeness review** only.
- **Not** BOQ
- **Not** QS-certified
- **Not** cost estimate
- **Not** budget
- **Not** procurement
- **Not** payment
- **Not** EVM
- **Not** 5D readiness
- **Not** writeback
- **Not** Modify proposal
- **Not** approval
- Manual session mappings are **weak provenance**
- Missing counts are **preparation gaps**, not commercial blockers
- F2 `structurally_ready` means **no open structural gaps at snapshot time only**

Suggested `non_claims` keys (boolean true):

`not_boq`, `not_qs_certified`, `not_cost_estimate`, `not_budget`, `not_procurement`, `not_payment`, `not_evm`, `not_5d_readiness_claim`, `not_writeback`, `not_modify_proposal`, `not_approval`, `manual_session_is_weak_provenance`, `missing_counts_are_preparation_gaps_only`, `snapshot_stage` / review stage text as appropriate (e.g. preparation completeness review).

---

## 9. Classification relationship

- F3 may report free-text / `manual_session` provenance from F2 rows.
- F3 does **not** require Classification C2/C3.
- F3 does **not** create `ClassificationAssignment`, `ClassificationMapping`, or `ClassificationRule`.
- Later C3 / C3.1 may introduce stronger schema-backed origins; F3 categories remain extensible without claiming official classification authority.

---

## 10. Quantities / F2 relationship

- F3 reviews **frozen F2 version rows** only.
- F3 does **not** rebuild Quantities runtime.
- F3 does **not** mutate `FiveDDataModel` / `FiveDModelVersion` / `FiveDModelRow`.
- F3 does **not** change `qty-prep-export-v1`.
- F3 does **not** use `QTOCache` / `QTOExportView`.
- `unresolved_register_snapshot` is optional diagnostic; **row flags are truth**.

---

## 11. 4D relationship

- **No** schedule alignment in F3
- **No** task / WBS coverage
- **No** EVM
- **No** invented schedule coverage percentages
- **F4** owns 4D alignment later (still no EVM)

---

## 12. Implementation checklist (later — Option A)

When founder authorizes F3 implementation:

- [ ] Add `src/fived/services/completeness_service.py` (`FiveDCompletenessService.build_version_review`)
- [ ] Add `src/fived/tests/test_completeness_service_f3.py`
- [ ] Optionally export service symbol from `src/fived/services/__init__.py`
- [ ] No migrations
- [ ] No UI / Quantities / export / QTOCache / writeback / classification assignment changes
- [ ] Run fived + classification + targeted Quantities regressions
- [ ] Keep local; no push until founder explicitly approves

---

## 13. Tests checklist (later)

- Empty version
- Mixed incomplete rows
- All-clear rows (`no_open_structural_gaps` for all)
- Missing quantity basis / source counts
- Missing classification / package / work counts
- Schema-excluded slots **not** counted as missing
- `manual_session` counted as **filled but weak**
- Session review counted separately (not structural by default)
- No DB writes
- No QTOCache / Quantities runtime / export calls
- Forbidden claim words absent from payload keys / text
- No cost / rate / EVM fields in payload
- Regression: fived + classification + Quantities slices

---

## 14. Explicit non-goals (recap)

No public UI · no rates · no cost totals · no BOQ · no EVM · no QS certification · no writeback · no Modify proposal · no C2/C3 assignments · no qty-prep-export-v1 change · no QTOCache foundation · no schedule alignment · no migrations · no readiness persistence.

---

## 15. Recommendation

This locked **5D-F3** spec is ready.  
Next authorized step: **implement F3 service-only** (`FiveDCompletenessService` + tests), still within Stage 1 preparation boundaries, keep local until founder explicitly approves push after the full 5D Data Model workstream.
