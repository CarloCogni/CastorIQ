# C3 — Schema-Backed Quantities Mapping (Locked Spec)

**Status:** Locked for implementation planning  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `cb1083e07fe392098dcf4f02468c09fde08a482d`  
**Document type:** Product / architecture contract (not an inventory of runtime files)  
**Planning source:** C2/C3 Planning Report — Schema-Backed Quantities Mapping; C2 founder evidence  
**This document does not authorize:** product code changes, migrations, UI implementation, tests, seed changes, fived/Ask/Modify/writeback work, commit, or push by itself.

---

## 1. C3 product goal

C3 adds **schema-backed manual mapping** inside the Quantities Preparation Builder.

When a mapping field is:

- included in schema, **and**
- Source Mapping Intent = `manual_field`, **and**
- the project has a **primary active** classification schema adopted for the relevant `purpose_role`

then the row review / manual mapping drawer should allow selecting a `ClassificationNode` instead of only typing free text.

### C3 is

- Session-only schema-backed (and free-text) preparation mappings
- Additive metadata on session / export / F2 / F3 contracts where present
- Soft UI rename of Package / BOQ Mapping → **Package Mapping** where safe

### C3 is not

- Persisted `ClassificationAssignment`
- Mapping / crosswalk / rules
- AI / ML suggestions
- IFC writeback
- Modify proposals / `ModificationProposal`
- Official classification certification
- BOQ / cost / rates / EVM / QS / 5D readiness claims
- Public approval workflow
- F4 schedule alignment

C3 values are **preparation session annotations** only.

---

## 2. Current prerequisites

| Prerequisite | Status |
|--------------|--------|
| C1 registry (`ClassificationSchema`, `ClassificationNode`, `ProjectClassificationSchema`) | Exists |
| C2 demo pack in pilot DB | Seeded and kept |
| Quantities free-text session manual mapping (`qty-prep-row-mapping-v1`) | Exists |
| F2 preparation snapshots | Exists (additive metadata later) |
| F3 completeness review | Exists (provenance bucket later) |

### Pilot C2 adoptions (kept for C3)

| purpose_role | schema key |
|--------------|------------|
| `element` | `nbkch-demo-elements` |
| `package` | `nbkch-demo-packages` |
| `work_package` | `nbkch-demo-work-packages` |

Evidence: `.df_c2_project_schema_seed_founder_review/` (untracked).

---

## 3. Field-to-purpose mapping (locked)

| Quantities field key | purpose_role | UI label (C3) |
|----------------------|--------------|---------------|
| `classification_code` | `element` | Classification Code |
| `package_boq_mapping` | `package` | **Package Mapping** |
| `work_package` | `work_package` | Work Package |

### Naming locks

- Keep internal key `package_boq_mapping` for Quantities / export / session compatibility.
- Soften UI label to **Package Mapping** where safe (column, drawer, help text).
- F2 continues to use `package_mapping*` storage names where already introduced.
- BOQ / cost-code remains reserved for later `purpose_role=boq_cost_later` — not C3.

---

## 4. Session payload design

Current session mapping (`qty-prep-row-mapping-v1`) stores **string values only**.

C3 must be **additive and backward compatible**.

### Structured field annotation (when schema node selected)

Per row / field, session may store:

| Key | Meaning |
|-----|---------|
| `value` / code | Display primary (same role as today’s string) |
| `label` | Node label helper |
| `schema_id` | Adopted schema UUID |
| `schema_key` | Stable schema key |
| `node_id` | Selected `ClassificationNode` UUID |
| `origin` | `manual_session_schema_node` |
| `source_intent` | `manual_field` |

### Backward compatibility

- Existing string-only payloads remain valid.
- Normalizer: string → treat as free-text session value with `origin=manual_session`.
- Readers must accept both shapes without error.

### Fallback free text

Use free-text input with `origin=manual_session` when:

- no primary active adoption for the role, **or**
- no active nodes on the adopted schema, **or**
- user chooses custom entry instead of a node

Do **not** remove free-text abruptly. Do **not** break existing demos.

### Persistence boundary

- Manual values remain **session-only**.
- Save draft continues to save **settings only**, not row mappings.
- No assignment approval UI.
- No `ClassificationAssignment` rows.

---

## 5. UI / UX behavior

### Row review / manual mapping drawer

When field is eligible (`included` + `manual_field`) **and** adopted schema exists:

- Show selector with **code + label**
- Prefer searchable select when node count is large (guideline: ≳ 50)
- Provenance chip: **Schema node (session)**
- Also allow fallback free text if needed

When no adopted schema (or empty nodes):

- Show current free-text input
- Short note: *No project schema adopted for this mapping role.*
- Provenance: **Free text (session)** when a value is present

### Prep table display

- Main value: selected **code** (or free-text string)
- Optional muted helper: **label** when schema-backed
- Badge: `Schema node (session)` or `Free text (session)`

### Copy / help

Update help modal where Package / BOQ Mapping is named: use Package Mapping; restate preparation-only boundary. Stale help is worse than none.

---

## 6. Data contract impact

C3 must **not break**:

- Current prep table / unresolved register / insights contracts
- `qty-prep-export-v1` required columns and semantics
- Existing F2 snapshots
- Existing F3 reviews

### Additive optional metadata (allowed)

**Classification**

- `classification_schema_id`
- `classification_schema_key`
- `classification_node_id`
- `classification_label`

**Package**

- `package_mapping_schema_id`
- `package_mapping_schema_key`
- `package_mapping_node_id`
- `package_mapping_label`

**Work package**

- `work_package_schema_id`
- `work_package_schema_key`
- `work_package_node_id`
- `work_package_label`

### Keep existing string columns

| Surface | Keep |
|---------|------|
| Quantities / export | `classification_code`, `package_boq_mapping`, `work_package` |
| F2 rows | `classification_code`, `package_mapping`, `work_package` (+ origins) |

Origins may include `manual_session_schema_node` in addition to `manual_session`.

---

## 7. Runtime / service impact

C3 may need:

| Piece | Role |
|-------|------|
| Primary schema lookup by `purpose_role` | Resolve project adoption |
| Active node list for schema | Selector payload |
| Session mapping service extension | Accept structured payload; normalize strings |
| Export serializer | Additive optional metadata; keep old fields |
| F2 snapshot service | Copy metadata when present |
| F3 completeness | Provenance bucket `manual_session_schema_node`; filled counts |

### Explicit non-goals

- `ClassificationAssignment` / `ClassificationMapping` / `ClassificationRule`
- Migrations unless absolutely necessary (prefer session + export JSON / existing provenance fields first)
- Writeback / Ask / Modify
- Automatic production seeding

---

## 8. Relationship to F2 / F3

### F2

- Copy schema / node metadata **if present**
- Keep string code as primary display / hash-relevant value as today
- Store origin `manual_session_schema_node` when applicable
- Prefer fitting metadata into existing JSON / provenance fields if available
- If new DB columns are required, plan a **separate, careful migration** — not silent breakage of old snapshots

### F3

- Count schema-backed session values as **filled**
- Provenance bucket: `manual_session_schema_node` (stronger than free-text `manual_session`, still **not approved**)
- `no_open_structural_gaps` may increase when mappings are filled
- Still no BOQ / cost / 5D readiness / approval claims

---

## 9. Boundary language (locked)

C3 values are:

- preparation mappings only
- session annotations
- **not** official classification certification
- **not** BOQ
- **not** cost code approval
- **not** QS-certified
- **not** 5D ready
- **not** IFC writeback
- **not** Modify proposal
- **not** approved

Forbidden product wording: “BOQ ready”, “cost ready”, “5D ready”, “EVM ready”, “approved”, “certified”.

---

## 10. Minimal implementation options

| Option | Scope | When |
|--------|-------|------|
| **A** | Selector data service + tests only; no UI | Thin slice / risk reduction |
| **B** | Selector service + drawer UI + session payload extension + export / F2 / F3 additive metadata + tests | Full C3 product goal |
| **C** | Persisted assignments (C3.1) | **Not now** |

### Locked recommendation

**Option B** for C3 implementation (after a short implementation planning gate).

Rationale: C2 already supplies pilot schemas; without UI + session + additive contracts, C3 does not deliver schema-backed Quantities mapping. Option A may be used as an internal first PR within Option B if the implementer wants service/tests before templates, but the locked C3 slice is Option B end-to-end.

Option C (`ClassificationAssignment`) waits for founder review after C3 evidence (**C3.1**).

---

## 11. Tests needed later

When implementation is authorized, cover:

- Project primary schema lookup by `purpose_role`
- Node selector payload (active nodes only)
- Fallback free text when no adoption / empty nodes
- Backward compatibility with string-only session mappings
- Structured session mapping store + normalize
- Unresolved register gaps clear when schema node selected
- Table displays code / optional label / provenance badge
- Export includes optional schema metadata; old fields unchanged
- F2 snapshot copies metadata or safely stores it without breaking old versions
- F3 counts `manual_session_schema_node` provenance
- Save / load draft remains settings-only
- No `ClassificationAssignment` / Mapping / Rule
- No writeback / `ModificationProposal`
- No BOQ / cost / 5D / EVM / approval claim strings
- Quantities / fived / classification regressions

---

## 12. Founder decisions locked

1. C3 remains **session-only**.
2. Keep **free-text fallback**.
3. Use **code** as display primary; **label** as helper.
4. Store `node_id` + `schema_id` + `schema_key` + `label` **additively**.
5. Rename UI **Package / BOQ Mapping → Package Mapping** where safe; keep internal key `package_boq_mapping`.
6. No persisted assignments until **C3.1**.
7. No public approval workflow.
8. No writeback / Modify proposal creation.
9. **C3 happens before F4** schedule alignment.
10. Keep work local; **do not push** until the full 5D / classification workstream is ready and founder explicitly approves.
11. Prefer **no migrations**; if F2 needs columns, isolate that decision and migrate carefully.
12. Demo C2 pack remains in pilot DB as C3 selector source.

---

## 13. Acceptance criteria (for later implementation review)

C3 implementation is complete when:

1. Eligible Manual-field mapping slots can select nodes from primary active schemas.
2. Free-text fallback still works when no adoption / custom entry.
3. Session accepts structured + legacy string payloads.
4. Export / F2 / F3 remain backward compatible with additive metadata.
5. UI shows Package Mapping label where updated; provenance badges present.
6. Help modal updated for schema-backed session mapping + boundaries.
7. Tests in §11 pass; no Assignment / writeback / BOQ-5D claims.
8. Founder evidence can show selector + session value + export optional fields without claiming readiness.

---

## 14. Next step after this lock

**Ready for C3 implementation planning** (Option B: selector service + Quantities drawer UI + session/export/F2/F3 additive path), still without C3.1 assignments and without push.
