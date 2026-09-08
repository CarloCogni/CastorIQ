# C2 — Project Classification Schemas (Locked Spec)

**Status:** Locked for implementation planning  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `26a52bd09981b788563de3915146d9aabca01648`  
**Document type:** Product / architecture contract (not an inventory of runtime files)  
**Planning source:** C2/C3 Planning Report — Schema-Backed Quantities Mapping  
**This document does not authorize:** product code changes, migrations, UI, seed execution, Quantities/fived/Ask/Modify/writeback work, commit, or push by itself.

---

## 1. C2 product goal

C2 prepares **project-level classification data** so later **C3** can use schema-backed Quantities mapping.

### C2 is about

- Project-adopted schemas
- Custom / demo project nodes
- Primary active schemas for:
  - `element`
  - `package`
  - `work_package`
- Optional repeatable pilot seed helper (later implementation step)

### C2 is not

- Quantities UI
- Row assignment
- Persisted `ClassificationAssignment`
- Mapping / crosswalk
- Rules
- AI classification
- Official external-standard claim
- BOQ / cost approval
- 5D readiness
- IFC writeback
- Modify proposal

C2 is a **project schema readiness** slice. It does not change Quantities, F2/F3, or writeback behavior.

---

## 2. Current foundation

### Classification C1 (exists)

- Models: `ClassificationSchema`, `ClassificationNode`, `ProjectClassificationSchema`
- Adoption purpose roles already include `element`, `package`, `work_package` (and reserved `boq_cost_later`)
- C1 has **no** assignments, mappings, or rules
- Spec: `docs/classification/C1_CLASSIFICATION_REGISTRY_FOUNDATION_SPEC.md`

### FiveD F2 / F3 (exist)

- F2 can snapshot Quantities prep rows into `FiveDModelVersion` / `FiveDModelRow`
- F3 can review completeness of frozen snapshot rows
- Founder evidence showed structural gaps: many rows missing classification / package / work mapping
- No BOQ / cost / EVM / 5D readiness claims

### Quantities (current behavior — out of C2 scope)

Session-only free-text values for:

| Runtime key | Current UI label |
|-------------|------------------|
| `classification_code` | Classification Code |
| `package_boq_mapping` | Package / BOQ Mapping |
| `work_package` | Work Package |

Source intents remain: `manual_field` | `future_modify_handoff` | `not_mapped`.  
C2 must **not** change Quantities, `qty-prep-export-v1`, or session mapping services.

---

## 3. C2 scope

C2 should create or support creation of project schemas and nodes for:

| Role | Purpose | Use later in C3 |
|------|---------|-----------------|
| Element / internal classification | `purpose_role=element` | Classification Code selector |
| Package mapping | `purpose_role=package` | Package Mapping selector |
| Work package | `purpose_role=work_package` | Work Package selector |

C2 should ensure each project can adopt a **primary active** schema for:

- `purpose_role=element`
- `purpose_role=package`
- `purpose_role=work_package`

### In C2

| Item | In C2 |
|------|-------|
| Project schemas + nodes for demo/pilot | Yes |
| Primary active adoptions per role | Yes |
| Django admin as creation path | Yes |
| Optional idempotent seed helper | Optional (recommended for repeatability) |
| Product UI beyond admin | **No** |
| Quantities integration | **No** |
| Assignments / mappings / rules | **No** |
| F2 / F3 changes | **No** |
| Migrations (new fields) | **Avoid** — reuse C1 models |

---

## 4. Out of scope (explicit)

Do **not** implement in C2:

- `ClassificationAssignment`
- `ClassificationMapping`
- `ClassificationRule`
- Quantities schema-backed selectors or session metadata
- Changes to `qty-prep-export-v1`
- F2 / F3 model or service changes
- Ask / Modify / writeback / `ModificationProposal`
- IFC writeback / `SET_CLASSIFICATION`
- FM bridge or migration of facilities taxonomy
- Official Uniclass / OmniClass / MasterFormat packs or certification claims
- BOQ / cost / rates / EVM / QS / 5D readiness behavior
- F4 schedule alignment
- AI / ML suggestions
- UI rename of Package / BOQ Mapping (deferred to C3)

---

## 5. Recommended C2 approach

**Locked recommendation:**

1. Start **admin / data-first** (schemas, nodes, adoptions via Django admin).
2. Add a **minimal idempotent pilot / demo seed service** only if needed for repeatable testing and founder evidence.
3. **No public UI** in C2.
4. **No Quantities integration** in C2.
5. **No assignments** in C2.

### Data creation options

| Option | Description | When |
|--------|-------------|------|
| **A — Admin / manual only** | Founder/admin creates schemas/nodes/adoptions in Django admin; no new code except maybe tests/docs | Acceptable for one-off pilot |
| **B — Seed helper service** | Idempotent service creates pilot demo schemas/nodes/adoptions; tests/evidence only; no public UI; no automatic production seed | **Recommended** for repeatability |

**Locked preference:** Option B for demo / internal pilot with clear demo naming. Option A remains valid if founder prefers zero new code.

---

## 6. Demo schema design for pilot

Use **small custom packs**, not official standards.

These are **internal demo / project preparation** classifications only.  
They are **not** official Uniclass / OmniClass / MasterFormat / BOQ codes.

### A. Element Classification schema

| Field | Value |
|-------|-------|
| key | `nbkch-demo-elements` |
| purpose | `internal_element` |
| purpose_role (adoption) | `element` |

| Code | Label |
|------|-------|
| `EL-DEMO-WALL` | Wall elements |
| `EL-DEMO-BEAM` | Beam elements |
| `EL-DEMO-COLUMN` | Column elements |
| `EL-DEMO-SLAB` | Slab elements |
| `EL-DEMO-DOOR` | Door elements |
| `EL-DEMO-PIPE` | Pipe segments |
| `EL-DEMO-GENERIC` | Other model elements |

### B. Package Mapping schema

| Field | Value |
|-------|-------|
| key | `nbkch-demo-packages` |
| purpose | `package` |
| purpose_role (adoption) | `package` |

| Code | Label |
|------|-------|
| `PKG-DEMO-STRUCTURE` | Structural works |
| `PKG-DEMO-ARCHITECTURE` | Architectural works |
| `PKG-DEMO-MEP` | MEP works |
| `PKG-DEMO-COORDINATION` | Coordination / unresolved package |

### C. Work Package schema

| Field | Value |
|-------|-------|
| key | `nbkch-demo-work-packages` |
| purpose | `work_package` |
| purpose_role (adoption) | `work_package` |

| Code | Label |
|------|-------|
| `WP-DEMO-BASEMENT-Z1` | Basement Zone 1 works |
| `WP-DEMO-BASEMENT-Z2` | Basement Zone 2 works |
| `WP-DEMO-COORDINATION` | Coordination / pending work package |
| `WP-DEMO-UNASSIGNED` | Unassigned work package |

### Demo pack constraints

- `is_official_claim` must remain **false**
- Project-scoped schemas only (not global official packs)
- Origin: `user_defined` or an imported / demo-safe origin if available on C1 models
- Keep node counts small (order of tens, not thousands)

---

## 7. Seed helper boundaries (if implemented later)

If Option B is implemented:

| Rule | Requirement |
|------|-------------|
| Idempotency | Re-running creates or reuses; does not duplicate |
| No silent overwrite | Must not overwrite user-edited active schemas silently |
| Project scope | Project-scoped schemas only |
| Official claim | `is_official_claim=false` |
| Origin | `user_defined` or demo-safe imported origin |
| Primary adoption | Adopt as primary active **only if** no existing primary active schema for that role; otherwise fail with clear conflict |
| Return value | Summary of created / reused / conflict |
| Isolation | Must not touch Quantities, F2, or F3 |
| No assignments | Must not create row / entity assignments |

Seed helper is for **tests and founder evidence**, not automatic production seeding.

---

## 8. Relationship to C3

C3 (later; **not** this slice) will:

- Read project primary active schemas by `purpose_role`
- Provide schema-backed selectors in Quantities when `source_intent=manual_field`
- Store session metadata **additively**:
  - `code`
  - `label`
  - `schema_id`
  - `schema_key`
  - `node_id`
  - `origin=manual_session_schema_node`
- Keep fallback free-text if no schema exists
- **Not** persist assignments yet (C3.1 later)

**C2 must not implement C3 behavior.**

---

## 9. Relationship to F2 / F3

- **C2 alone does not change F2 / F3.**
- After C3:
  - F2 may snapshot schema / node metadata additively
  - F3 may count schema-backed session values as filled with stronger provenance than free text, still **not approved**
- C2 must **not** alter fived models or services now

---

## 10. Naming and boundary (locked)

| Topic | Locked rule |
|-------|-------------|
| Package / BOQ Mapping UI label | Rename to **Package Mapping** in **C3**, not C2 |
| Internal key | Keep `package_boq_mapping` for Quantities contract compatibility |
| BOQ / cost-code | Reserved for later `purpose_role=boq_cost_later` |
| Forbidden wording | No “BOQ ready”, “cost ready”, “5D ready”, “approved”, “certified” |
| Package vs work package | Project-specific by default (per C1) |
| Package Mapping vs BOQ | Separate; Package Mapping is preparation grouping, not cost estimate |

---

## 11. Implementation checklist (later C2 — not authorized by this doc alone)

Potential files when implementation is approved:

| Path | Role |
|------|------|
| `docs/classification/C2_PROJECT_CLASSIFICATION_SCHEMAS_SPEC.md` | This locked spec |
| `src/classification/services/project_schema_seed.py` | Optional idempotent seed helper |
| `src/classification/tests/test_project_schema_seed_c2.py` | Optional tests |

Explicit non-goals for that implementation:

- No migrations expected unless adding new fields (**avoid**)
- No Quantities / fived / writeback changes
- No public UI
- No `ClassificationAssignment` / `ClassificationMapping` / `ClassificationRule`

---

## 12. Tests checklist (later C2)

When seed helper / C2 data path is implemented, tests should cover:

- Pilot demo schemas / nodes / adoptions created or reused
- Primary active adoption per role (`element`, `package`, `work_package`)
- Idempotency
- Conflict behavior when primary already exists
- `is_official_claim` remains false
- No assignments / mappings / rules created
- No Quantities / export / F2 / F3 behavior changed
- No writeback / `ModificationProposal`
- No BOQ / cost / 5D / EVM claims

---

## 13. Founder decisions locked

1. C2 starts with **small custom demo / project schemas**, not official standards.
2. **Package** and **Work Package** are project-specific.
3. **Package Mapping** is separate from BOQ / cost-code.
4. C2 does **not** implement assignments.
5. C2 does **not** integrate Quantities.
6. C3 remains **session-only** first.
7. C3.1 persisted assignments **wait**.
8. C3 should happen **before F4** schedule alignment.
9. Keep work local; **do not push** until the full 5D / classification workstream is ready and founder explicitly approves.
10. UI rename Package / BOQ Mapping → Package Mapping belongs to **C3**, not C2.

---

## 14. Acceptance criteria (for later implementation review)

C2 implementation is complete when:

1. Pilot (or target) project can have primary active schemas for element, package, and work_package.
2. Demo nodes exist with clear `*-DEMO-*` codes and non-official claim.
3. Seed path (if used) is idempotent and conflict-safe.
4. No Quantities, fived, writeback, or export contract changes.
5. No assignment / mapping / rule models introduced.
6. Tests (if seed helper exists) pass the checklist in §12.
7. Docs remain the product contract; no “BOQ / 5D / certified” wording in seed summaries or help text.

---

## 15. Next step after this lock

**Ready for C2 implementation planning** (admin/data and/or Option B seed helper), still without Quantities/C3 wiring until a separate C3 locked spec is approved.
