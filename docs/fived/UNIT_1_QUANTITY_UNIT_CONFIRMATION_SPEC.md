# UNIT-1 — Quantity Unit Confirmation

**Status:** Spec locked for discovery; implementation not started  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at discovery:** `ed35a1b`  
**Depends on:** Quantity Preparation + F2 freeze + S3 Review unit honesty  
**Evidence:** `.df_unit_1_quantity_unit_confirmation/`  

---

## 1. User story

As a BIM/5D user, I want Castor to confirm quantity units from trustworthy sources (IFC UnitAssignment and/or my explicit confirmation) so that Quantities and 5D Quantity Review can show m³ / m² / mm / count with confidence — and keep “Unit not resolved” wherever the unit is unknown.

---

## 2. Current problem

- Prep rows and frozen F2 rows typically store `unit_basis = "model volume units"` (or empty when basis unresolved).
- Quantities and 5D Review correctly map that to **Unit not resolved**.
- This blocks founder confidence even when the pilot IFC already declares:

  - `VOLUMEUNIT = m³`
  - `AREAUNIT = m²`
  - `LENGTHUNIT = mm`

- `ModelQuantitiesService` does not read `IFCFile.project_units`.
- Numeric Qto values on entities have no per-value unit suffix.

Showing fake m³/m²/m is forbidden. Silent defaults from basis names are forbidden.

---

## 3. Allowed source hierarchy

1. **IFC unit assignment** if indexed and reliable (`IFCFile.project_units` from `IfcUnitAssignment`).
2. **Project-level confirmation by user** (per measure family: volume / area / length / count).
3. **Quantity-basis defaults only after confirmation** (write canonical tokens onto prep `unit_basis`).
4. **Never silent defaults** (do not infer m³ merely because basis is NetVolume).

---

## 4. Supported first implementation scope (future)

When implementation is approved:

- Discover declared units from completed project IFC file(s).
- Detect multi-file conflicts → keep unresolved + explain.
- Compact **Unit status** panel on Quantities; summary on 5D Review.
- Propose declared labels with source.
- User Confirm / Override / Leave unresolved per measure family.
- On confirm: set prep row `unit_basis` to canonical tokens (`m3`, `m2`, `mm`, `count`, …).
- Freeze/review reuse existing F2 `unit_basis` + existing display resolvers.
- Magnitude sanity check (advisory only) may warn if volumes look like mm³ scale.

Out of scope: cost/rates, BOQ/EVM/payment, writeback/Modify, Excel/Ask automation, QTOCache/export changes, numeric rescale, inventing metres for length when LENGTHUNIT is mm, migrations unless blocked and reported first, S2 payload shape change unless blocked and reported first.

---

## 5. UX concept

### Quantities — Unit status panel

Title: **Quantity units**  
Subtitle: Confirm model units before treating totals as m³ / m² / mm.

Show:

- Declared IFC units (volume / area / length / count)
- Current display status (resolved / unresolved)
- Source (IFC UnitAssignment / user confirm / override / conflict)
- Actions: Confirm declared · Override · Clear confirmation
- Honest empty/conflict states

### Prep table / basis rules

- Primary unit cells show SI/labels only when confirmed (recommended) or clearly badged if product later allows declared-as-display.
- Otherwise keep **Unit not resolved** / family-specific unresolved copy.
- Never show `model volume units` in primary chrome.

### 5D Quantity Review

- Headline unit status uses existing `mapped_netvolume_unit_status` path.
- After confirmed freeze: `m³` when `unit_basis` is `m3`/`m³`.
- Attention card remains until volume family confirmed for mapped NetVolume rows.

---

## 6. Data behavior

- Read `IFCFile.project_units` (already indexed).
- Session/request stores confirmation choices (no migration preferred).
- Prep rows keep `unit_basis` string; presentation helpers map to display.
- Freeze copies `unit_basis` into `FiveDModelRow` as today.
- No cost calculation, rate multiplication, or BOQ.
- No IFC mutation / writeback proposals.
- Do not change numeric quantity values when confirming a label (label confirmation ≠ unit conversion).

---

## 7. Recommended confirmation model

**Measure-family confirmation, project-scoped**, seeded from IFC assignment:

| Family | Pilot declaration | Canonical token |
|--------|-------------------|-----------------|
| volume | m³ | `m3` |
| area | m² | `m2` |
| length | mm | `mm` |
| count | — | `count` |

Row-level overrides are a later escape hatch only.

---

## 8. Acceptance criteria (for future implementation)

- [ ] Unit status panel visible on Quantities with declared IFC units when present.
- [ ] User can confirm volume/area/length/count without inventing missing declarations.
- [ ] After confirm + regenerate/apply session rows, prep unit cells show confirmed labels (e.g. m³), not `model volume units`.
- [ ] Length confirms to **mm** on pilot (not m) when LENGTHUNIT is mm.
- [ ] Unconfirmed families remain unresolved in UI.
- [ ] Freeze → 5D Review shows confirmed unit status for mapped NetVolume when applicable.
- [ ] Multi-file unit conflict stays unresolved with explanation.
- [ ] Existing MAP-BIG / IFC-SEM snapshots remain intact and openable.
- [ ] No cost/rates/BOQ/writeback; no QTOCache behavior change.
- [ ] No migrations unless pre-approved; no S2 contract change unless pre-approved.
- [ ] Quantities / Review / Time View stay within existing performance budgets.
- [ ] Tests cover resolve helpers, confirm wiring, and honesty (no silent m³).

---

## 9. Discovery verdict (this phase)

**Ready to implement in a follow-up local slice.**  
Indexed `project_units` exist on the pilot; magnitudes align with declared volume/area/length units. The gap is product wiring + confirmation UX, not missing IFC unit assignment data.
