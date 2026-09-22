# FREEZE-UX-1 — Guided Freeze after Quantity Prep Changes

**Status:** Locked for local implementation  
**Branch:** `integration/df-foundation-main-sync`  
**HEAD at lock:** `8d47f91`  
**Depends on:** MAP-BIG-2, UNIT-2, SCALE-1A, MAP-SAFETY-1, F2 snapshot service  

---

## 1. User story

As a BIM/5D user, after applying quantity mapping or unit changes, I want a clear guided action to freeze a new 5D snapshot so I can review the updated data model without confusing it with older snapshots.

---

## 2. Supported first version

1. Detect pending session **mapping** and/or **unit confirmation** changes.  
2. Show Quantities banner when pending: title **Unsaved 5D review changes**.  
3. Primary CTA: **Freeze updated 5D snapshot** → POST new freeze endpoint.  
4. Secondary: **Continue editing** (dismiss scroll / stay on page).  
5. After successful freeze: toast + redirect to that version’s 5D Quantity Review.  
6. No auto-freeze. No old snapshot mutation.  
7. After batch Apply / unit confirm: toast already mentions freeze; banner visible on reload.

---

## 3. Snapshot naming

- Form field `version_label` (optional).  
- Default when empty: `Quantity Prep Snapshot — YYYY-MM-DD HH:mm` (UTC).  
- Demo script may pass `FOUNDER-DEMO-FREEZE-UX-1-v1` — **not** hardcoded in product UI.  
- `model_name`: reuse latest project `FiveDDataModel` when present; else create **Quantity Preparation**.

---

## 4. UX copy

**Banner title:** Unsaved 5D review changes  

**Body:** Your mapping or unit changes are active in this preparation session. Freeze a new 5D snapshot to review them in 5D Quantity Review. Existing snapshots will not be changed.  

**CTA:** Freeze updated 5D snapshot  

**Secondary:** Continue editing  

**Success toast:** Snapshot created. Opening 5D Quantity Review.

---

## 5. Data behavior

- POST freeze wraps `FiveDPrepSnapshotService.create_snapshot` only.  
- Passes `request.session` + `return_query` as query.  
- No migrations; no F2/S2 contract change; no IFC mutation.

---

## 6. Performance

- Pending detection = session dict inspect only (no entity scan).  
- Freeze cost = existing snapshot path (user-initiated).  
- Quantities avg &lt; 2.5s.

---

## 7. Non-goals

No snapshot diff, approval workflow, writeback, cost/rates, auto-freeze, deleting old snapshots.

---

## 8. Acceptance criteria

- Banner after mapping / unit session changes.  
- CTA freezes and opens 5D Review.  
- Old snapshots intact.  
- SEM / pagination / MAP-SAFETY / units unchanged.  
- No GitHub.
