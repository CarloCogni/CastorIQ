# 5D-MAP-BIG-1 — Practical Schema Mapping Workflow (Locked Spec)

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `04c5822bbb122a75189797c670a6ab30864a1944`  
**Document type:** Product / UX / data-behavior contract  
**Depends on:** C3 schema-backed Quantities mapping (`docs/classification/C3_SCHEMA_BACKED_QUANTITIES_MAPPING_SPEC.md`), F2 snapshot, S2/S3 Quantity Review  
**This document does not authorize:** push, PR, merge, remote changes, DB reset, FOUNDER-DEMO deletion, cost/rates/BOQ/EVM, Excel import, Ask-to-5D automation, writeback/Modify changes, QTOCache/QTOExportView changes, or S2 payload contract changes.

---

## 1. User story

> As a BIM/5D user, I want to map multiple similar IFC quantity rows to project schema nodes, preview the effect, and freeze a reviewed snapshot so I can improve 5D quantity coverage.

### Product goal

A user can:

1. Filter/select similar quantity rows  
2. Choose target schema nodes  
3. Preview affected rows before applying  
4. Apply mappings to the **session / preparation** state  
5. See mapping coverage improve on Quantities  
6. Freeze a **new** 5D snapshot  
7. Open 5D Quantity Review and see improved mapped/unmapped coverage  

### Product boundary (still true)

This is **preparation/review mapping**, not certified classification assignment.

It does **not**:

- Create QS approval  
- Calculate cost or multiply rates  
- Create BOQ / EVM / payment outputs  
- Permanently assign classifications in a `ClassificationAssignment` model  
- Mutate an existing frozen snapshot in place (including FOUNDER-DEMO)

---

## 2. Supported mapping modes (this slice)

### Mode A — Select rows manually (required)

- User checks rows on the prep table  
- Chooses classification / package / work-package schema nodes  
- Previews  
- Applies to session mapping  

### Mode B — Map similar rows from one reviewed row (required)

From the row review drawer, user can choose **Apply to similar rows**.

Similarity (exact, deterministic):

- Same IFC class, **and/or**  
- Same type name (exact match), **and/or**  
- Same quantity basis  

UI must let the user see which similarity axes are active and preview the affected set before apply.

### Mode C — Map by IFC class filter (optional if low risk)

- e.g. “all `IfcBeam` rows in current prep model”  
- Must show affected count and full preview before apply  
- Same apply path as Mode A  

### Explicitly out of scope

- AI suggestions  
- Excel import  
- Automatic mapping without preview  
- Permanent `ClassificationAssignment` model  
- Approval workflow  
- Rates / cost / BOQ / EVM / payment  
- Ask-to-5D automation  
- Writeback / Modify approval behavior  
- Changes to QTOCache / QTOExportView  
- Breaking prep export contract  
- Changing S2 payload contract  

---

## 3. Data behavior

### Storage

- **Use existing session mapping** (`qty_prep_row_mapping:{project_id}`, contract `qty-prep-row-mapping-v1`).  
- **Do not** add new persistent mapping models or migrations.  
- Batch apply writes **session/prep state only**.  
- Row keys remain the existing C3 keys:  
  `v1|{grain}|{ifc_class}|{type_name_or_-}|{quantity_basis_or_-}`  

### Field payload

Per apply, each of the three mapping slots may be:

| Input | Behavior |
|-------|----------|
| Schema node selected | Validate via existing `build_validated_session_mapping`; store structured C3 value (`origin=manual_session_schema_node`) |
| Free-text (only if already allowed for that field and no valid node) | Store legacy string path as today |
| **Empty / not provided** | **Leave existing mapping for that field unchanged** (batch-specific) |

> **Contract difference vs single-row drawer:** today’s single-row `apply_values` clears eligible fields omitted from the POST. Batch apply must **not** clear on empty. Single-row behavior remains unchanged.

### Overwrite

- If a selected row already has any mapping in slots being set, preview must show an **overwrite warning** (count + samples).  
- Apply only after explicit preview → Apply.  
- No silent overwrite.

### Clear mapping

- Do **not** invent a new bulk-clear unless the existing per-row clear path is reused safely.  
- Prefer: single-row clear remains in drawer; batch clear is out of scope unless trivially reusing `clear_values` with preview.

### Freeze / Review

- F2 snapshot captures resulting session mapping via **existing** F2 behavior (no F2 API redesign).  
- S2/S3 reflect improvements **only after a new snapshot** is created.  
- **Never mutate FOUNDER-DEMO** version/model; create a new version label for demos (e.g. `FOUNDER-DEMO-MAP-BIG-1-v1`).

---

## 4. UX behavior

### Quantities page additions

Small **Batch schema mapping** toolbar on the prep table:

- Selected count  
- **Map selected rows**  
- **Clear selection**  

Prep rows gain checkboxes (row_key-backed).

### Modal / drawer: Batch schema mapping

**Step 1:** Select rows (table or Mode B/C seed).  
**Step 2:** Open **Map selected rows**.  
**Step 3:** Modal fields:

- Classification node  
- Package node  
- Work package node  

Also show:

- Selected row count  
- Affected IFC classes (summary)  
- Affected type names (summary, capped)  
- Affected quantity basis values (summary)  

**Step 4 — Preview:**

Rows to be updated (sample capped, e.g. 25), each with:

- Row label  
- IFC class  
- Type name  
- Current mapping (display)  
- New mapping (display)  
- Quantity basis  
- Total quantity display  
- Unit display  

Plus:

- Valid / ignored / missing key counts  
- Overwrite warning count  

**Step 5 — Apply to session**

After apply:

- Table updates with schema-mapped badges (existing overlay)  
- Mapped/unmapped counts update if already computed in current view  
- Prompt:  
  **“Freeze a new snapshot to review updated 5D coverage.”**

### Required copy

- “This updates the current preparation session. Freeze a snapshot to review the updated 5D coverage.”  
- “Existing mapped rows will be overwritten only after preview.”  

### Forbidden copy on main UI

- certified / certification  
- BOQ / cost-ready / rates / EVM / payment  
- automatic classification / AI mapping  
- Raw `manual_session_schema_node` as a user-facing title  
- “Schema Insight” product title  
- “model volume units” on main prep table  

---

## 5. Backend contract (implementation guidance)

### Prefer two endpoints (or one with `preview_only`)

**Preview** (no session write):

Input:

- `row_keys: list[str]`  
- optional `classification_code` / `package_boq_mapping` / `work_package` node ids (and free-text only if already supported)  

Output:

- `selected_row_count`  
- `valid_row_count`  
- `ignored_or_missing_row_count`  
- current mapping summary  
- proposed mapping summary  
- `overwrite_warning_count`  
- `rows_sample` (≤25)  
- similarity context if Mode B/C  

**Apply** (session write only):

- Same payload  
- Deduplicate row keys  
- Ignore unknown keys safely  
- Reject invalid node ids (or fall back per existing C3 rules — prefer reject invalid node without clearing other fields)  
- Does **not** create an F2 snapshot  
- Returns toast + redirect/HTML partial consistent with takeoff HTMX patterns  

### Unchanged

- Existing single-row mapping POST  
- Prep export contract  
- F2 / S2 / S3 contracts  
- Writeback / Modify  

---

## 6. Safety

- No silent overwrite without preview  
- Overwrite warning when selected rows already mapped in slots being set  
- Empty target field → leave unchanged (batch)  
- Explicit clear only via existing safe paths  
- Never mutate FOUNDER-DEMO snapshot  
- Never mutate DB models except existing request/session mechanisms and existing F2 freeze (new version)  
- Auth / CSRF / project membership same as Quantities mapping today  

---

## 7. Acceptance criteria

- [ ] User can map multiple rows in one action  
- [ ] User can preview affected rows before apply  
- [ ] Mode B “similar rows” works with preview  
- [ ] Existing single-row review mapping still works  
- [ ] Existing prep export still works  
- [ ] Existing F2 snapshot still works  
- [ ] Existing 5D Quantity Review still works  
- [ ] New freeze shows improved mapped/unmapped coverage without deleting FOUNDER-DEMO  
- [ ] No cost / rate / BOQ / EVM claims  
- [ ] No main-UI raw technical strings listed above  
- [ ] No push / PR as part of this workstream  

---

## 8. Demo proof (Phase 3)

- Keep original FOUNDER-DEMO intact  
- Create local demo snapshot e.g. `FOUNDER-DEMO-MAP-BIG-1-v1`  
- Map a meaningful honest group beyond 3 rows (Beam / Wall / Column / Slab where schema nodes fit)  
- Keep unmapped rows honest  
- Confirm Unit not resolved remains honest when units are unresolved  

---

## 9. Local delivery policy

- All commits local until founder approves another push  
- Evidence under `.df_5d_map_big_1_mapping_workflow/` stays untracked unless requested  
- Spec commit message: `docs(fived): define practical schema mapping workflow`  
- Feature commit message (later): `feat(takeoff): add batch schema mapping workflow`  
