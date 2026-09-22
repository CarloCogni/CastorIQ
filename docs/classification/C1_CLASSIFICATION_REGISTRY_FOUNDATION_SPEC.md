# C1 — Classification Registry Foundation (Locked Spec)

**Status:** Locked for implementation planning  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `1a7089e780ac142aec394b2a3d5d47cd66d792d3`  
**Document type:** Product / architecture contract (not an inventory of runtime files)  
**This document does not authorize:** code changes, migrations, UI, Quantities/FM/Ask/Modify/writeback work, commit, or push by itself.

---

## 1. Locked founder decisions

1. **No mandatory external classification standard** by default.
2. Castor supports **optional / imported** schemas.
3. **Built-in official packs later only** after licensing / verification.
4. **MVP = custom / import first.**
5. **No claim** that Uniclass / OmniClass / MasterFormat are official built-in packs unless verified later.
6. **Package** and **Work Package** schemas are **project-specific by default** (breakdown varies by project, contract, zone, contractor, schedule).
7. Rename / split Quantities wording: use **Package Mapping** now; keep **BOQ / Cost Mapping** as a **later separate purpose**.
8. **C1 does not persist assignments.**
9. **C1 does not integrate Quantities UI.**
10. **C1 does not migrate FM taxonomy.** Keep facilities working; bridge later.
11. **C1 does not touch Modify / writeback.**
12. **C1 does not add** AI, rules, crosswalk, or document evidence.
13. **No approval workflow in C1.** Later statuses may exist in model planning only; no operational approval UI yet.
14. **Schema edition / version required.** Published / active schema codes must **not** be silently overwritten.
15. **No silent overwrite** on conflicts; conflicts go to a **future** review queue (not C1).
16. **Multilingual** is not an MVP blocker; labels / metadata may support it later.
17. Exports (future) are **evidence**, not certification — same boundary language as Quantities export.
18. **C1 supports registry + nodes + project adoption only.**
19. Assignment grain (later, not C1): support both `qty_prep_row` and IFC entity / type; **do not implement assignment in C1.**

---

## 2. C1 scope

C1 delivers the **Classification Registry Foundation** only:

| Item | In C1 |
|------|-------|
| New Django app `classification` | Yes (implementation step) |
| `ClassificationSchema` | Yes |
| `ClassificationNode` | Yes |
| `ProjectClassificationSchema` | Yes |
| Django admin registration | Yes |
| Migrations | Later implementation step (not this doc) |
| Tests | Later implementation step (not this doc) |
| Product UI beyond admin | **No** |
| Quantities changes | **No** |
| Facilities / FM changes | **No** |
| Ask / Modify / writeback changes | **No** |

C1 is a **data foundation**. It does not change user-facing Quantities, FM, Ask, or Modify flows.

---

## 3. Out of scope (explicit)

Do **not** implement in C1:

- `ClassificationAssignment`
- `ClassificationMapping`
- `ClassificationRule`
- `ClassificationImportBatch` (optional later; CSV import UI/service is C2+)
- Quantities schema-backed selectors
- Quantity preparation row assignment
- IFC entity / type assignment
- IFC writeback / `SET_CLASSIFICATION` from this app
- FM bridge or migration of `facilities.Classification*`
- Ask / RAG changes
- Document chunk tagging
- Excel / BOQ ingest
- Cost / EVM / 5D / QS certification behavior
- Approval workflow / operational review queue UI
- Hidden AI classification
- Changes to `qty-prep-export-v1`

---

## 4. Proposed C1 model contract

Contract version for this foundation: `classification-registry-c1-v1`.

### 4.1 `ClassificationSchema`

**Purpose:** Named classification schema (taxonomy / scheme) available on the platform or within a project.

| Field | Notes |
|-------|--------|
| `scope` | `platform` \| `project` |
| `project` | Nullable FK; required when `scope=project`; null when `scope=platform` |
| `key` | Stable slug within scope/project |
| `name` | Human-readable name |
| `edition` | Required version / edition marker (versioning decision) |
| `purpose` | Schema purpose enum (see §6) |
| `origin` | e.g. `imported` \| `user_defined` \| `forked` \| `builtin_template` (builtin reserved; not used as official pack until verified) |
| `source_uri` | Optional |
| `status` | e.g. `draft` \| `active` \| `deprecated` \| `archived` |
| `is_editable` | Whether users may edit nodes |
| `is_official_claim` | **Default `false`** |
| `contract_version` | e.g. `classification-registry-c1-v1` |
| `created_by` | Nullable FK to user if compatible with project patterns |
| timestamps | Via project timestamp base / `TimestampedModel` as applicable |

**Relationships:** 1 → N `ClassificationNode`; N projects via `ProjectClassificationSchema`.

**Versioning:** Prefer new edition / fork over silent in-place rewrite of published/active codes.

**Not in C1 model:** assignment FKs, mapping FKs, AI metadata, cost rates, writeback flags, approval engine.

### 4.2 `ClassificationNode`

**Purpose:** One code / node within a schema.

| Field | Notes |
|-------|--------|
| `schema` | FK → `ClassificationSchema` |
| `code` | Code string as used in the taxonomy |
| `label` | Human-readable label |
| `description` | Optional |
| `parent` | Nullable self-FK; must belong to **same** schema |
| `path` | Optional denormalized path |
| `depth` | Integer depth |
| `sort_order` | Integer |
| `status` | Align with schema lifecycle needs (`draft` / `active` / …) |
| `external_id` | Optional |
| `metadata` | JSON (sparse; future locale labels allowed here later) |

**Not in C1:** embeddings, assignment side effects, rich property sets required for QS/cost.

### 4.3 `ProjectClassificationSchema`

**Purpose:** Project adopts a schema for a **purpose role** (element / package / work package / …).

| Field | Notes |
|-------|--------|
| `project` | FK |
| `schema` | FK → `ClassificationSchema` |
| `purpose_role` | Adoption role enum (see §6) |
| `is_primary` | Boolean |
| `priority` | Integer |
| `status` | e.g. `active` \| `inactive` |
| `adopted_by` | Nullable FK to user if compatible |
| `adopted_at` | Datetime |

**Not in C1:** forcing a default external standard; operational approval; Quantities wiring.

---

## 5. Required constraints

1. **`ClassificationSchema` uniqueness:** `(scope, project, key, edition)`  
   - Platform rows: `project` is null; uniqueness must still hold under the chosen Django/Postgres null-handling strategy.
2. **`ClassificationNode` uniqueness:** `(schema, code)`.
3. **Node parent** must belong to the **same schema** (validation + preferably DB-safe checks).
4. **`ProjectClassificationSchema` uniqueness:** `(project, purpose_role, schema)`.
5. **Only one primary active** schema per `(project, purpose_role)`  
   (constraint / validation when `is_primary=True` and status active).
6. **`is_official_claim` defaults to `false`.**
7. **No cascade behavior** should break existing projects unexpectedly  
   (prefer `PROTECT` or careful `CASCADE` only where orphan schemas/nodes are intentional; do not cascade into facilities, takeoff, IFC, or writeback tables — there are no such FKs in C1).

Edition / active codes: published or active node codes under an active schema must not be silently overwritten by import or admin edits without an explicit future policy (fork / new edition).

---

## 6. Purpose roles / enum guidance

### Schema `purpose` (on `ClassificationSchema`)

Suggested values (implementation may use TextChoices):

| Value | Meaning |
|-------|---------|
| `element` | Internal / project element classification |
| `package` | Package mapping taxonomy |
| `work_package` | Work package / WBS-style taxonomy |
| `document_metadata` | Document / CDE metadata classification |
| `asset_fm` | Asset / FM-oriented (bridge later; not migrating FM in C1) |
| `external_reference` | Optional external standard reference schema (imported; not “official” unless verified) |
| `custom` | Ad-hoc user/project schema |
| `boq_cost_later` | **Reserved / future** BOQ / cost mapping purpose |

**`boq_cost_later`:** reserved name only. Must **not** imply current BOQ generation, cost estimating, rates, QS certification, 5D, or EVM readiness. Do not wire Quantities or cost surfaces to this purpose in C1–C3 without a separate product decision.

### Adoption `purpose_role` (on `ProjectClassificationSchema`)

Align with schema purposes used for project binding. Minimum for future Quantities (C3, not C1):

- `element` → today’s “Classification Code” intent  
- `package` → **Package Mapping** (not “Package / BOQ Mapping”)  
- `work_package` → Work Package  

Other roles (`document_metadata`, `asset_fm`, `external_reference`, `custom`, `boq_cost_later`) may be adoptable for registry completeness but have **no product UI consumers in C1**.

Package and work package adoptions are **project-specific by default** (founder decision).

---

## 7. Safety boundaries

C1 (and any future classification export language) must preserve:

- **Not BOQ**
- **Not QS-certified**
- **Not a cost estimate**
- **Not 5D / EVM**
- **Not an approval workflow** (C1)
- **Not IFC writeback**
- **Not a Modify proposal**
- **Not an Ask / RAG replacement**
- **Not hidden AI classification**
- **Not an official external-standard claim** unless imported **and** verified (`is_official_claim` stays false by default)

Evidence language (when exports exist later): same family of boundary wording as Quantities preparation export (`BOUNDARY.txt` style) — evidence, not certification.

---

## 8. Integration notes

| System | C1 behavior |
|--------|-------------|
| **Quantities** | Unchanged. Keeps current session free-text / manual mapping (`manual_field`, session annotations). |
| **`qty-prep-export-v1`** | Remains unchanged in C1. |
| **Package label debt** | Product copy still may say “Package / BOQ Mapping” until a separate copy slice; C1 does not change Quantities templates. Future C3 uses **Package Mapping**. |
| **Facilities taxonomy** | Untouched. `facilities.Classification` / `ClassificationReference` / asset M2M keep working. |
| **FM bridge** | Planned later; not C1. |
| **Quantities schema-backed selection** | **C3**, not C1. Session-first; persisted assignments = **C3.1** separate decision. |
| **Assignments / mappings / rules / document evidence** | C4+ / later slices; not C1. |
| **Ask / Modify / writeback** | Not touched. |

---

## 9. C1 implementation checklist (later)

When founder authorizes implementation:

- [ ] Add Django app `classification`
- [ ] Implement `ClassificationSchema`, `ClassificationNode`, `ProjectClassificationSchema`
- [ ] Register models in Django admin
- [ ] Add app to `INSTALLED_APPS` / settings
- [ ] Run `makemigrations` / `migrate` (implementation step only)
- [ ] Add tests (see §10)
- [ ] **No** product UI beyond admin
- [ ] **No** Quantities changes
- [ ] **No** FM / facilities changes
- [ ] **No** Ask / Modify / writeback changes
- [ ] **No** assignment / mapping / rule models
- [ ] Do not stage evidence / smoke artifacts; commit only when founder asks

---

## 10. Tests checklist (later)

- [ ] Schema uniqueness `(scope, project, key, edition)`
- [ ] Node uniqueness `(schema, code)`
- [ ] Same-schema parent validation
- [ ] Project adoption uniqueness `(project, purpose_role, schema)`
- [ ] Single primary **active** schema per `(project, purpose_role)`
- [ ] Lifecycle / status values accepted
- [ ] `is_official_claim` defaults `false`
- [ ] Facilities regression: existing Classification* behavior untouched
- [ ] No Quantities / prep-export behavior change
- [ ] No writeback / Modify side effects

---

## 11. Slice map (context only)

| Slice | Scope |
|-------|--------|
| **C1** | Registry + nodes + project adoption (this spec) |
| **C2** | Project custom schemas + node import (CSV); still no assignment |
| **C3** | Quantities session schema-backed selectors + free-text fallback; export additive only |
| **C3.1** | Persisted assignments (explicit decision gate) |
| **C4+** | Rules preview, document evidence, crosswalk / conflicts |

---

## 12. Recommendation

This locked spec is **ready for C1 implementation** when the founder explicitly authorizes it.

Until then: **no models, no migrations, no product code, no commit, no push.**
