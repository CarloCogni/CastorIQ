# Explore — Castor module

Standalone, iframe-embeddable module for **Castor** (BIM / FM platform). A facility
manager places **points** on a building's **floor plans**, attaches **versioned
photos** (regular or 360°) to each point, links points to **IFC entities by
GlobalID**, and reviews how a space changed over time.

Built as **variant C, "B-first"**: the floor-plan + photo-point workflow is the
core; the 360° panoramic layer sits on top (a point whose photo is an
equirectangular panorama opens the panoramic viewer).

## Features

- **Multi-floor** building; switch floors; reorder / rename / add / delete floors.
- **Floor plans** from PNG/JPG/WebP **or PDF** (each PDF page becomes a floor).
  Optional **white-background knock-out** for clean line drawings.
- **Points** placed by clicking the plan; drag to reposition. Each point has its
  **own phase** (set in the right panel as room state, independent per room).
  Two view modes in the top bar:
  - **Placement** — all pins **grey**, numbered 1..N by placement order (phase
    selector disabled).
  - **By phase** + chosen phase — shows **only** that phase's points (others
    hidden), colored by phase, numbered 1..N for the phase, with a legend.
  - Configurable **zero-pad width** (1 / 01 / 001 …); pins are pills that grow to
    fit multi-digit labels.
- **Two photo archives per point** — a **Photos** archive and a separate **360°**
  archive, each with its own Upload / Camera (mobile rear camera). Each section
  shows its newest as a hero; the bottom **timeline** has a **Photos / 360°**
  toggle and a **Thumbs / Details** view toggle (Details shows each photo's
  name / code / date·time / description). Clicking a timeline item opens a
  **media editor** (name, photo code, date, time, description) with
  View / Delete / Cancel / OK (closes only via those, not a stray click).
- **360° viewer** (Three.js) for equirectangular panoramas — drag to look, scroll
  to zoom. Warns if an image isn't ~2:1.
- **Compare** two versions with a draggable divider — flat before/after for photos,
  synchronised dual-texture sphere for 360°.
- **IFC link**: pick a **room** from the floor's IFC room list to auto-fill
  GlobalID + IFC type. Clicking a pin emits `HOTSPOT_CLICKED`; the host can drive
  the 3D Bridge. Inbound `FOCUS_ELEMENT` selects the matching point.
- **Linked data**: attach read-only **Facility / Schedule tables** (from a
  Castor-supplied catalog) to a room. Per table, choose the **match key** —
  *GlobalID*, *room number*, or *department* (department shows the whole
  department, not just one room).
- **Theme** follows the host via `SET_THEME` (dark default, light supported).

## Run locally (no install needed)

Requires only Python 3. **Use the bundled no-cache server** so edits always show
up (plain `http.server` lets the browser cache ES modules between reloads):

```bash
cd fm-explore
python serve.py
```

- App:    <http://127.0.0.1:5173/>
- Host test harness (simulates Castor): <http://127.0.0.1:5173/harness.html>

> Plain `python -m http.server 5173` also works, but then **hard-reload**
> (Ctrl/Cmd+Shift+R) after editing, or changes may be served from cache.

## Tech

- Vanilla JavaScript (ES modules), **no build step**. `Three.js` and `pdf.js` are
  loaded from a CDN via the import map in `index.html`. A Vite/npm setup can be
  layered on later by removing the import map.
- Files: `src/main.js` (wiring), `src/state.js` (single source of truth),
  `src/bridge/` (protocol + transport), `src/floorplan/`, `src/viewer/`, `src/ui/`.

## Embedding in Castor

Drop the built folder behind a URL and embed it:

```html
<iframe src="https://castor.example/static/explore/index.html"
        allow="camera"></iframe>
```

`allow="camera"` is needed for in-app photo capture. The module talks to the host
purely via `postMessage` (below) — no other coupling.

---

## postMessage protocol (v0.1)

### Envelope

- **Inbound** (Castor → Explore): `{ type, id?, requestId?, ...payload }`
- **Outbound** (Explore → Castor): `{ source: "fm-explore", type, requestId?, ...payload }`

Outbound messages always carry `source: "fm-explore"` so the host can filter them.
`requestId` is echoed back on the matching `ACK` / `ERROR` / reply.

### Handshake & origin locking

1. On load, Explore emits **`VIEWER_READY`** to `*` (parent unknown yet).
2. Castor replies with **`VIEWER_INIT`** (carrying optional initial config).
3. Explore reads `event.origin`, **locks it as the trusted parent**, and sends all
   later messages only to that origin. Messages from any other origin are ignored.

> A sandboxed iframe without `allow-same-origin` reports origin `"null"`; in that
> case Explore can't target it specifically and stays on `*`. Prefer embedding
> with `allow-same-origin` (or a real origin) in production.

### Inbound (Castor → Explore)

| `type` | payload | effect | reply |
|---|---|---|---|
| `VIEWER_INIT` | `{ theme?, floorId?, focus? }` | locks origin; applies initial theme / floor / focus | `ACK {ready, capabilities}` |
| `FOCUS_ELEMENT` | `{ id: GlobalID }` | switch to the point's floor, select + highlight it | `ACK {pointId, floorId}` / `ERROR UNKNOWN_GLOBAL_ID` |
| `SET_THEME` | `{ theme: "dark"\|"light" }` | switch theme | `ACK {theme}` |
| `SET_FLOORS` | `{ floors: [{id?,name,label,plan,planType?,rooms?}], replace? }` | add/replace floors (IfcOpenShell-derived plans) | `ACK {floorIds}` |
| `SET_ROOMS` | `{ floorId, rooms: [{globalId,name,ifcType,props?:{number,department,building,…}}] }` | set a floor's IFC room list; `props` are arbitrary IFC properties usable as identification fields + table filter keys | `ACK {floorId, count}` |
| `SET_TABLE_CATALOG` | `{ tables: { <key>: { group, label, columns:[{field,label}], rows:[{...display fields, globalId, roomNumber, department, _status?}] } } }` | supply the Facility/Schedule table catalog. Rows carry `globalId`/`roomNumber`/`department`; the panel filters them per the user's chosen match key | `ACK {tables}` |
| `GET_STATE` | — | — | `STATE {...}` |

### Outbound (Explore → Castor)

| `type` | payload |
|---|---|
| `VIEWER_READY` | `{ version, capabilities: [...] }` |
| `ACK` | `{ requestId, status: "ok", ... }` |
| `ERROR` | `{ requestId, status: "fail", code, message }` |
| `HOTSPOT_CLICKED` | `{ globalId, ifcType, pointId, floorId, label }` — user clicked a pin / "Focus in 3D" |
| `STATE` | `{ activeFloorId, selectedId, mode, floors:[{id,name,label}], pointCount, theme }` |

### Error codes

`UNKNOWN_GLOBAL_ID`, `BAD_PAYLOAD`, `UNKNOWN_TYPE`.

### Capabilities

`["plan","points","photo","360","timeline","compare","focus","multifloor"]`

---

## Data model (client; mirrors what the backend will persist)

```text
floor  { id, name, label, plan, planType:"image", rooms:[ room ] }
room   { globalId, name, ifcType, props:{ number, department, building, … } }  // from IFC
        // props the user enables (⚙) become identification fields + table filter keys
point  { id, floorId, label, roomId, globalId, ifcType, x%, y%, media:[ media ] }
media  { id, type:"photo"|"360", src, date:"YYYY-MM-DD", phase, label, addedAt }
```

- Point `x`/`y` are **percent of the plan image box**, so they hold at any size.
- `roomId` links a point to an IFC room; selecting a room fills `globalId` + `ifcType`.
- A pin's color comes from its **newest** media version's `phase`.

## Status — all phases complete

- [x] Scaffold + tokens + shell + floor plan
- [x] Point placement
- [x] Multi-floor + plan upload (image/PDF) + white knock-out
- [x] Photo attach (upload + camera)
- [x] Photo versioning + timeline + per-version phase
- [x] Custom phases, editable/reorderable floors, newest-photo hero
- [x] IFC link (GlobalID/IfcType from rooms) + phase-colored pins
- [x] Compare (photo + 360°)
- [x] 360° viewer
- [x] postMessage bridge (handshake, origin lock, ACK/ERROR, test harness)
- [x] Theme (SET_THEME) + protocol docs

## Design tokens

Colors and fonts mirror the Castor prototype (`src/style/tokens.css`): background
`#1c1d28`, sidebar `#161720`, cyan accent `#4fc4cf`; **Inter** for UI, **DM Mono**
for technical data. Dark by default; light theme via `SET_THEME`. The media/plan
stage stays dark in both themes (better for photos).
