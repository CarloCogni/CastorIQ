# 5D-MAP-BIG-2 — Package + Work Package Mapping Proof (Locked Spec)

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `50185b1ac259e3bd0918963dcfeea61b560e5f7a`  
**Depends on:** MAP-BIG-1 batch mapping (`5D_MAP_BIG_1_PRACTICAL_MAPPING_WORKFLOW_SPEC.md`)  
**This document does not authorize:** push, PR, GitHub, migrations, cost/rates/BOQ/EVM, Excel, Ask-to-5D, writeback, S2 contract change, or deletion of FOUNDER-DEMO / MAP-BIG-1 snapshots.

---

## 1. Problem

MAP-BIG-1 proved **classification** batch mapping only.  
FOUNDER-DEMO-MAP-BIG-1-v1 showed classification ~47/50 but **package and work-package rollups remained 0/0**. Full three-dimension schema mapping is therefore not proven.

Root cause: demo applied `classification_code` only. Services/UI already support package and work package.

## 2. Target outcome

A new local snapshot **FOUNDER-DEMO-MAP-BIG-2-v1** proves:

- classification groups > 0  
- package groups > 0  
- work package groups > 0  

Expected pilot targets:

| Dimension | Target |
|-----------|--------|
| Classification mapped | ~47/50 |
| Package mapped | ~47/50 (same Wall/Beam/Column/Slab set) |
| Work package mapped | ~47/50 |
| Rollup groups | no longer `4 / 0 / 0` — aim **4 / 2 / 1** |
| Unit | remains **Unit not resolved** (honest) |

### Recommended mapping story

**Classification**

- IfcBeam → EL-DEMO-BEAM  
- IfcWall → EL-DEMO-WALL  
- IfcColumn → EL-DEMO-COLUMN  
- IfcSlab → EL-DEMO-SLAB  

**Package**

- IfcBeam / IfcColumn / IfcSlab → PKG-DEMO-STRUCTURE  
- IfcWall → PKG-DEMO-ARCHITECTURE  
- Do not force Stair / StairFlight / Proxy unless semantically acceptable  

**Work package**

- Mapped structural/architecture rows → WP-DEMO-BASEMENT-Z1 if acceptable for demo  
- Leave uncertain rows unmapped  

Do **not** delete FOUNDER-DEMO or MAP-BIG-1.

## 3. UX safety improvements (small / low-risk)

1. **Preview-required Apply (client):** Apply disabled until a successful Preview; changing row selection or target selects invalidates preview and disables Apply again. Server still validates.  
2. **Post-apply freeze reminder:** Toast: “Session mapping updated. Freeze a new snapshot to review updated 5D coverage.”  
3. **Entry helper:** Near 5D Review entry: “Mappings changed? Freeze a new snapshot before opening the review.” / “Fresh mappings need a new freeze before they appear in 5D Review.”  
4. **Preview clarity:** Proposed pills only for fields being set; unchanged = omit or “Leave unchanged”; no raw origin strings in main preview.  

Do not add a full freeze workflow inside the modal.

## 4. Non-goals

No cost/rates, BOQ/EVM/payment, Excel import, Ask automation, writeback/approval, ClassificationAssignment model, migrations (unless reported), GitHub/push/PR, S2 payload contract change, QTOCache/QTOExportView changes.

## 5. Acceptance criteria

- [ ] Batch preview supports package and work-package fields  
- [ ] Batch apply can set package and work-package for multiple rows  
- [ ] Empty fields leave unchanged  
- [ ] Preview shows current → new for changed fields  
- [ ] Apply gated after preview (client) or documented if not  
- [ ] New snapshot shows non-zero package and work-package rollups  
- [ ] Old snapshots intact  
- [ ] No S2 contract change  
- [ ] No QTO/export/writeback behavior change  

## 6. Local commits

1. `docs(fived): define package and work mapping proof`  
2. `chore(takeoff): make batch mapping apply safer`  
3. Feature/demo evidence remains untracked unless requested  
