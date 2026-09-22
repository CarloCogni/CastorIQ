# MAP-SAFETY-1 — Mixed Selection Mapping Warning

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `f24f4df`  
**Depends on:** MAP-BIG-2, IFC-SEM-2/3, UNIT-2, SCALE-1A  

---

## 1. User story

As a BIM/5D user, I want Castor to warn me when I am about to batch map mixed quantity rows so I do not accidentally apply the wrong schema mapping to unrelated model data.

---

## 2. Supported first version

1. On **Preview**, analyze **selected visible rows** only (posted `row_keys`).  
2. Show **safe summary** when dimensions are consistent.  
3. Show **warning** when mixed across primary dimensions.  
4. **Apply remains allowed** after a successful preview (no hard block for mixed).  
5. Apply button copy:  
   - consistent → “Apply to session” (existing)  
   - mixed → “Apply anyway to selected rows”  
6. Preview path uses session prep UI so Unit confirmation and SEM fields can be inspected when present.  
7. No migrations; no F2/S2 contract change; no IFC mutation; no cost/rates.

---

## 3. Warning dimensions

| Dimension | Source field(s) | Warn when mixed |
|-----------|-----------------|-----------------|
| IFC Class | `ifc_class` | Yes |
| Measurement Basis | `quantity_basis` | Yes |
| Unit | `unit_basis_display` (fallback `unit_basis`) | Yes |
| Category | `semantic_category` | Yes if present |
| Family | `semantic_family` | Yes if present |
| Project Level | `prop:Identity Data.Project Level` | Yes if present |
| Level / Storey | `spatial:storey` | Yes if present |
| Type Name | `type_name` | **No** in v1 (noise) |

Empty / placeholder values (`""`, `"—"`, `"Unresolved"`, `"Unit not resolved"`, Mixed labels) are ignored unless they appear together with other distinct meaningful values in a way that yields ≥2 distinct *kept* values.

Cap sample values shown (e.g. 4).

---

## 4. Warning behavior

**Consistent:**  
`Selected rows look consistent for batch mapping.`

**Mixed:**  
`Selected rows contain mixed model evidence. Review before applying one mapping.`  
Plus compact lines: `IFC Class: 2 values — IfcWall, IfcBeam`.

No raw JSON dumps.

---

## 5. Apply behavior

- Empty selection / no target / invalid keys: existing errors unchanged.  
- Mixed: Apply enabled after preview; label “Apply anyway to selected rows”.  
- No second confirmation modal in v1.

---

## 6. UX placement

In `quantities_batch_mapping_preview.html`, **above** the proposed mapping summary.  
Severity: info/secondary when consistent; warning when mixed.

---

## 7. Data behavior

Session mapping apply only. No snapshot until user freezes. No S2/F2 change.

---

## 8. Acceptance criteria

- Homogeneous selection → safe summary.  
- Mixed IFC class / basis / unit → warning + details.  
- Visible selected rows only.  
- Apply still works after warning.  
- SEM filters, pagination, unit columns unchanged.  
- Old snapshots open. No GitHub.

---

## 9. Non-goals

Hard block, all-filtered analysis, AI validation, cost/rates, writeback, Type Name warnings, new mapping engine.
