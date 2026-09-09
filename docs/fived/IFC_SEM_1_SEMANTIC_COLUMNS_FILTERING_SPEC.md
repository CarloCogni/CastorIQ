# IFC-SEM-1 — IFC Semantic Columns and Filtering for Smarter Mapping

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `f43d577d52d33a7361777421c561eed570d4ccc5`  
**Depends on:** MAP-BIG batch mapping + founder walkthrough gap P1  

---

## 1. User story

As a BIM/5D user, I want to view and filter IFC semantic properties in the Quantity Preparation table so I can map rows to project schema nodes based on real model data, not only IFC class.

---

## 2. Supported first slice

**Option C (discovery):** Prep-native fields + read-only enrichment of high-coverage entity properties onto prep rows.

Included:

- Semantic field discovery service (read-only)
- Compact **IFC semantic filters** panel on Quantities
- Filter by one field/value at a time (GET params)
- Sort optional for numeric `element_count`
- “Select filtered rows” into existing batch mapping checkboxes
- Existing Preview → Apply session → Freeze → 5D Review unchanged

Excluded from this slice:

- Arbitrary pset column picker for all keys
- GIN index / migrations
- Level/zone population (stubs remain empty)
- IFC classification reference indexing
- S2 provenance contract change
- Writeback / Modify / cost / Excel / Ask automation

---

## 3. Candidate semantic fields (only if available)

### Prep-native (always discoverable when rows exist)

| key | label | filterable | sortable |
|-----|-------|------------|----------|
| `ifc_class` | IFC Class | yes | yes |
| `type_name` | Type Name | yes | yes |
| `quantity_basis` | Quantity Basis | yes | yes |
| `quantity_source` | Quantity Source | yes | yes |
| `element_count` | Element Count | no (exact filter optional later) | yes |

### Entity-derived enrichment (pilot-proven keys)

| key | source property | label | notes |
|-----|-----------------|-------|-------|
| `semantic_category` | `Other.Category` | Category | majority per prep grain |
| `semantic_family` | `Other.Family` | Family | majority per prep grain |

If enrichment finds no values: fields omitted or shown with coverage 0 and helper copy.

### Honest non-available state

If no entity property enrichment is possible:

> “No indexed IFC property-set columns are available on preparation rows yet. Current filters use preparation fields (class, type, basis, source).”

Do not invent fields that are not present in code/data.

---

## 4. UX concept

**Panel title:** IFC semantic filters  

**Subtitle:** Use available model data to find rows before batch mapping.

**Controls:**

- Field select (available discovered fields)
- Value select (sample / distinct values for that field among prep rows)
- Apply filter
- Clear filters
- Select filtered rows
- Footnote: showing X of Y preparation rows (after filter)
- Optional sort: `semantic_sort=element_count` (asc/desc) if cheap

Placement: near Batch schema mapping toolbar — compact, not a huge new section.

Batch mapping flow unchanged: filter/select → Map selected → Preview → Apply → Freeze → Review.

No raw JSON dumps. No model volume units as confirmed product units. No cost/BOQ claims.

---

## 5. Data behavior

- Read-only extraction from existing indexed data (`IFCEntity` + prep aggregates)
- No IFC mutation; no Modify proposals
- No DB write except existing session mapping on Apply
- Freeze captures mapping as today
- Semantic filter provenance in S2: **deferred** (would risk contract change)

Query params (suggested):

- `semantic_field=<key>`
- `semantic_value=<value>`
- `semantic_sort=<key>` (optional)
- `semantic_sort_dir=asc|desc` (optional)

---

## 6. Performance boundary

- Do not load 6000+ entities into DOM
- Enrichment: single iterator with `.only("ifc_type", "properties")` (+ type name via select_related if needed); known keys only
- Cap discovery sample values (e.g. ≤20 distinct)
- Keep Quantities avg **&lt; 2.5s** on pilot
- Prep table remains capped at 50 rows

---

## 7. Non-goals

- Excel import, Ask-to-5D, AI matching
- Writeback / new IFC property creation
- Rates / cost / BOQ / EVM / payment
- Certified assignment
- Migrations (report first if unavoidable — not expected)
- S2 payload contract change

---

## 8. Acceptance criteria

- [ ] User sees available semantic fields
- [ ] User can filter by at least IFC Class (and Type/Basis when present)
- [ ] Category/Family filters available when enrichment finds values
- [ ] Select filtered rows works with batch mapping
- [ ] Existing batch Preview/Apply still works
- [ ] MAP-BIG-2 snapshots still open unchanged
- [ ] No performance regression beyond budget
- [ ] Honest helper when property enrichment empty
- [ ] No GitHub / no push / no PR for this local work

---

## 9. Commit plan

1. `docs(fived): define ifc semantic filtering for mapping`
2. `feat(takeoff): discover ifc semantic mapping fields`
3. `feat(takeoff): add semantic filters to quantity preparation`

---

## 10. Demo proof

Freeze (do not delete prior demos): `FOUNDER-DEMO-IFC-SEM-1-v1` after filter → select → batch map → apply → freeze → Review.
