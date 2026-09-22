# 5D-S1 — Schema-Driven Quantity Model (Locked Spec)

**Status:** Locked for implementation planning  
**Slice:** 5D-S1 — Schema-Driven Quantity Model (spec only)  
**Next build slice (authorized only after founder approval of this lock):** 5D-S2 insight service  
**Future insight contract (S2):** `fived-schema-quantity-insight-s2-v1`  
**Depends on:** Quantities Preparation Builder; C1/C2 classification registry; C3 schema-backed mapping; F2 snapshot (`fived-snapshot-f2-v1`); F3 completeness (`fived-completeness-f3-v1`)  
**Branch context:** `integration/df-foundation-main-sync` @ `7a63fb314e3a160589bf6b591a2b5f9ba63e03cf`  
**Ahead of origin at lock:** 9 local commits (keep local; no push until founder explicitly approves after the full 5D/classification workstream)

**Planning source:** 5D-S1 — Schema-Driven Quantity Model Planning report (founder-corrected scope).  
**This document does not authorize:** product code, services, tests, migrations, UI, export changes, commit, or push by itself.

---

## 1. Product definition

5D-S1 defines the **current 5D workstream** as a **schema-driven IFC quantity model**.

### Meaning

| Premise | Implication |
|---------|-------------|
| IFC model is the quantity source | Totals come from indexed IFC quantities via Quantities prep |
| Quantities / F2 snapshot rows are the stable analytical input | Insight is versioned and reviewable |
| Project schema interprets grouping and measurement | Adopted classification / package / work schemas drive rollups |
| C3 schema provenance links rows to nodes | Session → export → F2 → F3 chain carries `node_id` / `schema_key` / origin |
| Insights summarize quantities by schema nodes and expose gaps | First useful output is grouped totals + gap counts |
| Optional rates/cost metadata may exist later as schema data | Placeholders only — **no cost calculation now** |

### It is not

- BQ / specification linkage  
- Schedule cost loading / 4D cost alignment  
- EVM  
- BOQ generation  
- Cost estimate  
- Rate calculation (`unit_rate × quantity` or any extension)  
- Payment / procurement  
- QS certification  
- IFC writeback  
- Modify proposal / `ModificationProposal`  
- Ask / RAG implementation  
- Official / approved / certified classification authority  

Allowed language: schema-driven quantity insight, preparation grouping, snapshot review, unmapped gaps, basis/unit buckets, provenance counts.

Forbidden claim language: BOQ ready, 5D ready, cost ready, estimate ready, QS approved, certified cost, earned value, payment/procurement readiness, schedule cost loaded.

---

## 2. Current foundation

| Foundation | Status | Role for S1/S2 |
|------------|--------|----------------|
| Quantity Preparation Builder | Exists | Capture surface for basis, schema includes, source intents, session mapping |
| C1 Classification Registry | Exists | `ClassificationSchema` / `ClassificationNode` / `ProjectClassificationSchema` |
| C2 demo schemas / nodes / adoptions | Exists (pilot) | Element / package / work_package demo vocabulary |
| C3 schema-backed session mapping | Exists | Drawer select + free-text fallback; session structured values |
| `qty-prep-export-v1` additive schema metadata | Exists | Do **not** alter; remains export contract |
| F2 versioned snapshot | Exists | Frozen `FiveDModelVersion` + `FiveDModelRow` analytical input |
| F3 completeness review | Exists | Gap / provenance register; complementary to S2 |
| C3 full founder smoke | **Pass** | Quantities schema node → table provenance → export meta → F2 provenance → F3 counts |

C3 founder smoke evidence (local, untracked): `.df_c3_full_schema_provenance_founder_smoke/`.

---

## 3. Corrected boundary (founder lock)

| Lock | Statement |
|------|-----------|
| 5D vs 4D | Current **5D Data Model** is **separate from the 4D Model** |
| Immediate 5D scope | **Schema-driven IFC quantity analysis** |
| Schedule | **4D schedule alignment** is **future integration only** (not S1–S3 build) |
| BQ / spec | **Not** the immediate next implementation scope |
| Rates / cost | May be modeled later as **optional schema data**; **not calculated** in S1/S2 |
| Export / legacy QTO | Do not alter `qty-prep-export-v1`; do not use `QTOCache` / `QTOExportView` |
| Push policy | **Keep local.** No push until founder explicitly approves |

This supersedes any earlier reading of F1 that treated schedule alignment or BQ/spec linkage as the next 5D build focus.

---

## 4. Minimal data shape

| Decision | Lock |
|----------|------|
| Compute mode | **Service-only** computed insight |
| Input | Existing **F2** `FiveDModelVersion` + `FiveDModelRow` |
| Grouping references | C3 provenance on rows + C1/C2 schema/node data |
| Migrations for S2 | **None** if possible |
| New tables for S2 | **None** initially |
| Export | Do **not** change `qty-prep-export-v1` |
| Cache | Do **not** use `QTOCache` |
| Writes | Do **not** mutate snapshots or Quantities session from S2 |

Preferred join path:

1. Read frozen F2 rows.  
2. Read `quantity_provenance["mapping"]` when present (`schema_id`, `schema_key`, `node_id`, `label`, `origin`).  
3. Fall back to string code fields + origin when metadata is absent.  
4. Resolve node labels/codes from `ClassificationNode` when `node_id` is valid.  
5. Emit JSON-safe insight dict (F3-like).

---

## 5. Schema-driven grouping

S2 must group rows by:

1. **Classification node** (primary)  
2. **Package node** (primary, equal weight)  
3. **Work package node** (optional but useful)

### Join rules

| Priority | Source |
|----------|--------|
| 1 | `quantity_provenance.mapping.<slot>.node_id` / `schema_key` / `label` / `origin` |
| 2 | Fallback: row string code (`classification_code` / `package_mapping` / `work_package`) + origin |
| 3 | **Unmapped** bucket when slot is included and value/metadata missing |

### Unmapped

- Unmapped rows go into a dedicated **Unmapped** bucket.  
- **Do not** force unmapped rows into schema groups.  
- Unmapped is an insight gap, not a crash.

### Provenance awareness

Rollups should distinguish (at least in counts):

- `manual_session_schema_node`  
- `manual_session` (free text)  
- empty / missing / deferred intents as applicable  

Schema-backed is stronger than free-text for insight labeling, but **still not approved / official**.

---

## 6. Quantity basis handling

| Rule | Lock |
|------|------|
| Summarize by basis/unit buckets | Required |
| Mixed units / mixed bases | **Never coerce** into one total |
| Split totals by | `quantity_basis`, `unit_basis`, and `quantity_source` when useful |
| Missing basis / source | Count separately |
| Gap severity | Insight gaps — **not** hard failures |

Every node rollup entry that reports quantity must be keyed (or nested) by basis/unit so consumers cannot mis-sum incompatible measures.

---

## 7. First insight output (S2 contract sketch)

**Future service return contract:** `fived-schema-quantity-insight-s2-v1`

Future S2 service should return a JSON-safe dict including:

| Key | Purpose |
|-----|---------|
| `contract_version` | `fived-schema-quantity-insight-s2-v1` |
| Version metadata | `version_id`, `version_label`, `data_model_id`, `content_hash` as available |
| Project / model metadata | Project id/name; model name |
| `boundary` / `non_claims` | Explicit not-BOQ / not-cost / not-EVM / not-certified / not-writeback |
| `row_count` | Frozen row count |
| `quantity_totals_by_classification` | Grouped totals (split by basis/unit) |
| `quantity_totals_by_package` | Grouped totals (split by basis/unit) |
| `quantity_totals_by_work_package` | Optional |
| `unmapped_counts` | Per slot + overall |
| `missing_mapping_counts` | Included-slot gaps |
| `quantity_basis_gap_counts` | Missing basis / source / unresolved |
| `provenance_counts` | Including `manual_session_schema_node` |
| `basis_unit_buckets` | Cross-cutting basis/unit summary |
| `rate_metadata_presence` | Optional later — counts only, **no multiplication** |
| Issue / sample rows | Capped representative unresolved rows |
| `generated_at` | UTC timestamp |

---

## 8. Rates / cost boundary

| Allowed (later / conceptual) | Forbidden in S1/S2 implementation |
|------------------------------|-----------------------------------|
| Schema may hold optional rate/cost **metadata** | `unit_rate × quantity` |
| Rate metadata **present / missing** counts | `total_cost` / `estimated_cost` / budget |
| Future import/edit of rates (separate slice) | Payment / procurement |
| Cost calculation **planning** (5D-S6) | EVM |
| | BOQ generation / QS certification |

`boq_cost_later` purpose / purpose_role remains **reserved** — not activated by S1/S2.

---

## 9. External resource feeding (later, conceptual)

Document only — **not** S1/S2 implementation:

- Excel classification / rate tables may later create/update schema nodes and metadata.  
- Ask / document side may later **suggest** mappings or metadata.  
- IFC queries may later suggest candidate filters.  
- External values are **not automatic authority**.  
- Human / session review remains required.  
- No Ask / document / Excel import implementation in S1 or S2.

---

## 10. Relationship to F2 / F3

| Layer | Role |
|-------|------|
| **F2** | Provides **frozen** row input (codes, origins, totals, `quantity_provenance`) |
| **F3** | Provides **completeness / gap / provenance** review |
| **S2** | Adds **grouped schema-driven quantity insight** |

Additional locks:

- S2 does **not** replace F3.  
- S2 does **not** mutate snapshots.  
- S2 does **not** rebuild Quantities runtime.  
- S2 reads **only** frozen F2 rows plus classification / schema references.  
- Live Quantities runtime may later support preview; **not required for S2**.

---

## 11. Future schema measurement metadata

Plan only (prefer existing `ClassificationNode.metadata` JSON — no migration if possible):

Optional future keys (illustrative):

- `target_ifc_classes`  
- `type_name_hints`  
- `preferred_quantity_basis`  
- `unit_basis`  
- `grouping_role`  
- optional rate metadata placeholders  
- optional cost category placeholders  

Hard rules when that convention lands:

- **No** auto quantity override of prep/F2 totals  
- **No** rate calculation  
- **No** cost calculation  
- **No** authority / certified claim  

Measurement metadata is **deferred** relative to S2 (see founder decisions).

---

## 12. Minimal slices

| Slice | Intent | Build now? |
|-------|--------|------------|
| **5D-S1** | Locked spec only (this document) | Spec only |
| **5D-S2** | Schema-driven quantity insight service over F2 | Next build (after founder OK) |
| **5D-S3** | Thin evidence / report page or evidence runner | After S2 |
| **5D-S4** | Optional schema node measurement metadata convention | After S2/S3 as needed |
| **5D-S5** | External schema / rate import **planning** only | Planning |
| **5D-S6** | Cost calculation **planning** only | Planning — **do not implement** |

**Do not** recommend as next build work: BQ/spec linkage, schedule alignment, cost calculation, Ask import.

---

## 13. Founder decisions locked

| # | Decision |
|---|----------|
| 1 | S2 computes from **F2 snapshot first** |
| 2 | Rollups include **classification** and **package** as **equal primary** outputs |
| 3 | **Work package** rollup is **optional but useful** |
| 4 | Unmapped rows go into **Unmapped** bucket |
| 5 | Mixed bases / units are **split**, never coerced |
| 6 | Measurement metadata **deferred** (S4) |
| 7 | Rates / cost metadata placeholders allowed conceptually; **calculation not allowed** |
| 8 | **Keep local**, no push until founder explicitly approves |

---

## 14. S2 implementation checklist (later)

When founder authorizes S2 implementation:

| Item | Expectation |
|------|-------------|
| Service | `src/fived/services/schema_quantity_insight_service.py` |
| Tests | `src/fived/tests/test_schema_quantity_insight_service_s2.py` |
| Helpers | Reuse classification selectors / node lookup as needed |
| Migrations | **None** |
| UI | **None** in S2 |
| Export | **No** `qty-prep-export-v1` change |
| QTO | **No** `QTOCache` / `QTOExportView` |
| Writeback | **No** Ask / Modify / writeback touch |
| Pattern | F3-like: read-only, JSON-safe dict, no DB writes |

Suggested API shape (non-binding until S2 planning):

```text
SchemaQuantityInsightService.build_version_insight(version: FiveDModelVersion) -> dict
```

---

## 15. S2 tests checklist (later)

| Area | Assert |
|------|--------|
| Grouping | By classification node |
| Grouping | By package node |
| Optional | Work package rollup |
| Unmapped | Dedicated bucket; no force-fit |
| Basis | Mixed basis/unit split |
| Gaps | Missing basis / source counts |
| Join | Schema metadata vs fallback code |
| Provenance | Counts include `manual_session_schema_node` |
| Cost | No rate × quantity; no cost totals |
| Forbidden fields | No BOQ / EVM / cost claim keys |
| Side effects | No DB writes |
| Boundaries | No QTOCache; no Modify / writeback |
| Regression | F2 / F3 / classification / Quantities targeted tests still pass |

---

## 16. Non-claims block (required on S2 payloads)

S2 responses must include explicit non-claims equivalent to:

- `not_boq`  
- `not_qs_certified`  
- `not_cost_estimate`  
- `not_budget`  
- `not_procurement` / `not_payment`  
- `not_evm`  
- `not_5d_readiness`  
- `not_writeback`  
- `not_modify_proposal`  
- `not_approval`  
- `schema_session_is_not_approved` (align with F3/C3)  
- `mixed_bases_not_coerced` (or equivalent wording in boundary text)

---

## 17. Out of scope reminder

S1/S2 must not:

- Implement BQ/spec linkage  
- Implement schedule alignment / schedule cost loading  
- Implement cost or rate calculation  
- Implement EVM  
- Implement BOQ generation  
- Implement payment / procurement  
- Alter `qty-prep-export-v1`  
- Touch `QTOCache` / `QTOExportView`  
- Touch Ask / Modify / writeback  
- Add Assignment / Mapping / Rule persistence  
- Add public commercial 5D UI claiming readiness  

---

**End of 5D-S1 locked spec.**  
Next authorized planning step after founder acceptance: **5D-S2 implementation planning** (service + tests only; still no UI / migrations / export changes unless separately approved).
