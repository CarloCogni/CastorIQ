// state.js — single source of truth for Explore's client state.
//
// Mutations go through the exported functions, which notify subscribers so the
// UI re-renders. Later steps (the postMessage bridge) drive the same functions,
// so host-initiated and user-initiated changes share one path.
//
// Model: a building has many floors; each floor has its own plan image; each
// point belongs to a floor (floorId). Only the active floor's points are shown.

const listeners = new Set();

// ── Phases (user-editable, persisted). Declared before `state` so loadPhases()
// can run during state initialisation without hitting the const TDZ. ──
const PHASES_KEY = "fm-explore.phases";
const STATE_KEY = "fm-explore.session"; // declared early so the restore-on-load IIFE can read it (no TDZ)
function loadPhases() {
  try {
    const raw = localStorage.getItem(PHASES_KEY);
    if (raw) { const a = JSON.parse(raw); if (Array.isArray(a) && a.length) return a; }
  } catch (_) { /* ignore */ }
  return ["Construction", "Fit-out", "Occupied"];
}

export const state = {
  building: { id: "JR", name: "Palác Jiráskovo" },

  // Each floor: { id, name, label, plan, planType, rooms: [{ globalId, name, ifcType }] }
  // rooms[] mirrors the IFC IfcSpace list for that storey — supplied by the host
  // (IfcOpenShell) via SET_FLOORS/SET_ROOMS; seeded here for the demo.
  floors: [
    {
      id: "JR-4F", name: "4F", label: "Palác Jiráskovo · 4F", plan: "./assets/plans/floor-4.svg", planType: "image",
      rooms: [
        { globalId: "2nQ8aF$E1B0xfa4B", name: "Office 4B", ifcType: "IfcSpace", props: { number: "4B", department: "TechSpace", building: "Palác Jiráskovo" } },
        { globalId: "1aB7zT$E1B0xfa4A", name: "Office 4A", ifcType: "IfcSpace", props: { number: "4A", department: "—", building: "Palác Jiráskovo" } },
        { globalId: "3cD9xR$E1B0xfa4C", name: "Office 4C", ifcType: "IfcSpace", props: { number: "4C", department: "TechSpace", building: "Palác Jiráskovo" } },
        { globalId: "4eF1yT$E1B0xfa4D", name: "Office 4D", ifcType: "IfcSpace", props: { number: "4D", department: "DataFlow Labs", building: "Palác Jiráskovo" } },
        { globalId: "5gH2zU$E1B0xCORE", name: "Core", ifcType: "IfcSpace", props: { number: "Core", department: "Building", building: "Palác Jiráskovo" } },
        { globalId: "6iJ3aV$E1B0xfWC0", name: "WC", ifcType: "IfcSpace", props: { number: "WC", department: "Building", building: "Palác Jiráskovo" } },
        { globalId: "7kL4bW$E1B0xKTCH", name: "Kitchen", ifcType: "IfcSpace", props: { number: "KTCH", department: "Common", building: "Palác Jiráskovo" } },
        { globalId: "8mN5cX$E1B0xMTG3", name: "Meeting 3B", ifcType: "IfcSpace", props: { number: "MTG", department: "Common", building: "Palác Jiráskovo" } },
      ],
    },
    {
      id: "JR-3F", name: "3F", label: "Palác Jiráskovo · 3F", plan: "./assets/plans/floor-3.svg", planType: "image",
      rooms: [
        { globalId: "9oP6dY$E1B03OPEN", name: "Open office", ifcType: "IfcSpace" },
        { globalId: "A1qR7eZE1B00MT3A", name: "Meeting 3A", ifcType: "IfcSpace" },
        { globalId: "B2sT8fA$E1B0MT3B", name: "Meeting 3B", ifcType: "IfcSpace" },
        { globalId: "C3uV9gB$E1B3CORE", name: "Core", ifcType: "IfcSpace" },
        { globalId: "D4wX0hC$E1B03WC0", name: "WC", ifcType: "IfcSpace" },
      ],
    },
    {
      id: "JR-2F", name: "2F", label: "Palác Jiráskovo · 2F", plan: "./assets/plans/floor-2.svg", planType: "image",
      rooms: [
        { globalId: "E5yZ1iD$E1B0RECE", name: "Reception", ifcType: "IfcSpace" },
        { globalId: "F6aB2jE$E1B0RETA", name: "Retail A", ifcType: "IfcSpace" },
        { globalId: "G7cD3kF$E1B0RETB", name: "Retail B", ifcType: "IfcSpace" },
        { globalId: "H8eF4lG$E1B0CAFE", name: "Café", ifcType: "IfcSpace" },
        { globalId: "I9gH5mH$E1B2CORE", name: "Core", ifcType: "IfcSpace" },
      ],
    },
  ],
  activeFloorId: "JR-4F",

  // Each point: { id, floorId, label, globalId, ifcType, x (% of plan), y (%), media: [] }
  points: [
    {
      id: "p1", floorId: "JR-4F", label: "Office 4B · NE corner",
      roomId: "2nQ8aF$E1B0xfa4B", globalId: "2nQ8aF$E1B0xfa4B", ifcType: "IfcSpace", x: 46, y: 22,
      phase: "Occupied",
      tables: [{ key: "workorders", filterBy: "globalId" }, { key: "assets", filterBy: "department" }],
      media: [
        { id: "m-demo360", type: "360", src: "./assets/pano/demo-pano.svg", date: "2026-05-23", label: "Demo panorama (equirectangular)" },
      ],
    },
    { id: "p2", floorId: "JR-4F", label: "Office 4A · window", roomId: "1aB7zT$E1B0xfa4A", globalId: "1aB7zT$E1B0xfa4A", ifcType: "IfcSpace", x: 18, y: 30, phase: "Construction", tables: [], media: [] },
  ],

  selectedId: null,
  selectedMediaId: null, // last viewed/selected media version (for timeline highlight)
  mode: "plan",          // 'plan' | '360' | 'compare'
  placing: false,        // true while in "add point" mode
  attachType: "photo",   // 'photo' | '360' — type chosen for the next attached media
  attachPhase: "",       // phase chosen for the next attached media
  phases: loadPhases(),  // user-extendable list of phases
  numbering: { mode: "placement", phase: "", pad: "auto" }, // pin numbering
  idProps: ["number", "department"], // IFC room props shown as identification + usable as table filter keys
  archiveType: "photo",   // which photo archive the bottom timeline shows: 'photo' | '360'
  timelineView: "thumbs", // timeline layout: 'thumbs' | 'details'
  sort: { key: "date", dir: "desc" }, // timeline sort: key date|time|name|description, dir asc|desc (default: newest first)
  phaseColors: {},        // user colour overrides per phase name (managed in the phase manager)
};

// Restore a previously-saved working set (points / media / floors / settings) over
// the demo seed. Saved as data URLs, so media + imported plans reload intact.
(function restoreSession() {
  const saved = loadSession();
  if (saved) Object.assign(state, saved);
})();

export function setArchiveType(type) {
  state.archiveType = type === "360" ? "360" : "photo";
  emit();
}
export function setTimelineView(v) {
  state.timelineView = v === "details" ? "details" : "thumbs";
  emit();
}
export function setSortKey(key) {
  state.sort = { ...state.sort, key };
  emit();
}
export function toggleSortDir() {
  state.sort = { ...state.sort, dir: state.sort.dir === "asc" ? "desc" : "asc" };
  emit();
}

// ── Phases: add a custom one (persisted) ──
export function addPhase(name) {
  const n = (name || "").trim();
  if (!n) return null;
  if (!state.phases.includes(n)) {
    state.phases.push(n);
    try { localStorage.setItem(PHASES_KEY, JSON.stringify(state.phases)); } catch (_) { /* ignore */ }
  }
  emit();
  return n;
}

// Rename a phase everywhere (phase list, points, colour override, numbering filter).
export function renamePhase(oldName, newName) {
  const n = (newName || "").trim();
  if (!oldName || !n || oldName === n) return false;
  if (state.phases.includes(n)) return false; // would collide with an existing phase
  state.phases = state.phases.map((p) => (p === oldName ? n : p));
  state.points.forEach((p) => { if (p.phase === oldName) p.phase = n; });
  if (state.phaseColors[oldName]) { state.phaseColors[n] = state.phaseColors[oldName]; delete state.phaseColors[oldName]; }
  if (state.numbering.phase === oldName) state.numbering = { ...state.numbering, phase: n };
  try { localStorage.setItem(PHASES_KEY, JSON.stringify(state.phases)); } catch (_) { /* ignore */ }
  emit();
  return true;
}

// Delete a phase: clear it from any points (they go grey) and drop its colour override.
export function deletePhase(name) {
  if (!name) return false;
  state.phases = state.phases.filter((p) => p !== name);
  state.points.forEach((p) => { if (p.phase === name) p.phase = ""; });
  if (state.phaseColors[name]) delete state.phaseColors[name];
  if (state.numbering.phase === name) state.numbering = { ...state.numbering, phase: "" };
  try { localStorage.setItem(PHASES_KEY, JSON.stringify(state.phases)); } catch (_) { /* ignore */ }
  emit();
  return true;
}

// Override a phase's pin colour (hex). Persisted with the session.
export function setPhaseColor(name, color) {
  if (!name) return;
  state.phaseColors = { ...state.phaseColors, [name]: color };
  emit();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Host-persistence hook: fires (debounced) with a serialisable snapshot whenever the
// working set changes, so the embedder (Castor) can persist authoritatively.
const stateChangeListeners = new Set();
export function onStateChange(fn) {
  stateChangeListeners.add(fn);
  return () => stateChangeListeners.delete(fn);
}

function emit() {
  listeners.forEach((fn) => fn(state));
  scheduleSave();
}

// ── Session persistence (localStorage) ───────────────────────────────────────
// Media + imported plans are stored as data URLs (see main.js / import.js), so a
// saved session reloads intact. If storage is full, we keep working in-memory.
let saveTimer = null;
let storageOk = true;
function exportSession() {
  return {
    v: 1,
    floors: state.floors,
    points: state.points,
    activeFloorId: state.activeFloorId,
    phases: state.phases,
    phaseColors: state.phaseColors,
    idProps: state.idProps,
    numbering: state.numbering,
    archiveType: state.archiveType,
    timelineView: state.timelineView,
    sort: state.sort,
  };
}
function saveSession() {
  const snap = exportSession();
  try { localStorage.setItem(STATE_KEY, JSON.stringify(snap)); storageOk = true; }
  catch (_) { storageOk = false; } // quota exceeded — stay in-memory
  stateChangeListeners.forEach((fn) => fn(snap, storageOk));
}
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSession, 350);
}
function loadSession() {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (!s || !Array.isArray(s.floors) || !s.floors.length) return null;
    const out = {
      floors: s.floors,
      points: Array.isArray(s.points) ? s.points : [],
      activeFloorId: s.activeFloorId || s.floors[0].id,
      phaseColors: (s.phaseColors && typeof s.phaseColors === "object") ? s.phaseColors : {},
      idProps: Array.isArray(s.idProps) ? s.idProps : ["number", "department"],
      numbering: s.numbering || { mode: "placement", phase: "", pad: "auto" },
      archiveType: s.archiveType === "360" ? "360" : "photo",
      timelineView: s.timelineView === "details" ? "details" : "thumbs",
      sort: s.sort || { key: "date", dir: "desc" },
    };
    if (Array.isArray(s.phases) && s.phases.length) out.phases = s.phases;
    return out;
  } catch (_) { return null; }
}
// Public: full working set (for GET_STATE { full:true }) and reset-to-demo.
export function exportFullState() { return exportSession(); }
export function clearSession() {
  try { localStorage.removeItem(STATE_KEY); localStorage.removeItem(PHASES_KEY); } catch (_) { /* ignore */ }
}

// Hydrate state from a host-supplied snapshot (e.g. SET_USER_STATE from Castor).
// Same shape exportSession() produces. Missing fields keep their current value,
// so the host can send partial updates (e.g. only points + media). Empty floors
// arrays are ignored to avoid clobbering the demo / host SET_FLOORS payload.
export function importFullState(snap) {
  if (!snap || typeof snap !== "object") return false;
  if (Array.isArray(snap.floors) && snap.floors.length) state.floors = snap.floors;
  if (Array.isArray(snap.points)) state.points = snap.points;
  if (Array.isArray(snap.phases) && snap.phases.length) state.phases = snap.phases;
  if (snap.phaseColors && typeof snap.phaseColors === "object") state.phaseColors = snap.phaseColors;
  if (Array.isArray(snap.idProps)) state.idProps = snap.idProps;
  if (snap.numbering && typeof snap.numbering === "object") state.numbering = snap.numbering;
  if (snap.archiveType) state.archiveType = snap.archiveType === "360" ? "360" : "photo";
  if (snap.timelineView) state.timelineView = snap.timelineView === "details" ? "details" : "thumbs";
  if (snap.sort && typeof snap.sort === "object") state.sort = snap.sort;
  if (snap.activeFloorId && state.floors.some((f) => f.id === snap.activeFloorId)) {
    state.activeFloorId = snap.activeFloorId;
  }
  state.selectedId = null; // selection is per-session, never hydrated
  emit();
  return true;
}

// ── Derived getters ──
export function activeFloor() {
  return state.floors.find((f) => f.id === state.activeFloorId) || state.floors[0] || null;
}
export function pointsForActiveFloor() {
  return state.points.filter((p) => p.floorId === state.activeFloorId);
}
export function roomsForActiveFloor() {
  const f = activeFloor();
  return (f && f.rooms) || [];
}

// ── Identification props (which IFC room properties are shown / used as keys) ──
export function setIdProps(list) {
  state.idProps = Array.isArray(list) ? list.slice() : [];
  emit();
}
// Union of property keys present across all rooms (what the IFC offers).
export function availableProps() {
  const set = new Set();
  state.floors.forEach((f) => (f.rooms || []).forEach((r) => Object.keys(r.props || {}).forEach((k) => set.add(k))));
  return [...set];
}

// ── Phase → colour (for plan pins + legend) ──
export const PHASE_COLORS = {
  Construction: "#d4903a", "Fit-out": "#4fc4cf", Occupied: "#42b880", Demo: "#8b7fd4",
};
const PHASE_PALETTE = ["#8b7fd4", "#e0a3d8", "#7fd4c0", "#d4c87f", "#e0975a", "#5ab0e0"];
export function phaseColor(phase) {
  if (!phase) return "#8a8da6";                 // no phase → neutral slate
  if (state.phaseColors && state.phaseColors[phase]) return state.phaseColors[phase]; // user override
  if (PHASE_COLORS[phase]) return PHASE_COLORS[phase];
  let h = 0;
  for (let i = 0; i < phase.length; i += 1) h = (h * 31 + phase.charCodeAt(i)) >>> 0;
  return PHASE_PALETTE[h % PHASE_PALETTE.length];
}
// Newest media version's phase (used as a fallback for the point's phase).
export function pointPhase(point) {
  if (!point.media || !point.media.length) return "";
  const m = [...point.media].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (b.addedAt || 0) - (a.addedAt || 0)))[0];
  return m.phase || "";
}
// The phase that drives a point's pin color + numbering: ONLY the point's own
// assigned phase (room state). No photo fallback — a point with no phase assigned
// stays grey until the user assigns one in the panel. Each point is independent.
export function effectivePhase(point) {
  return point.phase || "";
}

// ── Floor mutations ──
export function setActiveFloor(id) {
  if (state.activeFloorId === id) return;
  state.activeFloorId = id;
  state.selectedId = null; // selection doesn't carry across floors
  state.placing = false;
  emit();
}

// Add one or more floors (from upload, PDF pages, or backend/IFC later).
// Returns the ids of the added floors. Does NOT auto-switch (caller decides).
export function addFloors(list) {
  const added = [];
  list.forEach((f) => {
    const id = f.id || newFloorId();
    state.floors.push({
      id,
      name: f.name || shortFromLabel(f.label) || id,
      label: f.label || f.name || id,
      plan: f.plan,
      planType: f.planType || "image",
    });
    added.push(id);
  });
  emit();
  return added;
}

// Replace the plan image of an existing floor (e.g. swap placeholder for a real one).
export function setFloorPlan(floorId, plan, planType = "image") {
  const f = state.floors.find((f) => f.id === floorId);
  if (!f) return;
  f.plan = plan;
  f.planType = planType;
  emit();
}

// Patch arbitrary fields on a floor (rename via {name}/{label}, knockout, …).
export function updateFloor(floorId, patch) {
  const f = state.floors.find((f) => f.id === floorId);
  if (!f) return;
  Object.assign(f, patch);
  emit();
}

// Set/replace a floor's IFC room list (host supplies it from IfcOpenShell).
export function setRooms(floorId, rooms) {
  const f = state.floors.find((f) => f.id === floorId);
  if (!f) return false;
  f.rooms = Array.isArray(rooms) ? rooms : [];
  emit();
  return true;
}

// Ingest floors from the host (e.g. IfcOpenShell-derived plans via SET_FLOORS).
// replace=true swaps the whole set; otherwise appends. Returns the added ids.
export function setFloors(list, replace = false) {
  if (replace) { state.floors = []; }
  const ids = [];
  list.forEach((f) => {
    const id = f.id || newFloorId();
    state.floors.push({
      id,
      name: f.name || shortFromLabel(f.label) || id,
      label: f.label || f.name || id,
      plan: f.plan,
      planType: f.planType || "image",
      rooms: Array.isArray(f.rooms) ? f.rooms : [],
    });
    ids.push(id);
  });
  if (replace && state.floors.length) {
    // Preserve the user's current floor across host re-hydration (e.g. navigating
    // away from Explore to another module and back) when it's still present;
    // only fall back to the first floor if the previous one is gone.
    if (!state.floors.some((f) => f.id === state.activeFloorId)) {
      state.activeFloorId = state.floors[0].id;
      state.selectedId = null;
    }
  }
  emit();
  return ids;
}

// Reorder floors: move a floor up (dir<0) or down (dir>0) in the list.
export function moveFloor(floorId, dir) {
  const i = state.floors.findIndex((f) => f.id === floorId);
  if (i < 0) return false;
  const j = i + (dir < 0 ? -1 : 1);
  if (j < 0 || j >= state.floors.length) return false;
  const tmp = state.floors[i];
  state.floors[i] = state.floors[j];
  state.floors[j] = tmp;
  emit();
  return true;
}

// Delete a floor and its points. Refuses to delete the last remaining floor.
export function deleteFloor(floorId) {
  if (state.floors.length <= 1) return false;
  state.floors = state.floors.filter((f) => f.id !== floorId);
  state.points = state.points.filter((p) => p.floorId !== floorId);
  if (state.activeFloorId === floorId) {
    state.activeFloorId = state.floors[0].id;
    state.selectedId = null;
  }
  emit();
  return true;
}

// ── Point mutations ──
export function addPoint(x, y) {
  const pt = {
    id: newId(),
    floorId: state.activeFloorId,
    label: "Point " + (pointsForActiveFloor().length + 1),
    roomId: "",
    globalId: "",
    ifcType: "",
    // When placing while filtering "by phase", the new point joins that phase right
    // away — otherwise it would be created with no phase and instantly drop out of
    // the active filter (vanish). In placement mode it stays unset (grey).
    phase: (state.numbering.mode === "phase" && state.numbering.phase) ? state.numbering.phase : "",
    x: round1(x),
    y: round1(y),
    tables: [],
    media: [],
  };
  state.points.push(pt);
  state.selectedId = pt.id;
  // keep placing ON so points can be dropped one after another (toggle off via the button)
  emit();
  return pt;
}

export function selectPoint(id) {
  state.selectedId = id;
  emit();
}
export function deselect() {
  if (state.selectedId === null) return;
  state.selectedId = null;
  emit();
}
export function movePoint(id, x, y) {
  const p = state.points.find((p) => p.id === id);
  if (!p) return;
  p.x = round1(x);
  p.y = round1(y);
  emit();
}
export function updatePoint(id, patch) {
  const p = state.points.find((p) => p.id === id);
  if (!p) return;
  Object.assign(p, patch);
  emit();
}
export function deletePoint(id) {
  state.points = state.points.filter((p) => p.id !== id);
  if (state.selectedId === id) state.selectedId = null;
  emit();
}
export function setPlacing(v) {
  state.placing = !!v;
  emit();
}

// ── Media (photos / 360°) on a point ──
export function setAttachType(type) {
  state.attachType = type === "360" ? "360" : "photo";
  emit();
}
export function setAttachPhase(phase) {
  state.attachPhase = phase || "";
  emit();
}
export function setSelectedMedia(mediaId) {
  state.selectedMediaId = mediaId;
  emit();
}
export function setNumbering(mode, phase) {
  state.numbering = { mode: mode === "phase" ? "phase" : "placement", phase: phase || "", pad: state.numbering.pad };
  emit();
}
export function setNumberingPad(pad) {
  state.numbering = { ...state.numbering, pad: pad === "auto" ? "auto" : Number(pad) };
  emit();
}
export function setPointPhase(pointId, phase) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p) return;
  p.phase = phase || "";
  emit();
}
export function setMediaPhase(pointId, mediaId, phase) {
  const p = state.points.find((p) => p.id === pointId);
  const m = p && p.media.find((m) => m.id === mediaId);
  if (m) { m.phase = phase || ""; emit(); }
}

export function addMedia(pointId, media) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p) return null;
  const m = {
    id: newMediaId(),
    type: media.type === "360" ? "360" : "photo",
    src: media.src,
    date: media.date || todayStr(),
    time: media.time || "",
    phase: media.phase || "",
    label: media.label || "",     // name
    code: media.code || "",
    description: media.description || "",
    addedAt: Date.now(),
  };
  p.media.push(m);
  emit();
  return m;
}

// Edit a media item's metadata (name/code/date/time/phase/description).
export function setMediaMeta(pointId, mediaId, patch) {
  const p = state.points.find((p) => p.id === pointId);
  const m = p && p.media.find((m) => m.id === mediaId);
  if (!m) return;
  ["label", "code", "date", "time", "phase", "description"].forEach((k) => {
    if (k in patch) m[k] = patch[k];
  });
  emit();
}

export function removeMedia(pointId, mediaId) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p) return;
  p.media = p.media.filter((m) => m.id !== mediaId);
  emit();
}

// Re-insert a previously removed media item at its original index (undo).
export function restoreMedia(pointId, media, index) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p || !media) return;
  const i = (index == null || index < 0 || index > p.media.length) ? p.media.length : index;
  p.media.splice(i, 0, media);
  emit();
}

// ── Linked Facility/Schedule tables shown for a point's room ──
// point.tables = [{ key, filterBy }]   filterBy ∈ globalId | roomNumber | department
export function addPointTable(pointId, key, filterBy = "globalId") {
  const p = state.points.find((p) => p.id === pointId);
  if (!p) return;
  if (!p.tables) p.tables = [];
  if (!p.tables.some((t) => t.key === key)) p.tables.push({ key, filterBy });
  emit();
}
export function removePointTable(pointId, key) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p || !p.tables) return;
  p.tables = p.tables.filter((t) => t.key !== key);
  emit();
}
export function setPointTableFilter(pointId, key, filterBy) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p || !p.tables) return;
  const t = p.tables.find((t) => t.key === key);
  if (t) { t.filterBy = filterBy; emit(); }
}

// ── id + helpers ──
function newId() {
  return "pt-" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));
}
function newMediaId() {
  return "m-" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));
}
function todayStr() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}
function newFloorId() {
  return "fl-" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));
}
function shortFromLabel(label) {
  if (!label) return "";
  return label.length > 4 ? label.slice(0, 4) : label;
}
function round1(n) {
  return Math.round(n * 10) / 10;
}
