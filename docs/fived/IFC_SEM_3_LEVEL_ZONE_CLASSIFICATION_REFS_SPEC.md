# IFC-SEM-3 — Level / Zone and Classification Reference Semantics

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `3fad2e3500ad5c0135cb263fdd611f0d0cac7098`  
**Depends on:** IFC-SEM-1, IFC-SEM-2, UNIT-2  

---

## 1. User story

As a BIM/5D user, I want to filter quantity preparation rows by level, zone, and existing model classification references so I can map quantities to project schema more accurately.

---

## 2. Supported first version

Extend the existing IFC semantic filters panel (reuse SEM-2 column picker / filter / select / batch path).

### Model structure

| Field | Key | Source | Pilot note |
|-------|-----|--------|------------|
| Level / Storey (spatial) | `spatial:storey` | `IFCEntity.spatial_container` → storey name | Available; may be low-cardinality |
| Project Level (authoring) | `prop:Identity Data.Project Level` | Indexed properties | Preferred filter on pilot |
| Spatial container | `spatial:container` | Direct container name | Available when FK set |
| Zone | `struct:zone` | — | **Unavailable** honest state |

### Existing classification

| Field | Key | Source | Pilot note |
|-------|-----|--------|------------|
| IFC Classification Reference | `classref:ifc` | — | **Unavailable** until SEM-4 indexing |
| OmniClass Title | `prop:Identity Data.OmniClass Title` | Properties | Classification-like |
| OmniClass Number | `prop:Identity Data.OmniClass Number` | Properties | Classification-like |
| Assembly Code | `prop:Identity Data.Assembly Code` | Properties | Classification-like |

Do **not** confuse existing model classification evidence with Castor target schema mapping (`classification_code` / package / work package).

---

## 3. Data display rules (grouped prep rows)

- Single value → show value  
- Multiple nonempty values → `Mixed values`  
- Missing → `—`  
- Optional detail: distinct count (e.g. “3 levels”)  
- Never dump raw JSON  

---

## 4. UX

Panel sections:

1. **Core preparation fields** — existing SEM-1  
2. **IFC properties** — SEM-2 picker  
3. **Model structure** — curated add/filter + unavailable Zone helper  
4. **Existing classification** — curated classification-like columns + unavailable IFC class-ref helper  

Controls unchanged: add column · filter value · clear · select filtered rows · batch map.

Query params: reuse `sem_cols`, `semantic_field`, `semantic_value`. Allow `spatial:` keys in `sem_cols`.

---

## 5. Classification reference rules

| Concept | Meaning |
|---------|---------|
| Existing model classification | What the IFC/model already says (OmniClass, Assembly Code, …) |
| Castor schema mapping | What the user maps to for this 5D model |

No automatic crosswalk in SEM-3.

---

## 6. Data behavior

- Read-only discovery/enrichment  
- No DB writes except existing session mapping on Apply  
- Freeze/Review unchanged; no S2 contract change  
- No writeback / Modify / IFC mutation  
- No migrations  

---

## 7. Performance

- Single entity scan pass (extend SEM-2 `_scan_entities`)  
- `select_related("spatial_container__entity")` for storey names  
- No 6000-row DOM dump  
- Quantities avg &lt; 2.5s on pilot  

---

## 8. Non-goals

No cost/rates, unit changes, Excel import, Ask-to-5D, writeback, new IFC classification authoring, full relationship re-indexer, SCALE-1 pagination.

---

## 9. Acceptance criteria

- User can see Level/Storey (spatial) availability  
- User can see Zone availability or honest unavailable  
- User can see IFC classification-ref availability or honest unavailable  
- User can add/filter Project Level and classification-like fields when indexed  
- Select filtered rows → batch map still works  
- SEM-2 property picker and UNIT-2 unit columns remain intact  
- Old snapshots still open; no performance regression; no GitHub  

---

## 10. SEM-4 follow-ons

Index true `IfcRelAssociatesClassification`, zone assignments, richer spatial drill-down.
