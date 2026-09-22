# IFC-SEM-2 — IFC Property Column Picker

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `dd2eaa921b4d16f443061803cb4e18f4dc44591d`  
**Depends on:** IFC-SEM-1  

---

## 1. User story

As a BIM/5D user, I want to add IFC property-set and parameter columns to the Quantity Preparation table so I can filter and map rows based on actual model semantics, not only IFC class.

---

## 2. Supported first version

- Discover indexed IFC property keys from `IFCEntity.properties` (ranked, capped).
- Compact **IFC property columns** controls on Quantities.
- User adds selected `prop:<PropertyKey>` columns (session/request via query params).
- Prep-row display aggregation: single value / `Mixed values` / `—`.
- Filter by selected property column value; select filtered rows; existing batch mapping.
- Keep SEM-1 prep-native fields and Category/Family.

Out of scope: writeback, Ask, Excel, AI mapping, cost/BOQ, unit confirmation, migrations, S2 provenance change, full 6000-row table.

---

## 3. Candidate property columns

Only keys found in pilot index (see `.df_ifc_sem_2_property_column_picker/candidate_columns.json`).

First-class examples: `Other.Category`, `Other.Family`, `Other.Type`, `Other.Family and Type`, `Identity Data.Type Name`, `Identity Data.4D status`, `Identity Data.Workset`, `Phasing.Phase Created`.

Exclude noisy `*.id` / GUID-like keys. Cap discovery list (~25) and selected columns (~5).

---

## 4. UX

**Title:** IFC property columns  
**Subtitle:** Add model properties to refine row selection before mapping.

Controls: Add column · selected chips · filter field/value · Clear · Select filtered rows.

Table: selected property columns appear as readable values (tooltip for long text). No raw JSON.

Query params:
- `sem_cols` — comma-separated `prop:...` keys
- Reuse `semantic_field` / `semantic_value` for filter (including `prop:` keys)
- Preserve existing SEM-1 params

---

## 5. Data behavior

- Read-only entity property scan; no IFC mutation; no DB writes except session mapping on Apply.
- Column selection is request/query based (no permanent model).
- Freeze/Review unchanged; property columns need not persist into F2.

Aggregation on grouped prep rows:
- all entities agree → show value
- multiple nonempty values → `Mixed values`
- none → `—`

---

## 6. Performance

- One capped entity iterator per Quantities build
- Do not dump all properties into DOM
- Quantities avg &lt; 2.5s on pilot

---

## 7. Non-goals

No property authoring, writeback, Ask, Excel, AI auto-map, cost/rates/BOQ/EVM, unit confirmation, full entity table.

---

## 8. Acceptance criteria

- [ ] Available property columns visible
- [ ] User can add selected columns to prep table
- [ ] Filter by at least one real property field
- [ ] Select filtered → batch map works
- [ ] Old snapshots intact; no perf regression; no raw JSON; no GitHub

## 9. Commits

1. `docs(fived): define ifc property column picker`
2. `feat(takeoff): discover indexed ifc property columns`
3. `feat(takeoff): aggregate ifc properties for prep rows`
4. `feat(takeoff): add ifc property filters to quantities`
