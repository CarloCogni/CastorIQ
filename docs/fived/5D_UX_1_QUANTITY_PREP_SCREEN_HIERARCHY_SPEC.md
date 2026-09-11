# 5D-UX-1 — Quantity Preparation UX System & Screen Hierarchy Spec

**Status:** Spec only (no product implementation in this commit)  
**Applies to:** Quantities (5D Quantity Preparation) and 5D Quantity Review  
**Non-goals:** New product features, backend contract changes, cost/rates/BOQ/EVM/writeback, migrations

---

## 1. Product story

Castor helps the user build a **reviewed 5D Quantity Preparation Model** from IFC evidence. The user:

1. Discovers available model data  
2. Chooses sources / configuration  
3. Maps rows to Castor schema  
4. Confirms units  
5. Freezes a snapshot  
6. Reviews the result  

Visible UI should answer, in order: **what am I doing → what do I decide → what is next**.

---

## 2. Page hierarchy

### A. Page header

- **Title:** 5D Quantity Preparation  
- **Subtitle:** Build a reviewed, schema-mapped quantity snapshot from IFC evidence.  
- **Help pill:** `?` (existing help modal; expand with journey + distinctions)  
- **Status chips only** (compact, not essays):
  - IFC evidence (present / missing)
  - Mapping session (clean / pending changes)
  - Units (confirmed / unresolved)
  - Snapshot (latest label or “none yet”)

Secondary header actions (not primary): Open Model, Open IFC Elements.

### B. Workflow rail / step indicator

1. Discover  
2. Configure  
3. Map  
4. Confirm Units  
5. Freeze  
6. Review  

Rail is navigational/orientation only in V1 of the UX pass (scroll or collapse sections). It must not invent new backend state machines.

### C. Main workspace

**Main (left / center):**

- Filters toolbar (collapsed discover controls)  
- Selected-row actions  
- Preparation table  
- Pagination  

**Support (right, ≥lg; stacked under table on small):**

- Semantic source readiness (compact)  
- Unit confirmation (compact)  
- Freeze / next-action card  

### D. Advanced / help

Move out of primary path:

- Long non-claims (not cost / BOQ / writeback / Ask / Modify)  
- Contract IDs, internal source keys  
- Raw IFC inventory, visual summary charts, insights essays  
- Legacy QTO cache tools (already advanced)  
- Multi-paragraph classification-layer essays  

---

## 3. Component rules

| Component | Rule |
|-----------|------|
| Page header | One title, one subtitle, chips, help; no multi-paragraph boundary block |
| Section card | Shared `.qty-card` token: 1 border, 1 radius, consistent padding; no cards-in-cards without accordion |
| Section title | One style (`section-title`); nested groups use `section-subtitle` |
| Helper text | Max one short line under section title; else help/advanced |
| Status chip | Shared palette + vocabulary (see §6) |
| Warning card | Freeze pending, mixed selection, conflicts — one warning pattern |
| Empty / unavailable | “Missing in this IFC export” / “Not indexed yet” — never “unsupported” for Zone |
| Primary button | One clear primary per step context |
| Secondary button | Outline / default for supporting actions |
| Subtle / link | Help, Advanced, Continue editing, Back |
| Table toolbar | Single band: selection count + Map + Clear + filter summary |
| Right-side panel | Sticky support stack on large screens |
| Advanced details | `<details>` or help modal; default collapsed |

---

## 4. Typography rules

| Level | Role | Guidance |
|-------|------|----------|
| Page title | `h1`/`h5` working title | One per page |
| Section title | Strong, consistent size | No `h6.small` vs `h6` drift |
| Body text | Decisions and table content | Default body |
| Helper text | One short clarifying line | Muted; not multi-sentence |
| Meta text | Counts, timestamps, chip labels | Smallest readable |
| Badge / chip text | Status | Fixed small; never wrap essays |

No random text sizes. No large helper notes. No tiny critical instructions.

---

## 5. Button hierarchy

**Primary (context-dependent, visually clear):**

- Map selected rows (Map step)  
- Freeze updated 5D snapshot (Freeze step / pending banner)  
- Open 5D Review (when a current snapshot is the next step)  
- Confirm declared units (Confirm Units step focus)  

**Secondary:**

- Clear filters  
- Continue editing  
- Preview (batch)  
- Generate preparation model  
- Save/load draft  

**Subtle:**

- Help  
- Advanced details  
- Open Model / Open IFC Elements  
- Pagination  

Do not make navigation-away actions the strongest button on the page.

---

## 6. Status language

Use consistently:

- Available  
- Missing in this IFC export  
- Present but not indexed  
- User mapping required  
- Confirmed  
- Unresolved  
- Warning  

Avoid in primary UI: Unsupported, Failed, Unknown (unless truly unknown), developer-only labels (`classref:ifc` as sole label — prefer human label + optional advanced key).

---

## 7. Copy reduction rules

**Keep visible:**

- What the user needs to decide  
- Current status  
- Next action  

**Move to help / advanced:**

- Exhaustive non-claims lists  
- Long technical explanations  
- Contract / slice IDs  
- Source key internals  
- Future Ask/Modify/writeback bridge narratives  

One short product boundary may remain near help (“Preparation snapshot — not cost or BOQ”).

---

## 8. Critical distinction rules

UI must keep visually and verbally separate:

1. **Existing IFC evidence** (incl. true Classification References)  
2. **Authoring-tool properties** (OmniClass, Assembly Code, …)  
3. **Castor schema mapping** (target nodes)  
4. **User semantic profile / readiness** (source choices / gaps)  
5. **Unit**  
6. **Measurement Basis**  
7. **Total Quantity**  

Never merge unit into total quantity display. Never present ClassRef as Castor mapping.

---

## 9. Acceptance criteria (implementation pass)

- [ ] Screen reads as one product, not stitched panels  
- [ ] User can identify next action within ~5 seconds  
- [ ] Primary actions follow §5  
- [ ] Status language follows §6  
- [ ] Notes reduced; long copy in help/advanced  
- [ ] Quantity / Unit / Measurement Basis remain separate  
- [ ] No feature regression (filters, map, units, freeze, review)  
- [ ] No cost / rates / BOQ / writeback claims in primary UI  

---

## 10. Out of scope for UX-1 implementation

- New readiness fields or profile persistence  
- Zone indexing (SEM-4B)  
- Auto-crosswalk  
- Backend data contract changes  
- Migrations  
