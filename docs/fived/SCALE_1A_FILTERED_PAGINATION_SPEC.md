# SCALE-1A — Filtered Pagination for Quantity Preparation

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `ca87ddd`  
**Depends on:** MAP-BIG-2, IFC-SEM-1/2/3, UNIT-2  

---

## 1. User story

As a BIM/5D user, I want to review and map more than the first 50 quantity preparation rows by using filters and pagination, so I can build a 5D model from larger IFC datasets safely.

---

## 2. Supported first version

1. Raise type/prep aggregate caps so pilot’s ~309 type grains are available (service cap → **500**).  
2. Apply semantic/structure filters **before** pagination.  
3. Server-side page of filtered prep rows for DOM.  
4. Page sizes: **50 / 100 / 200** (default **50**).  
5. Show: `Showing X–Y of Z filtered preparation rows` and `Page N of M`.  
6. Preserve query params (semantic cols/filters, basis, source mapping, units session).  
7. Batch map **selected visible rows only**.  
8. Keep full filtered `prep_rows` for freeze/export/known keys (pagination is display-only).

Query params:
- `prep_page` (1-based, default 1)
- `prep_page_size` ∈ {50,100,200} (default 50)

---

## 3. Selection safety

- Checkboxes only on current page.  
- Modal: “You are mapping N selected visible rows from the current page.”  
- No “select/map all filtered rows” in SCALE-1A (disabled/future placeholder optional).

---

## 4. UX

- Compact pagination above (and optionally below) the prep table.  
- Page size dropdown; Previous/Next.  
- Empty state when filter yields zero.  
- Wording: **preparation rows**, not IFC entities.  
- Batch toolbar: “Map selected visible rows”.

---

## 5. Data behavior

- No migrations; no IFC mutation; no S2 contract change.  
- Session mapping apply only for posted row_keys.  
- Freeze uses full filtered prep set from runtime (not page slice).

---

## 6. Performance

- Do not render all prep rows.  
- Do not dump 6000 entities into DOM.  
- Quantities avg &lt; 2.5s with semantic columns.  
- Entity property scan remains one pass (unchanged SEM pattern).

---

## 7. Non-goals

No infinite scroll, virtualization, all-filtered bulk apply, element-level prep, writeback, Ask/Excel, cost/rates, unit conversion.

---

## 8. Acceptance criteria

- User can reach prep rows beyond the old first 50.  
- Filters apply before pagination; counts accurate.  
- SEM-2/3 + UNIT-2 columns still work.  
- Visible-row batch map works; old snapshots open; perf OK; no GitHub.

---

## 9. SCALE-1B follow-ons

Map-all-filtered with confirmation; larger caps/cursors; element-level prep; virtualization.
