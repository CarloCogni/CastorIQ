# SEM-4 — Zone / Classification Evidence Indexing + User Semantic Mapping Profile

**Status:** Spec revised after product correction (pre-SEM-4A)
**Baseline:** `main` @ `7c0f132`
**Depends on:** IFC-SEM-1/2/3, UNIT-2, guided freeze
**Mode:** Spec only until SEM-4A implementation is explicitly approved

**Product correction:** SEM-4 is **not** a pilot-IFC experiment. Castor’s 5D discovery must be **user-configurable** and **IFC-schema-aware**. An uploaded IFC is one export instance — it may be incomplete, wrongly exported, missing zones/classification relationships, or use custom parameters instead of native IFC entities. Absence in the current file is a **data/export gap**, not product irrelevance.

---

## A. Problem

Today Quantities can:

- Filter authoring-tool properties (OmniClass, Assembly Code, Project Level, …)
- Use spatial storey/container when indexed
- Map rows to Castor target schema nodes (classification / package / work package)

But Castor does **not** yet:

1. Index **true IFC classification associations** when the export contains them
2. Index **IfcZone membership** when the export contains it
3. Offer a durable **User Semantic Mapping Profile** that lets the user choose *which discovered evidence* feeds each 5D preparation field
4. Surface **readiness gaps** when a chosen/required 5D field has no usable source in this IFC instance

SEM-3 already shows honest unavailable stubs for Zone and IFC Classification Reference. Pilot IBS happens to lack zones and to contain Uniformat `B10` associations — that is **instance evidence**, not the product contract.

---

## B. User story

As a BIM/5D user, I want to open **any** IFC model, see what semantic evidence Castor discovered, choose which sources define Level / Zone / Classification evidence / Package / Work Package / Unit / Measurement Basis for my 5D preparation model, and see clear readiness gaps for missing data — without Castor inventing zones or auto-writing IFC classification into Castor schema nodes.

---

## C. Four-layer product model (non-negotiable)

| Layer | Meaning | Example |
|-------|---------|---------|
| **1. IFC schema capability** | What IFC *can* represent | `IfcZone`, `IfcRelAssociatesClassification`, storeys, psets, QTO |
| **2. Uploaded IFC instance evidence** | What *this export* actually contains | Pilot: no zones; Uniformat B10 assoc present; OmniClass in Identity Data |
| **3. User Semantic Mapping Profile** | How the user binds discovered fields into 5D prep roles | Zone source = `prop:Identity Data.Zone` *or* `struct:zone` *or* unset |
| **4. Castor schema mapping** | User’s **target** classification/package/work package nodes for this 5D model | Map rows → project schema nodes (session → freeze) |

**Rules:**

- Do not hardcode that Zone must only come from `IfcZone`.
- Do not hardcode that Classification evidence must only come from `IfcClassificationReference`.
- Do not hardcode that Level must only come from spatial storey.
- Do not hardcode that Work Package / Package must only come from one platform-defined field.
- Do not treat missing instance evidence as “feature not needed.”
- Do not fake missing zones.
- Do not auto-map IFC classification refs → Castor schema nodes.

---

## D. What “true zone” means (schema capability)

**Native IFC zone capability** = `IfcZone` membership via `IfcRelAssignsToGroup`.

Also allowed as **user-chosen** zone *sources* when native zones are absent or unused:

- Pset / Identity Data / custom properties discovered on the instance (e.g. `Pset.*.Zone`, `Identity Data.Zone`)
- Other group-like evidence only if explicitly selected by the user (no silent invention)

`IfcSpace` remains spatial structure — not auto-aliased as Zone unless the user deliberately maps a space-derived field (out of SEM-4A default).

---

## E. What “true IFC classification reference” means (schema capability)

**Native IFC classification capability** = `IfcClassification` / `IfcClassificationReference` via `IfcRelAssociatesClassification`.

Also allowed as **user-chosen** classification-*evidence* sources:

- Authoring-tool properties already indexed (OmniClass Title/Number, Assembly Code, …)
- Any user-selected discovered property column

These are **evidence sources for filtering/columns**, not automatic Castor target schema assignments.

---

## F. Distinction table (expanded)

| Concept | Layer | Role in 5D prep |
|---------|-------|-----------------|
| True IFC classification reference | Schema + instance | Discoverable evidence; optional profile source |
| Authoring-tool property (OmniClass / Assembly Code / …) | Instance properties | Discoverable evidence; optional profile source |
| User Semantic Mapping Profile | User config | Chooses which evidence feeds Level/Zone/Class evidence/… |
| Castor schema mapping | Target registry | User maps rows to project nodes for the 5D model |
| Readiness gap | Instance vs profile | Required/chosen source empty → show gap, don’t invent data |

---

## G. User Semantic Mapping Profile (contract)

A project-scoped (or prep-config-scoped) profile binds **5D semantic roles** to **discovered sources**.

### Example role → source choices

| 5D role | Allowed source kinds (illustrative) |
|---------|-------------------------------------|
| Level | `spatial:storey` **or** `prop:Identity Data.Project Level` **or** user-selected property **or** unset |
| Zone | `struct:zone` (IfcZone membership) **or** `prop:…Zone…` **or** user-selected property **or** unset |
| Classification evidence | `classref:ifc` **or** OmniClass/Assembly props **or** user-selected property **or** unset |
| Package | Castor schema mapping field **or** user-selected property **or** unset |
| Work Package | Castor schema mapping field **or** user-selected property **or** unset |
| Unit | IFC `project_units` confirmed by user (UNIT-2) |
| Measurement Basis | Selected QTO / basis rules (existing prep) |

### Profile behaviors

- User can change sources when the IFC export uses custom parameters.
- Profile does not invent values.
- If chosen source has no values on this instance → **readiness gap**.
- Multiple candidates may be listed as “available sources”; only the selected binding drives prep enrichment for that role.
- Castor schema mapping remains the **target** layer for classification/package/work package *assignments*, independent of which evidence column the user uses to *find* rows.

### Persistence (implementation later — choose without locking migrations in SEM-4A discovery)

Prefer extending existing **preparation configuration draft / session** mechanisms if possible. New DB model only if unavoidable (report before migrating).

---

## H. SEM-4A scope (first implementation slice)

SEM-4A **must** include:

| ID | Requirement |
|----|-------------|
| **A** | Index true IFC classification references when present (`IfcRelAssociatesClassification` → queryable evidence). |
| **B** | Keep OmniClass / Assembly Code as authoring-tool properties (distinct from true refs). |
| **C** | Specify (and begin implementing if approved) a **user-configurable semantic source mapping contract** for 5D fields (profile). |
| **D** | Show missing required/chosen 5D fields as **readiness gaps**. |
| **E** | Do **not** fake missing zones. |
| **F** | Do **not** auto-map IFC classification refs to Castor schema nodes. |
| **G** | Do **not** implement writeback now; keep the **future bridge** documented: missing data → Ask/Modify proposal → approval → IFC writeback → re-index → 5D update. |

### SEM-4A recommended delivery shape

1. **Indexer:** materialize ClassRef evidence (properties denorm preferred; no migration if possible).
2. **Discovery UI:** show available sources for Classification evidence (true ref + OmniClass + Assembly + other props).
3. **Profile contract:** Level / Zone / Classification evidence (minimum); Package / Work Package / Unit / Basis bindings documented and stubbed or partially wired if low-risk.
4. **Readiness:** gaps when profile source unset or empty on instance.
5. **Zone:** if IfcZone membership not indexed yet, keep honest gap; do not synthesize zones from spaces. Zone property sources may still be selectable if discovered in properties.

### SEM-4B (follow-on)

Index native `IfcZone` membership (`struct:zone`) so it appears as a first-class available source when present in *any* IFC upload.

---

## I. Data model / indexing options

1. **Denormalize ClassRef (and later Zone) into `IFCEntity.properties`** — preferred indexing path; reparse required.
2. New JSON / relational tables — only if multi-value query needs force it (report migrations first).
3. Live IfcOpenShell on Quantities load — **rejected** (performance).

Profile storage is separate from ClassRef indexing and may reuse prep configuration drafts.

---

## J. Migration / no-migration

| Concern | V1 preference |
|---------|----------------|
| ClassRef indexing | No migration (properties denorm) + reparse |
| Semantic profile | Prefer session/draft JSON; migration only if proven necessary |
| Live IFC scan | Forbidden |

---

## K. Indexing / parser impact

**Required for A.** Extend parse pipeline:

1. Walk `IfcRelAssociatesClassification` → write reserved `ClassRef.*` (or equivalent) on related products (and document type handling).
2. Later (4B): Walk zone assignments → `Zone.*`.
3. Expose discovered keys to SEM source picker / profile.
4. Future writeback path re-indexes after approved IFC mutation (not SEM-4A).

---

## L. Quantities UI impact

- Classification evidence: true IFC ref available when indexed; OmniClass/Assembly remain.
- Semantic profile panel (or prep-config section): choose sources per 5D role.
- Readiness strip: missing Level/Zone/Classification evidence/Package/Work Package/Unit/Basis per profile rules.
- Batch mapping / freeze / units unchanged in intent.
- Help copy: four-layer model + “this IFC export may be incomplete.”

---

## M. Prep-row aggregation rules

Unchanged from SEM-3:

- Single value → show
- Multiple distinct → `Mixed values` (+ optional count)
- Missing → `—`
- Never dump raw JSON

ClassRef display preference: `System / Identification` (e.g. `Uniformat / B10`).

---

## N. Performance boundary

- Quantities avg &lt; 2.5s
- Index at parse/reparse only
- Single SEM enrichment scan
- No live IFC open in Quantities

---

## O. Non-goals (SEM-4A)

- Cost / rates / BOQ / EVM / payment
- Ask / Excel automation (bridge documented only)
- Writeback / Modify approval execution
- Auto-map ClassRef → Castor schema nodes
- Faking zones or inventing classification associations
- Hardcoding product semantics to the IBS pilot export
- Replacing Castor schema mapping with IFC refs
- S2 contract change unless separately approved

---

## P. Future bridge (document only — do not build in SEM-4A)

```
readiness gap (missing zone / class assoc / …)
  → Ask or Modify proposal (future)
  → human approval
  → IFC writeback
  → re-index / reparse
  → Quantities + 5D prep update
```

Guardian/writeback remain out of SEM-4A scope; SEM-4A only leaves the gap visible and the bridge intentional.

---

## Q. Implementation recommendation

1. Update specs/contracts (this document) — **done in correction pass**.
2. **SEM-4A:** ClassRef indexing + keep OmniClass/Assembly + semantic profile contract + readiness gaps + no fake zones + no auto-map + no writeback.
3. **SEM-4B:** Native IfcZone membership indexing as an available source.
4. Commit/push only when founder explicitly requests.

---

## R. Acceptance criteria (SEM-4A later build)

- True IFC classification references indexed when present; filterable/columnable as evidence.
- OmniClass / Assembly Code remain distinct authoring properties.
- User can configure (at least) Classification evidence / Level / Zone **source bindings** via Semantic Mapping Profile contract (full UI polish may span slices).
- Missing chosen/required sources appear as readiness gaps — not silent success.
- No invented zones on IFCs that lack zone evidence.
- No auto-map from IFC ClassRef → Castor schema nodes.
- No writeback executed.
- Pilot absence of zones treated as export gap, not “Zone unsupported forever.”
- Performance gate holds; no migrations unless pre-approved.
- Help text explains the four layers.

---

## Discovery evidence

`.df_sem_4_zone_classification_ref_discovery/` — pilot probes remain valid as **instance evidence**, not as the product ceiling.
See also `product_correction_user_semantic_profile.md` in that folder.
