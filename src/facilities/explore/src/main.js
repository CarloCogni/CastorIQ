// main.js — Explore module entrypoint.
//
// Standalone, iframe-ready. No build step: plain ES modules served statically.
// Step 2 scope: place points on the plan (click), select them, edit identity in
// the detail panel, drag to reposition, delete. State lives in state.js; the UI
// is a pure function of it. The postMessage bridge (Step 3) will drive the same
// state mutations, so host- and user-initiated changes share one path.

import {
  state, subscribe,
  activeFloor, pointsForActiveFloor, roomsForActiveFloor, setActiveFloor, addFloors, setFloors, setRooms, updateFloor, setFloorPlan,
  setIdProps, availableProps,
  addPoint, selectPoint, deselect, movePoint, updatePoint, deletePoint, setPlacing,
  setAttachPhase, setSelectedMedia, setMediaMeta, setArchiveType, setTimelineView, setSortKey, toggleSortDir, setNumbering, setNumberingPad, setPointPhase, addMedia, removeMedia, restoreMedia,
  addPhase, renamePhase, deletePhase, setPhaseColor, deleteFloor, moveFloor, phaseColor, effectivePhase, addPointTable, removePointTable, setPointTableFilter,
  onStateChange, exportFullState, importFullState, clearSession,
  POINT_KINDS, CUSTOM_SYMBOLS, pointKind, pointGlyph, pointKindLabel, setPlaceKind, setKindFilter, setPointKind, setPlaceCustomName, setPointKindLabel,
} from "./state.js";
import { filterRows, tableCatalog, setTableCatalog } from "./data/roomdata.js";
import { renderPins, initFloorplan } from "./floorplan/floorplan.js";
import { renderFloorSwitcher, buildFloorManager } from "./ui/floors.js";
import { renderPanel } from "./ui/panel.js";
import { renderTimeline } from "./ui/timeline.js";
import { initModal, openModal, closeModal } from "./ui/modal.js";
import { MSG, ERR } from "./bridge/protocol.js";
import { initBridge, emit, ack, error } from "./bridge/bridge.js";

// ── Element refs ──
const els = {
  app: document.getElementById("app"),
  viewer: document.getElementById("viewer"),
  pinLayer: document.getElementById("pinLayer"),
  planImg: document.getElementById("planImg"),
  floorSwitcher: document.getElementById("floorSwitcher"),
  legend: document.getElementById("legend"),
  numbering: document.getElementById("numbering"),
  kindFilter: document.getElementById("kindFilter"),
  dpKinds: document.getElementById("dpKinds"),
  btnAddPoint: document.getElementById("btnAddPoint"),
  btnPoints: document.getElementById("btnPoints"),
  btnTheme: document.getElementById("btnTheme"),
  btnImport: document.getElementById("btnImport"),
  btnFloors: document.getElementById("btnFloors"),
  fileInput: document.getElementById("fileInput"),
  photoUpload: document.getElementById("photoUpload"),
  photoCamera: document.getElementById("photoCamera"),
  dpTitle: document.getElementById("dpTitle"),
  dpSub: document.getElementById("dpSub"),
  dpBody: document.getElementById("dpBody"),
  dpActions: document.getElementById("dpActions"),
  tlRoom: document.getElementById("tlRoom"),
  tlTrack: document.getElementById("tlTrack"),
  tlTypes: document.getElementById("tlTypes"),
  tlView: document.getElementById("tlView"),
  tlSort: document.getElementById("tlSort"),
  tlCollapse: document.getElementById("tlCollapse"),
  tlResize: document.getElementById("tlResize"),
  dpResize: document.getElementById("dpResize"),
  expTimeline: document.getElementById("expTimeline"),
  vLabel: document.getElementById("vLabel"),
  mhSub: document.getElementById("mhSub"),
  lightbox: document.getElementById("lightbox"),
  lbImg: document.getElementById("lbImg"),
  lbClose: document.getElementById("lbClose"),
  pano: document.getElementById("pano"),
  panoMount: document.getElementById("panoMount"),
  panoTitle: document.getElementById("panoTitle"),
  panoWarn: document.getElementById("panoWarn"),
  panoClose: document.getElementById("panoClose"),
  panoReset: document.getElementById("panoReset"),
  btnCmp: document.getElementById("btnCmp"),
  compare: document.getElementById("compare"),
  cmpStage: document.getElementById("cmpStage"),
  cmpMediaL: document.getElementById("cmpMediaL"),
  cmpMediaR: document.getElementById("cmpMediaR"),
  cmpPano: document.getElementById("cmpPano"),
  cmpLeft: document.getElementById("cmpLeft"),
  cmpRight: document.getElementById("cmpRight"),
  cmpLblL: document.getElementById("cmpLblL"),
  cmpLblR: document.getElementById("cmpLblR"),
  cmpDivider: document.getElementById("cmpDivider"),
  cmpClose: document.getElementById("cmpClose"),
};

let compareInstance = null; // 360° compare (Three.js)
let cmpDiv = 50;            // divider position (%)
let cmpMode = "photo";      // 'photo' (drag anywhere) | 'pano' (drag = rotate)

// A 360° image should be equirectangular ~2:1. Anything outside this tolerance
// will look distorted when wrapped on the sphere.
const PANO_RATIO_TOLERANCE = 0.3;
function panoRatioOk(ratio) {
  return ratio && Math.abs(ratio - 2) <= PANO_RATIO_TOLERANCE;
}

// point id + archive type awaiting a photo from the upload/camera file inputs
let attachTargetId = null;
let attachTargetType = "photo";
// live 360° viewer instance (Three.js), so we can dispose it on close
let panoInstance = null;

// ── Toast helper (optional action button, e.g. Undo) ──
let toastTimer = null;
const toastEl = document.getElementById("toast");
export function toast(msg, action) {
  toastEl.innerHTML = "";
  toastEl.appendChild(document.createTextNode(msg));
  if (action && action.label) {
    const b = document.createElement("button");
    b.className = "toast-act";
    b.textContent = action.label;
    b.addEventListener("click", () => { clearTimeout(toastTimer); toastEl.classList.remove("on"); if (action.fn) action.fn(); });
    toastEl.appendChild(b);
  }
  toastEl.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("on"), action ? 6000 : 1800);
}

// ── Render: UI is a pure function of state ──
function render() {
  const floor = activeFloor();

  // Swap plan image only when it changes (avoids reload flicker)
  if (floor && els.planImg.getAttribute("src") !== floor.plan) {
    els.planImg.setAttribute("src", floor.plan);
  }

  renderFloorSwitcher(els.floorSwitcher, state.floors, state.activeFloorId, (id) => setActiveFloor(id), openFloorManager);

  const allPts = pointsForActiveFloor();
  // Header "kind" filter (photo / camera / sensor / custom) hides non-matching pins.
  const pts = state.kindFilter ? allPts.filter((p) => pointKind(p) === state.kindFilter) : allPts;
  const nums = computeNumbers(pts);
  renderPins(els.pinLayer, pts, {
    selectedId: state.selectedId,
    onSelect: (id) => { selectPoint(id); emitHotspotClicked(id); },
    onMove: (id, x, y) => movePoint(id, x, y),
    // Pin is colored by its assigned phase in every mode; grey until a phase is set.
    colorFor: (pt) => phaseColor(effectivePhase(pt)),
    // Non-photo kinds draw a symbol instead of a number.
    glyphFor: (pt) => pointGlyph(pt),
    numbers: nums,
    pad: numberPad(nums),
  });
  renderLegend();
  renderNumbering();
  renderKindFilter();
  renderPlaceKind();

  els.app.classList.toggle("placing", state.placing);
  els.btnAddPoint.classList.toggle("on", state.placing);
  els.btnAddPoint.textContent = state.placing ? "✕ Cancel" : "+ Point";

  const sel = state.points.find((p) => p.id === state.selectedId) || null;
  renderPanel(
    { title: els.dpTitle, sub: els.dpSub, body: els.dpBody, actions: els.dpActions },
    sel,
    {
      onField: (id, f, v) => updatePoint(id, { [f]: v }),
      onDelete: (id) => confirmDeletePoint(id),
      onUpload: (id, type) => { attachTargetId = id; attachTargetType = type; els.photoUpload.click(); },
      onCamera: (id, type) => { attachTargetId = id; attachTargetType = type; els.photoCamera.click(); },
      onRemoveMedia: (pid, mid) => removeMediaWithUndo(pid, mid),
      onViewMedia: (pid, mid) => openMedia(pid, mid),
      onFocus3D: (id) => { emitHotspotClicked(id); toast("Sent FOCUS to Castor → 3D bridge"); },
      onSetPointPhase: (id, phase) => setPointPhase(id, phase),
      onAddPointPhase: (id) => openAddPhase((name) => setPointPhase(id, name)),
      onSelectRoom: selectRoom,
      onAddTable: (id, key) => { addPointTable(id, key, "globalId"); },
      onRemoveTable: (id, key) => { removePointTable(id, key); },
      onSetTableFilter: (id, key, filterBy) => { setPointTableFilter(id, key, filterBy); },
      onConfigIdProps: openIdPropsConfig,
      onSetPointKindLabel: (id, name) => setPointKindLabel(id, name),
      onSetPointKind: (id, kind, symbol) => setPointKind(id, kind, symbol),
    },
    {
      phases: state.phases,
      rooms: roomsForActiveFloor(),
      room: sel ? roomForPoint(sel) : null,
      idProps: state.idProps,
      propLabel,
      customSymbols: CUSTOM_SYMBOLS,
      catalog: tableCatalog(),
      filterKeys: filterKeysObj(),
      roomName: sel ? roomNameForPoint(sel) : "",
      getRows: sel ? (key, filterBy) => filterRows(key, filterBy, roomKeys(sel)) : () => ({ columns: [], rows: [] }),
    },
  );

  // Bottom timeline: the selected archive (Photos / 360°) for the selected point
  renderArchiveToggle(sel);
  renderTimelineView();
  renderTimelineSort();
  els.expTimeline.classList.toggle("details", state.timelineView === "details");
  renderTimeline(els.tlTrack, sel, {
    type: state.archiveType,
    view: state.timelineView,
    sort: state.sort,
    selectedMediaId: state.selectedMediaId,
    onSelect: (pid, mid) => openMediaEditor(pid, mid),
  });

  // Compare works within the active archive (photo↔photo or 360↔360), never mixed.
  const cmpCount = sel ? sel.media.filter((m) => m.type === state.archiveType).length : 0;
  const archWord = state.archiveType === "360" ? "360°" : "photo";
  els.btnCmp.disabled = cmpCount < 2;
  els.btnCmp.title = cmpCount >= 2
    ? `Compare two ${archWord} versions`
    : `Select a point with 2+ ${state.archiveType === "360" ? "360° panoramas" : "photos"} (current archive: ${archWord})`;

  els.vLabel.textContent = floor ? floor.label : "Floor plan";
  els.mhSub.textContent = floor ? "Floor plan · " + floor.name : "Floor plan";
  els.tlRoom.textContent = sel ? (sel.label || "Point") : "—";
}

// ── Photo attach (one or more files from the upload/camera inputs) ──
function fileToDataURL(file) {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => resolve(null);
    r.readAsDataURL(file);
  });
}
function handlePhotoFiles(fileList) {
  const files = [...(fileList || [])].filter((f) => f && f.type && f.type.startsWith("image/"));
  if (!files.length || !attachTargetId) { attachTargetId = null; return; }
  const type = attachTargetType;
  const targetId = attachTargetId;
  attachTargetId = null;
  // Read as data URLs (not object URLs) so attached media persists / survives a reload.
  Promise.all(files.map(fileToDataURL)).then((results) => {
    const srcs = [];
    results.forEach((src) => { if (src && addMedia(targetId, { type, src })) srcs.push(src); });
    if (srcs.length) setArchiveType(type); // show the archive we just added to
    toast(srcs.length ? `Added ${srcs.length} ${type === "360" ? "360°" : "photo"}${srcs.length > 1 ? "s" : ""}` : "Could not attach");

    // For 360°, warn once if any image isn't equirectangular 2:1
    if (srcs.length && type === "360") {
      let warned = false;
      srcs.forEach((src) => {
        const img = new Image();
        img.onload = () => {
          const ratio = img.naturalHeight ? img.naturalWidth / img.naturalHeight : 0;
          if (!warned && !panoRatioOk(ratio)) { warned = true; toast("Heads up: some 360° images aren't 2:1 — use equirectangular panoramas"); }
        };
        img.src = src;
      });
    }
  });
}

// ── View media: 360° opens the panoramic viewer, photos open the flat lightbox ──
function openMedia(pointId, mediaId) {
  const p = state.points.find((p) => p.id === pointId);
  const m = p && p.media.find((m) => m.id === mediaId);
  if (!m) return;
  setSelectedMedia(mediaId); // highlight in timeline / panel
  if (m.type === "360") openPano(m);
  else openLightbox(m);
}

function openLightbox(m) {
  els.lbImg.src = m.src;
  els.lightbox.hidden = false;
}
function closeLightbox() {
  els.lightbox.hidden = true;
  els.lbImg.src = "";
}

async function openPano(m) {
  els.panoTitle.textContent = m.label || "360°";
  els.panoWarn.hidden = true;
  els.pano.hidden = false; // must be visible before measuring the mount size
  try {
    const { createPano } = await import("./viewer/pano360.js");
    if (panoInstance) { panoInstance.dispose(); panoInstance = null; }
    panoInstance = createPano(els.panoMount, m.src, (meta) => {
      if (!panoRatioOk(meta.ratio)) {
        els.panoWarn.textContent =
          `⚠ ${meta.width}×${meta.height} (ratio ${meta.ratio.toFixed(2)}:1) — not equirectangular 2:1, so it looks distorted. Use a 360° camera / Photo Sphere image.`;
        els.panoWarn.hidden = false;
      }
    });
  } catch (err) {
    console.error("[explore] pano viewer failed", err);
    toast("360° viewer failed — see console");
    closePano();
  }
}
function closePano() {
  if (panoInstance) { panoInstance.dispose(); panoInstance = null; }
  els.panoMount.innerHTML = "";
  els.pano.hidden = true;
}

subscribe(render);

// ── Wire interactions ──
initFloorplan({ viewer: els.viewer, pinLayer: els.pinLayer, state, actions: { addPoint, deselect } });

els.btnAddPoint.addEventListener("click", () => {
  setPlacing(!state.placing);
  if (state.placing) toast("Click on the plan to place a point");
});

// All-points list + search (jump across floors)
els.btnPoints.addEventListener("click", openPointList);

// Standalone theme toggle (when embedded, Castor still drives theme via SET_THEME).
const THEME_KEY = "fm-explore.theme";
els.btnTheme.addEventListener("click", () => {
  const next = (document.documentElement.getAttribute("data-theme") === "light") ? "dark" : "light";
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* ignore */ }
});

// ── Timeline: chevron collapse + drag-to-resize the top edge ("roleta") — persisted.
//    Drag the handle to set any height; chevron hides it to header-only. ──
const TL_COLLAPSE_KEY = "fm-explore.tlCollapsed";
const TL_HEIGHT_KEY = "fm-explore.tlHeight";
const TL_MIN_H = 70;
function tlMaxH() { return Math.round(window.innerHeight * 0.7); }
function setTimelineHeight(h) {
  const clamped = Math.max(TL_MIN_H, Math.min(Math.round(h), tlMaxH()));
  els.expTimeline.classList.remove("collapsed");
  els.expTimeline.style.height = clamped + "px";
  try { localStorage.setItem(TL_HEIGHT_KEY, String(clamped)); localStorage.setItem(TL_COLLAPSE_KEY, ""); } catch (_) { /* ignore */ }
}
// chevron: collapse to header-only, or restore the last dragged height
els.tlCollapse.addEventListener("click", () => {
  const collapse = !els.expTimeline.classList.contains("collapsed");
  els.expTimeline.classList.toggle("collapsed", collapse);
  if (collapse) {
    els.expTimeline.style.height = ""; // let .collapsed{height:auto} show header only
  } else {
    const h = parseInt(localStorage.getItem(TL_HEIGHT_KEY) || "", 10);
    if (h) els.expTimeline.style.height = Math.max(TL_MIN_H, Math.min(h, tlMaxH())) + "px";
  }
  try { localStorage.setItem(TL_COLLAPSE_KEY, collapse ? "1" : ""); } catch (_) { /* ignore */ }
});
// drag the top edge to resize
if (els.tlResize) {
  let tlDragging = false;
  els.tlResize.addEventListener("pointerdown", (e) => {
    tlDragging = true;
    try { els.tlResize.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    e.preventDefault();
  });
  els.tlResize.addEventListener("pointermove", (e) => {
    if (!tlDragging) return;
    const r = els.expTimeline.getBoundingClientRect();
    setTimelineHeight(r.bottom - e.clientY);
  });
  els.tlResize.addEventListener("pointerup", () => { tlDragging = false; });
}
// restore persisted state on load
try {
  if (localStorage.getItem(TL_COLLAPSE_KEY)) {
    els.expTimeline.classList.add("collapsed");
  } else {
    const h = parseInt(localStorage.getItem(TL_HEIGHT_KEY) || "", 10);
    if (h) els.expTimeline.style.height = Math.max(TL_MIN_H, Math.min(h, tlMaxH())) + "px";
  }
} catch (_) { /* ignore */ }

// ── Detail panel: drag its left edge to widen/narrow (the viewer + its floor
//    switcher follow) — persisted. ──
const PANEL_W_KEY = "fm-explore.panelWidth";
const PANEL_MIN_W = 240;
function panelMaxW() { return Math.max(PANEL_MIN_W, Math.round(window.innerWidth * 0.6)); }
const detailPanel = document.querySelector(".detail-panel");
function setPanelWidth(w) {
  if (!detailPanel) return;
  const clamped = Math.max(PANEL_MIN_W, Math.min(Math.round(w), panelMaxW()));
  detailPanel.style.width = clamped + "px";
  try { localStorage.setItem(PANEL_W_KEY, String(clamped)); } catch (_) { /* ignore */ }
}
if (els.dpResize && detailPanel) {
  let dpDragging = false;
  els.dpResize.addEventListener("pointerdown", (e) => {
    dpDragging = true;
    try { els.dpResize.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    e.preventDefault();
  });
  els.dpResize.addEventListener("pointermove", (e) => {
    if (!dpDragging) return;
    const r = detailPanel.getBoundingClientRect();
    setPanelWidth(r.right - e.clientX);
  });
  els.dpResize.addEventListener("pointerup", () => { dpDragging = false; });
}
try { const pw = parseInt(localStorage.getItem(PANEL_W_KEY) || "", 10); if (pw) setPanelWidth(pw); } catch (_) { /* ignore */ }

// ── Host palette → module tokens, so the embedded background matches Castor
//    exactly (sent by the host in VIEWER_INIT / SET_THEME; works light + dark). ──
function applyHostPalette(p) {
  if (!p || typeof p !== "object") return;
  const root = document.documentElement.style;
  const set = (k, v) => { if (v) root.setProperty(k, v); };
  set("--bg", p.body);
  set("--viewer-bg", p.body);
  set("--sb", p.tertiary || p.body);
  set("--p1", p.tertiary || p.body);
  set("--p2", p.secondary || p.tertiary || p.body);
  set("--p3", p.secondary || p.tertiary || p.body);
  set("--b1", p.border);
  set("--b2", p.border);
  set("--t1", p.text);
  set("--t2", p.muted || p.text);
}

// ── Import floor plans (images + PDF) ──
els.btnImport.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", async (e) => {
  const files = e.target.files;
  if (!files || !files.length) return;
  toast("Importing plans…");
  try {
    const { importFiles } = await import("./floorplan/import.js");
    const descriptors = await importFiles(files);
    if (!descriptors.length) { toast("No usable images/PDF in selection"); return; }
    const ids = addFloors(descriptors);
    setActiveFloor(ids[0]); // jump to the first imported floor
    toast(`Imported ${descriptors.length} floor${descriptors.length > 1 ? "s" : ""}`);
    if (reopenManagerAfterImport) openFloorManager(); // came from the floor manager's “＋ Add floor”
  } catch (err) {
    console.error("[explore] import failed", err);
    toast("Import failed — see console");
  } finally {
    reopenManagerAfterImport = false;
    els.fileInput.value = ""; // allow re-importing the same file
  }
});

// Photo file inputs (multi-select allowed on the gallery picker)
els.photoUpload.addEventListener("change", (e) => {
  handlePhotoFiles(e.target.files);
  e.target.value = "";
});
els.photoCamera.addEventListener("change", (e) => {
  handlePhotoFiles(e.target.files);
  e.target.value = "";
});

// Lightbox close (button, backdrop click, Esc)
els.lbClose.addEventListener("click", closeLightbox);
els.lightbox.addEventListener("click", (e) => { if (e.target === els.lightbox) closeLightbox(); });

// 360° viewer close / reset
els.panoClose.addEventListener("click", closePano);
els.panoReset.addEventListener("click", () => panoInstance && panoInstance.reset());

// Compare: open / version selectors / divider drag / close
els.btnCmp.addEventListener("click", () => { if (!els.btnCmp.disabled) openCompare(state.selectedId); });
els.cmpLeft.addEventListener("change", renderCompareView);
els.cmpRight.addEventListener("change", renderCompareView);
els.cmpClose.addEventListener("click", closeCompare);

let cmpDragging = false;
function moveDividerTo(clientX) {
  const r = els.cmpStage.getBoundingClientRect();
  cmpDiv = Math.max(5, Math.min(95, ((clientX - r.left) / r.width) * 100));
  applyDivider();
}
// Grab the divider line directly (works in both photo + 360° modes)
els.cmpDivider.addEventListener("pointerdown", (e) => { e.stopPropagation(); cmpDragging = true; });
// In photo mode, dragging anywhere on the stage moves the divider (360° drag = rotate, handled by the canvas)
els.cmpStage.addEventListener("pointerdown", (e) => {
  if (cmpMode !== "photo") return;
  cmpDragging = true;
  moveDividerTo(e.clientX);
});
document.addEventListener("pointermove", (e) => { if (cmpDragging) moveDividerTo(e.clientX); });
document.addEventListener("pointerup", () => { cmpDragging = false; });

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!els.lightbox.hidden) closeLightbox();
  else if (!els.pano.hidden) closePano();
  else if (!els.compare.hidden) closeCompare();
});

// ── Phase legend: all assigned phases on the floor (placement), or just the
//    filtered one (by phase). Hidden when no point has a phase yet. ──
function renderLegend() {
  const { mode, phase } = state.numbering;
  const present = (mode === "phase" && phase)
    ? [phase]
    : [...new Set(pointsForActiveFloor().map(effectivePhase).filter(Boolean))];
  if (!present.length) { els.legend.hidden = true; els.legend.innerHTML = ""; return; }
  els.legend.hidden = false;
  els.legend.innerHTML = present
    .map((ph) => `<span class="leg-i"><span class="leg-d" style="background:${phaseColor(ph)}"></span>${escHtml(ph)}</span>`)
    .join("");
}

// ── Pin numbering: by placement order, or sequence within a chosen phase. Both
//    start at 1. Returns id → integer | null (null = not in the active set). ──
function computeNumbers(points) {
  const map = {};
  const { mode, phase } = state.numbering;
  const byPhase = mode === "phase" && phase;
  let n = 0;
  points.forEach((pt) => {
    const inSet = byPhase ? (effectivePhase(pt) === phase) : true;
    if (!inSet) { map[pt.id] = null; return; }      // hidden by the phase filter
    if (pointKind(pt) !== "photo") { map[pt.id] = "•"; return; } // shows a symbol, no number
    map[pt.id] = ++n;                                // only photo points get numbers
  });
  return map;
}
// Zero-pad width: explicit setting, or auto from the highest number on the floor.
function numberPad(nums) {
  if (state.numbering.pad !== "auto") return Number(state.numbering.pad) || 1;
  const max = Math.max(1, ...Object.values(nums).filter((v) => typeof v === "number"));
  return String(max).length;
}

function renderNumbering() {
  const { mode, phase, pad } = state.numbering;
  let html =
    `<span class="num-lbl" title="Pin numbering">№</span>` +
    `<div class="seg">` +
    `<button class="seg-b ${mode === "placement" ? "on" : ""}" data-num="placement">Placement</button>` +
    `<button class="seg-b ${mode === "phase" ? "on" : ""}" data-num="phase">By phase</button>` +
    `</div>`;
  // Phase selector is display-only: enabled in 'by phase', disabled in 'placement'.
  // Lists ONLY phases actually assigned to points on this floor (so every option shows something).
  {
    const opts = phasesOnActiveFloor();
    const noneLeft = mode === "phase" && !opts.length;
    html += `<select class="phase-sel" data-numphase ${mode === "placement" ? "disabled" : ""} title="Show only this phase">` +
      `<option value="">${noneLeft ? "no phases assigned yet" : "— pick phase"}</option>` +
      opts.map((p) => `<option value="${escHtml(p)}" ${p === phase ? "selected" : ""}>${escHtml(p)}</option>`).join("") +
      `</select>`;
  }
  // manage phases (rename / colour / delete)
  html += `<button class="num-cfg" data-managephases title="Manage phases (rename / colour / delete)">⚙</button>`;
  // digits / zero-padding
  html += `<select class="phase-sel" data-numpad title="Digits (zero-padding)">` +
    [["auto", "Digits: auto"], [1, "1 (1)"], [2, "2 (01)"], [3, "3 (001)"], [4, "4 (0001)"]]
      .map(([v, lbl]) => `<option value="${v}" ${String(pad) === String(v) ? "selected" : ""}>${lbl}</option>`).join("") +
    `</select>`;

  els.numbering.innerHTML = html;
  els.numbering.querySelectorAll("[data-num]").forEach((b) => {
    b.addEventListener("click", () => {
      const m = b.dataset.num;
      let ph = "";
      if (m === "phase") {
        const present = phasesOnActiveFloor();
        const selPt = state.points.find((p) => p.id === state.selectedId);
        const selPhase = selPt ? effectivePhase(selPt) : "";
        // prefer the selected point's phase, else keep the current one, else first present
        ph = (selPhase && present.includes(selPhase)) ? selPhase
          : (present.includes(state.numbering.phase) ? state.numbering.phase : (present[0] || ""));
      }
      setNumbering(m, ph);
    });
  });
  const ps = els.numbering.querySelector("[data-numphase]");
  if (ps) ps.addEventListener("change", () => setNumbering("phase", ps.value));
  const pd = els.numbering.querySelector("[data-numpad]");
  if (pd) pd.addEventListener("change", () => setNumberingPad(pd.value));
  const mp = els.numbering.querySelector("[data-managephases]");
  if (mp) mp.addEventListener("click", openPhaseManager);
}

// ── Header: filter the plan by point kind (photo / camera / sensor / custom) ──
function renderKindFilter() {
  if (!els.kindFilter) return;
  const cur = state.kindFilter;
  els.kindFilter.innerHTML =
    `<span class="num-lbl" title="Filter points by kind">⛬</span>` +
    `<select class="phase-sel" data-kindfilter title="Show only this point kind">` +
    `<option value="">All kinds</option>` +
    Object.keys(POINT_KINDS).map((k) =>
      `<option value="${k}" ${k === cur ? "selected" : ""}>${escHtml(POINT_KINDS[k].label)}</option>`).join("") +
    `</select>`;
  const sel = els.kindFilter.querySelector("[data-kindfilter]");
  if (sel) sel.addEventListener("change", () => setKindFilter(sel.value));
}

// ── Panel: choose the kind (+ custom symbol) for the next placed point ──
function renderPlaceKind() {
  if (!els.dpKinds) return;
  const k = state.placeKind;
  let html =
    `<span class="dp-kinds-lbl">New point</span>` +
    `<select class="phase-sel" data-placekind title="Kind of the next placed point">` +
    Object.keys(POINT_KINDS).map((kk) =>
      `<option value="${kk}" ${kk === k ? "selected" : ""}>${escHtml(POINT_KINDS[kk].label)}</option>`).join("") +
    `</select>`;
  if (k === "custom") {
    html += `<input class="phase-sel dp-customname" data-placecustomname placeholder="Type name (e.g. Exit)" value="${escHtml(state.placeCustomName || "")}" title="Name for the custom point type" />`;
    html += `<span class="dp-syms">` +
      CUSTOM_SYMBOLS.map((s) =>
        `<button class="dp-sym${s === state.placeSymbol ? " on" : ""}" data-placesym="${escHtml(s)}" title="Symbol">${escHtml(s)}</button>`).join("") +
      `</span>`;
  }
  els.dpKinds.innerHTML = html;
  const sel = els.dpKinds.querySelector("[data-placekind]");
  if (sel) sel.addEventListener("change", () => setPlaceKind(sel.value, state.placeSymbol));
  const nm = els.dpKinds.querySelector("[data-placecustomname]");
  if (nm) nm.addEventListener("input", () => setPlaceCustomName(nm.value));
  els.dpKinds.querySelectorAll("[data-placesym]").forEach((b) =>
    b.addEventListener("click", () => setPlaceKind("custom", b.dataset.placesym)));
}

function phasesOnActiveFloor() {
  return [...new Set(pointsForActiveFloor().map(effectivePhase).filter(Boolean))];
}

// ── Pick an IFC room → autofill GlobalID + IFC type (+ label suggestion) ──
function selectRoom(pointId, roomGlobalId) {
  const fl = activeFloor();
  const room = fl && (fl.rooms || []).find((r) => r.globalId === roomGlobalId);
  const pt = state.points.find((p) => p.id === pointId);
  if (room) {
    const patch = { roomId: room.globalId, globalId: room.globalId, ifcType: room.ifcType };
    if (pt && (!pt.label || /^Point \d+$/.test(pt.label))) patch.label = room.name;
    updatePoint(pointId, patch);
    toast(`Linked to ${room.name} (${room.ifcType})`);
  } else {
    updatePoint(pointId, { roomId: "" }); // custom — GlobalID/IFC become editable
  }
}

// The room a point represents (linked IFC room name, else its label) — used to
// filter the linked Facility/Schedule tables.
function roomForPoint(pt) {
  const fl = activeFloor();
  return (pt.roomId && fl && (fl.rooms || []).find((r) => r.globalId === pt.roomId)) || null;
}
function roomNameForPoint(pt) {
  const room = roomForPoint(pt);
  return room ? room.name : (pt.label || "");
}
// Values a point's room can be matched on (globalId + IFC props), for the tables.
function roomKeys(pt) {
  const room = roomForPoint(pt);
  return { globalId: pt.globalId || (room && room.globalId) || "", ...((room && room.props) || {}) };
}

const PROP_LABELS = { globalId: "GlobalID", number: "Room number", department: "Department", building: "Building", zone: "Zone", level: "Level" };
function propLabel(k) { return PROP_LABELS[k] || (k.charAt(0).toUpperCase() + k.slice(1)); }

// Filter keys for the linked tables = GlobalID + the configured identification props.
function filterKeysObj() {
  const obj = { globalId: { label: "GlobalID" } };
  state.idProps.forEach((k) => { obj[k] = { label: propLabel(k) }; });
  return obj;
}

// ⚙ — choose which IFC room properties are identification fields (and filter keys).
// Edits are staged in a draft and only applied on OK (Cancel discards).
function openIdPropsConfig() {
  const avail = availableProps();
  const draft = new Set(state.idProps);
  const node = document.createElement("div");
  node.innerHTML =
    `<div class="cfg-hint">Pick the IFC room properties to show as identification fields. They also become filter keys for the linked tables.</div>` +
    avail.map((k) =>
      `<label class="cfg-row"><input type="checkbox" data-prop="${escHtml(k)}" ${draft.has(k) ? "checked" : ""}/> <span>${escHtml(propLabel(k))}</span> <span class="cfg-key">${escHtml(k)}</span></label>`).join("") +
    (avail.length ? "" : `<div class="dp-empty-sm">No room properties found in the IFC.</div>`) +
    `<div class="modal-actions"><button class="btn btn-sm" data-act="cancel">Cancel</button><button class="btn btn-p btn-sm" data-act="ok">OK</button></div>`;
  node.querySelectorAll("[data-prop]").forEach((cb) => {
    cb.addEventListener("change", () => { if (cb.checked) draft.add(cb.dataset.prop); else draft.delete(cb.dataset.prop); });
  });
  node.querySelector('[data-act="cancel"]').addEventListener("click", closeModal);
  node.querySelector('[data-act="ok"]').addEventListener("click", () => {
    setIdProps(avail.filter((k) => draft.has(k))); // keep available-props order
    closeModal();
    toast("Identification fields updated");
  });
  openModal("Identification fields", node);
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ── Bottom-timeline archive toggle (Photos / 360°), with per-point counts so an
//    empty active archive doesn't hide media that lives in the other one. ──
function renderArchiveToggle(sel) {
  const t = state.archiveType;
  const nPhoto = sel ? sel.media.filter((m) => m.type === "photo").length : 0;
  const n360 = sel ? sel.media.filter((m) => m.type === "360").length : 0;
  const ct = (n) => `<span class="seg-ct">${n}</span>`;
  els.tlTypes.innerHTML =
    `<div class="seg">` +
    `<button class="seg-b ${t === "photo" ? "on" : ""}" data-arch="photo">Photos${ct(nPhoto)}</button>` +
    `<button class="seg-b ${t === "360" ? "on" : ""}" data-arch="360">360°${ct(n360)}</button>` +
    `</div>`;
  els.tlTypes.querySelectorAll("[data-arch]").forEach((b) =>
    b.addEventListener("click", () => setArchiveType(b.dataset.arch)));
}

// ── Timeline layout toggle (Thumbs / Details) ──
function renderTimelineView() {
  const v = state.timelineView;
  els.tlView.innerHTML =
    `<div class="seg">` +
    `<button class="seg-b ${v === "thumbs" ? "on" : ""}" data-tlview="thumbs">▦ Thumbs</button>` +
    `<button class="seg-b ${v === "details" ? "on" : ""}" data-tlview="details">☰ Details</button>` +
    `</div>`;
  els.tlView.querySelectorAll("[data-tlview]").forEach((b) =>
    b.addEventListener("click", () => setTimelineView(b.dataset.tlview)));
}

// ── Timeline sort (Date / Time / Name / Description + direction) ──
function renderTimelineSort() {
  const { key, dir } = state.sort;
  els.tlSort.innerHTML =
    `<span class="etl-sortlbl">Sort</span>` +
    `<select class="phase-sel" data-sortkey>` +
    [["date", "Date"], ["time", "Time"], ["name", "Name"], ["description", "Description"]]
      .map(([v, l]) => `<option value="${v}" ${key === v ? "selected" : ""}>${l}</option>`).join("") +
    `</select>` +
    `<button class="btn btn-sm etl-sortdir" data-sortdir title="${dir === "asc" ? "Ascending" : "Descending"}">${dir === "asc" ? "↑" : "↓"}</button>`;
  els.tlSort.querySelector("[data-sortkey]").addEventListener("change", (e) => setSortKey(e.target.value));
  els.tlSort.querySelector("[data-sortdir]").addEventListener("click", () => toggleSortDir());
}

// ── Media editor: name / code / date / time / phase / description (OK / Cancel) ──
function openMediaEditor(pointId, mediaId) {
  const p = state.points.find((p) => p.id === pointId);
  const m = p && p.media.find((x) => x.id === mediaId);
  if (!m) return;

  const node = document.createElement("div");
  node.className = "med-edit";
  node.innerHTML =
    `<div class="med-prev">` +
      `<img src="${escHtml(m.src)}" alt="" />${m.type === "360" ? `<span class="thumb-360">360°</span>` : ""}` +
      `<button class="med-view" data-view title="Open full ${m.type === "360" ? "360° viewer" : "photo"}">▶ view full</button></div>` +
    `<label class="fld"><span>Name</span><input class="fld-in" data-f="label" value="${escHtml(m.label)}" placeholder="e.g. NE corner" /></label>` +
    `<label class="fld"><span>Photo code</span><input class="fld-in" data-f="code" value="${escHtml(m.code)}" placeholder="e.g. IMG_4821" /></label>` +
    `<div class="med-row2">` +
      `<label class="fld"><span>Date</span><input class="fld-in" type="date" data-f="date" value="${escHtml(m.date)}" /></label>` +
      `<label class="fld"><span>Time</span><input class="fld-in" type="time" data-f="time" value="${escHtml(m.time)}" /></label>` +
    `</div>` +
    `<label class="fld"><span>Description</span><textarea class="fld-in med-desc" data-f="description" rows="3" placeholder="Notes…">${escHtml(m.description)}</textarea></label>` +
    `<div class="modal-actions"><button class="btn btn-r btn-sm" data-act="del">Delete</button><span class="ma-spacer"></span><button class="btn btn-sm" data-act="cancel">Cancel</button><button class="btn btn-p btn-sm" data-act="ok">OK</button></div>`;

  node.querySelector("[data-view]").addEventListener("click", () => { closeModal(); openMedia(pointId, mediaId); });
  node.querySelector('[data-act="ok"]').addEventListener("click", () => {
    const patch = {};
    node.querySelectorAll("[data-f]").forEach((el) => { patch[el.dataset.f] = el.value; });
    setMediaMeta(pointId, mediaId, patch);
    closeModal();
    toast("Photo details saved");
  });
  node.querySelector('[data-act="cancel"]').addEventListener("click", closeModal);
  node.querySelector('[data-act="del"]').addEventListener("click", () => { closeModal(); removeMediaWithUndo(pointId, mediaId); });
  openModal(m.type === "360" ? "360° details" : "Photo details", node);
}

// ── Delete a point — confirm first (it also removes the point's photos) ──
function confirmDeletePoint(id) {
  const p = state.points.find((x) => x.id === id);
  if (!p) return;
  const n = p.media.length;
  const node = document.createElement("div");
  node.innerHTML =
    `<div class="cfg-hint">Delete <b>${escHtml(p.label || "this point")}</b>` +
    (n ? ` and its ${n} photo${n > 1 ? "s" : ""}` : "") +
    `? This can't be undone.</div>` +
    `<div class="modal-actions"><button class="btn btn-sm" data-act="cancel">Cancel</button><button class="btn btn-r btn-sm" data-act="ok">Delete</button></div>`;
  node.querySelector('[data-act="cancel"]').addEventListener("click", closeModal);
  node.querySelector('[data-act="ok"]').addEventListener("click", () => { deletePoint(id); closeModal(); toast("Point deleted"); });
  openModal("Delete point", node);
}

// ── Remove a media item but offer an Undo (re-inserts it at its old position) ──
function removeMediaWithUndo(pid, mid) {
  const p = state.points.find((x) => x.id === pid);
  const idx = p ? p.media.findIndex((m) => m.id === mid) : -1;
  const snapshot = idx >= 0 ? p.media[idx] : null;
  removeMedia(pid, mid);
  if (snapshot) toast("Photo removed", { label: "Undo", fn: () => restoreMedia(pid, snapshot, idx) });
  else toast("Photo removed");
}

// ── Point list + search (jump to any point across floors) ──
function openPointList() {
  const node = document.createElement("div");
  node.className = "ptl";
  node.innerHTML =
    `<input class="fld-in ptl-search" placeholder="Search label / room / GlobalID…" />` +
    `<div class="ptl-list"></div>`;
  const search = node.querySelector(".ptl-search");
  const listEl = node.querySelector(".ptl-list");

  const matches = (p, term) => {
    if (!term) return true;
    return (p.label || "").toLowerCase().includes(term) ||
      (p.globalId || "").toLowerCase().includes(term) ||
      roomNameForPoint(p).toLowerCase().includes(term);
  };
  const floorName = (fid) => { const f = state.floors.find((x) => x.id === fid); return f ? f.name : ""; };
  const draw = () => {
    const term = (search.value || "").trim().toLowerCase();
    // Grouped by point kind (Photo / Camera / Sensor / Custom).
    const matched = state.points.filter((p) => matches(p, term));
    const groups = [];
    ["photo", "camera", "sensor"].forEach((k) => {
      const pts = matched.filter((p) => pointKind(p) === k);
      if (pts.length) groups.push({ label: POINT_KINDS[k].label, glyph: k !== "photo" ? POINT_KINDS[k].glyph : "", pts });
    });
    // Custom points sub-group by their user-set type name.
    const customPts = matched.filter((p) => pointKind(p) === "custom");
    [...new Set(customPts.map((p) => pointKindLabel(p)))].forEach((nm) => {
      const pts = customPts.filter((p) => pointKindLabel(p) === nm);
      groups.push({ label: nm, glyph: pts[0].symbol || "◆", pts });
    });
    if (!groups.length) { listEl.innerHTML = `<div class="dp-empty-sm">No matching points</div>`; return; }
    listEl.innerHTML = groups.map((g) => {
      const head = g.label + (g.glyph ? " " + g.glyph : "");
      return `<div class="ptl-grp">${escHtml(head)} · ${g.pts.length}</div>` +
      g.pts.map((p) => {
        const ph = effectivePhase(p);
        const glyph = pointGlyph(p);
        const meta = [floorName(p.floorId), p.globalId ? "GID " + p.globalId.slice(0, 8) + "…" : "", ph].filter(Boolean).join("  ·  ");
        return `<div class="ptl-item${p.id === state.selectedId ? " on" : ""}">` +
          `<button class="ptl-jump" data-pid="${p.id}" title="Jump to this point">` +
            `<span class="ptl-dot" style="background:${phaseColor(ph)}"></span>` +
            (glyph ? `<span class="ptl-sym">${escHtml(glyph)}</span>` : "") +
            `<span class="ptl-name">${escHtml(p.label || "(point)")}</span>` +
            `<span class="ptl-meta">${escHtml(meta)}</span>` +
          `</button>` +
          `<button class="ptl-del" data-del-pid="${p.id}" title="Delete this point (and its photos)">✕</button>` +
          `</div>`;
      }).join("");
    }).join("");
    listEl.querySelectorAll("[data-pid]").forEach((b) => b.addEventListener("click", () => {
      const p = state.points.find((x) => x.id === b.dataset.pid);
      if (p) { setActiveFloor(p.floorId); selectPoint(p.id); closeModal(); }
    }));
    // Delete a point straight from the list (handy for points orphaned when a
    // plan is removed). Two-step confirm so it isn't an accidental click.
    listEl.querySelectorAll("[data-del-pid]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      if (b.dataset.armed === "1") {
        deletePoint(b.dataset.delPid);
        toast("Point deleted");
        const t = document.getElementById("modalTitle");
        if (t) t.textContent = `Points · ${state.points.length}`;
        draw();
      } else {
        b.dataset.armed = "1";
        b.textContent = "Delete?";
        b.classList.add("armed");
        setTimeout(() => { if (b.isConnected) { b.dataset.armed = ""; b.textContent = "✕"; b.classList.remove("armed"); } }, 3000);
      }
    }));
  };
  search.addEventListener("input", draw);
  draw();
  openModal(`Points · ${state.points.length}`, node);
  setTimeout(() => search.focus(), 50);
}

// ── Phase manager: rename / recolour / delete phases (+ quick add) ──
function openPhaseManager() {
  const node = document.createElement("div");
  const draw = () => {
    node.innerHTML =
      (state.phases.length ? state.phases.map((ph) => {
        const used = state.points.filter((p) => p.phase === ph).length;
        return `<div class="phm-row">` +
          `<input type="color" class="phm-color" data-color="${escHtml(ph)}" value="${escHtml(phaseColor(ph))}" title="Pin colour" />` +
          `<input class="fld-in phm-name" data-rename="${escHtml(ph)}" value="${escHtml(ph)}" />` +
          `<span class="phm-use" title="Points using this phase">${used}</span>` +
          `<button class="btn btn-r btn-sm phm-del" data-del="${escHtml(ph)}" title="Delete phase">✕</button>` +
          `</div>`;
      }).join("") : `<div class="dp-empty-sm">No phases yet.</div>`) +
      `<div class="np-row phm-add"><input class="fld-in" id="phmNew" placeholder="New phase name" /><button class="btn btn-p btn-sm" id="phmAdd">Add</button></div>` +
      `<div class="fm-note">Renaming updates every point using the phase. Deleting clears it from its points (they go grey).</div>`;
    bind();
  };
  const bind = () => {
    node.querySelectorAll("[data-color]").forEach((inp) =>
      inp.addEventListener("input", () => setPhaseColor(inp.dataset.color, inp.value)));
    node.querySelectorAll("[data-rename]").forEach((inp) =>
      inp.addEventListener("change", () => {
        if (renamePhase(inp.dataset.rename, inp.value)) draw();
        else { inp.value = inp.dataset.rename; toast("Name is empty or already used"); }
      }));
    node.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => { deletePhase(b.dataset.del); draw(); }));
    const ni = node.querySelector("#phmNew");
    const submit = () => { const n = addPhase(ni.value); if (n) { ni.value = ""; draw(); } };
    node.querySelector("#phmAdd").addEventListener("click", submit);
    ni.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  };
  draw();
  openModal("Manage phases", node);
}

// ── Compare two versions of the selected point (within the active archive) ──
function fmtD(d) { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || ""); return m ? `${m[1]}-${m[2]}-${m[3]}` : (d || "—"); }
// A label that actually distinguishes versions: date + time + name/code.
function versionLabel(m) {
  const t = m.time ? " " + m.time : "";
  const nm = m.label || m.code || "";
  return fmtD(m.date) + t + (nm ? " · " + nm : "");
}
function versionsSorted(list) {
  return [...list].sort((a, b) => (a.date > b.date ? 1 : a.date < b.date ? -1 : (a.addedAt || 0) - (b.addedAt || 0)));
}

function openCompare(pointId) {
  const p = state.points.find((p) => p.id === pointId);
  // Only versions of the currently-shown archive are comparable (photo↔photo or 360↔360).
  const pool = p ? p.media.filter((m) => m.type === state.archiveType) : [];
  if (pool.length < 2) { toast(`Need 2+ ${state.archiveType === "360" ? "360°" : "photo"} versions to compare`); return; }
  const vers = versionsSorted(pool);
  const opt = (m) => `<option value="${m.id}">${escHtml(versionLabel(m))}</option>`;
  els.cmpLeft.innerHTML = vers.map(opt).join("");
  els.cmpRight.innerHTML = vers.map(opt).join("");
  els.cmpLeft.value = vers[0].id;                 // oldest
  els.cmpRight.value = vers[vers.length - 1].id;  // newest
  cmpDiv = 50;
  els.compare.hidden = false;
  renderCompareView();
}

function renderCompareView() {
  const p = state.points.find((x) => x.id === state.selectedId);
  if (!p) return;
  if (compareInstance) { compareInstance.dispose(); compareInstance = null; }
  els.cmpMediaL.innerHTML = ""; els.cmpMediaR.innerHTML = ""; els.cmpPano.innerHTML = "";

  const lm = p.media.find((m) => m.id === els.cmpLeft.value);
  const rm = p.media.find((m) => m.id === els.cmpRight.value);
  if (!lm || !rm) return;
  els.cmpLblL.textContent = versionLabel(lm);
  els.cmpLblR.textContent = versionLabel(rm);

  if (lm.type === "360" && rm.type === "360") {
    // synchronised spherical compare — stage drag rotates, divider line wipes
    cmpMode = "pano";
    els.cmpStage.classList.add("pano");
    import("./viewer/compare.js").then(({ createPanoCompare }) => {
      if (els.compare.hidden) return; // closed before load finished
      compareInstance = createPanoCompare(els.cmpPano, lm.src, rm.src);
      compareInstance.setDivider(cmpDiv / 100);
    }).catch((err) => { console.error("[explore] compare failed", err); toast("Compare failed — see console"); });
  } else {
    // flat before/after — drag anywhere on the stage moves the divider
    cmpMode = "photo";
    els.cmpStage.classList.remove("pano");
    els.cmpMediaL.innerHTML = `<img src="${lm.src}" alt="" draggable="false" />`;
    els.cmpMediaR.innerHTML = `<img src="${rm.src}" alt="" draggable="false" />`;
  }
  applyDivider();
}

function applyDivider() {
  els.cmpDivider.style.left = cmpDiv + "%";
  els.cmpMediaR.style.clipPath = `inset(0 0 0 ${cmpDiv}%)`;
  if (compareInstance) compareInstance.setDivider(cmpDiv / 100);
}

function closeCompare() {
  if (compareInstance) { compareInstance.dispose(); compareInstance = null; }
  els.compare.hidden = true;
  els.cmpMediaL.innerHTML = ""; els.cmpMediaR.innerHTML = ""; els.cmpPano.innerHTML = "";
}

// ── Floor manager (rename / delete / reorder / add) ──
let reopenManagerAfterImport = false;
function openFloorManager() {
  const node = buildFloorManager(state.floors, {
    onRename: (id, patch) => updateFloor(id, patch),
    onDelete: (id) => {
      if (deleteFloor(id)) { toast("Floor deleted"); openFloorManager(); } // rebuild list
      else toast("Can't delete the last floor");
    },
    onMove: (id, dir) => { if (moveFloor(id, dir)) openFloorManager(); },
    onAdd: () => { reopenManagerAfterImport = true; els.fileInput.click(); },
  });
  // Reset-to-demo: discard the saved session and reload the original demo data.
  const reset = document.createElement("button");
  reset.className = "btn btn-r btn-sm fm-reset";
  reset.textContent = "⟲ Reset to demo data";
  reset.title = "Discard saved points / photos / floors and restore the demo";
  reset.addEventListener("click", confirmReset);
  node.appendChild(reset);
  openModal("Edit floors", node);
}

function confirmReset() {
  const c = document.createElement("div");
  c.innerHTML =
    `<div class="cfg-hint">Discard all placed points, photos and imported floors and restore the original demo data? This can't be undone.</div>` +
    `<div class="modal-actions"><button class="btn btn-sm" data-act="cancel">Cancel</button><button class="btn btn-r btn-sm" data-act="ok">Reset</button></div>`;
  c.querySelector('[data-act="cancel"]').addEventListener("click", openFloorManager);
  c.querySelector('[data-act="ok"]').addEventListener("click", () => { clearSession(); location.reload(); });
  openModal("Reset to demo", c);
}

// ── Add a custom phase. onAdded(name) decides what to do with it
//    (default: set it as the attach phase; from the timeline: assign to that media). ──
function openAddPhase(onAdded) {
  const node = document.createElement("div");
  node.className = "np-row";
  node.innerHTML =
    `<input class="fld-in" id="npInput" placeholder="Phase name (e.g. Demolition, Handover)" />` +
    `<div class="modal-actions"><button class="btn btn-sm" data-act="cancel">Cancel</button><button class="btn btn-p btn-sm" id="npOk">Add</button></div>`;
  const input = node.querySelector("#npInput");
  const submit = () => {
    const name = addPhase(input.value);
    if (name) { (onAdded || setAttachPhase)(name); toast(`Phase added: ${name}`); }
    closeModal();
  };
  node.querySelector("#npOk").addEventListener("click", submit);
  node.querySelector('[data-act="cancel"]').addEventListener("click", closeModal);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  openModal("Add phase", node);
  setTimeout(() => input.focus(), 50);
}

// ── Bridge: outbound HOTSPOT_CLICKED + inbound FOCUS_ELEMENT ──
function emitHotspotClicked(pointId) {
  const p = state.points.find((p) => p.id === pointId);
  if (!p) return;
  emit(MSG.HOTSPOT_CLICKED, {
    globalId: p.globalId,
    ifcType: p.ifcType,
    pointId: p.id,
    floorId: p.floorId,
    label: p.label,
  });
}

// Resolve a GlobalID to a point, switch to its floor and select it (programmatic — no echo).
function focusByGlobalId(globalId) {
  const p = state.points.find((pt) => pt.globalId && pt.globalId === globalId);
  if (!p) return null;
  setActiveFloor(p.floorId);
  selectPoint(p.id);
  return p;
}

const CAPABILITIES = ["plan", "points", "photo", "360", "timeline", "compare", "focus", "multifloor"];

function snapshotState() {
  return {
    activeFloorId: state.activeFloorId,
    selectedId: state.selectedId,
    mode: state.mode,
    floors: state.floors.map((f) => ({ id: f.id, name: f.name, label: f.label })),
    pointCount: state.points.length,
    theme: document.documentElement.getAttribute("data-theme") || "dark",
  };
}

function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
}

// Embedded mode (set on VIEWER_INIT): when true, the toolbar "Floors" button asks
// the Castor host to open its Floor-plans manager instead of the module's own.
let isEmbedded = false;

initBridge({
  // Handshake: origin already locked by the router; apply any initial config.
  [MSG.VIEWER_INIT]: (msg) => {
    if (msg.theme) applyTheme(msg.theme);
    if (msg.floorId) setActiveFloor(msg.floorId);
    if (msg.focus) focusByGlobalId(msg.focus);
    // v0.2 — host can flag embedded mode. In that case, the standalone-only
    // toolbar controls (host owns plan upload + theme + persistence reset)
    // are hidden via the .castor-embedded body class (see components.css).
    isEmbedded = !!msg.embedded;
    if (msg.embedded) document.body.classList.add("castor-embedded");
    if (msg.palette) applyHostPalette(msg.palette); // match Castor's background exactly
    ack(msg.requestId, { ready: true, capabilities: CAPABILITIES });
    toast("Connected to Castor");
  },
  [MSG.SET_THEME]: (msg) => {
    applyTheme(msg.theme);
    if (msg.palette) applyHostPalette(msg.palette); // re-match on host theme switch
    ack(msg.requestId, { theme: msg.theme === "light" ? "light" : "dark" });
  },
  [MSG.FOCUS_ELEMENT]: (msg) => {
    if (!msg.id) { error(msg.requestId, "FOCUS_ELEMENT requires { id: GlobalID }", ERR.BAD_PAYLOAD); return; }
    const p = focusByGlobalId(msg.id);
    if (p) { ack(msg.requestId, { pointId: p.id, floorId: p.floorId }); toast("Focused: " + (p.label || p.globalId)); }
    else { error(msg.requestId, `No point with GlobalID ${msg.id}`, ERR.UNKNOWN_GLOBAL_ID); toast("GlobalID not found: " + msg.id); }
  },
  [MSG.GET_STATE]: (msg) => {
    // { full:true } → the entire serialisable working set (for host persistence)
    emit(MSG.STATE, msg.full ? exportFullState() : snapshotState(), msg.requestId);
  },
  [MSG.SET_FLOORS]: (msg) => {
    if (!Array.isArray(msg.floors)) { error(msg.requestId, "SET_FLOORS requires { floors: [...] }", ERR.BAD_PAYLOAD); return; }
    const ids = setFloors(msg.floors, !!msg.replace);
    ack(msg.requestId, { floorIds: ids });
    toast(`Host added ${ids.length} floor${ids.length > 1 ? "s" : ""}`);
  },
  [MSG.SET_ROOMS]: (msg) => {
    if (!msg.floorId || !Array.isArray(msg.rooms)) { error(msg.requestId, "SET_ROOMS requires { floorId, rooms: [...] }", ERR.BAD_PAYLOAD); return; }
    if (setRooms(msg.floorId, msg.rooms)) ack(msg.requestId, { floorId: msg.floorId, count: msg.rooms.length });
    else error(msg.requestId, `No floor ${msg.floorId}`, ERR.BAD_PAYLOAD);
  },
  [MSG.SET_TABLE_CATALOG]: (msg) => {
    if (!msg.tables || typeof msg.tables !== "object") { error(msg.requestId, "SET_TABLE_CATALOG requires { tables: {...} }", ERR.BAD_PAYLOAD); return; }
    setTableCatalog(msg.tables);
    render();
    ack(msg.requestId, { tables: Object.keys(msg.tables) });
  },
  // v0.2 — host pushes the saved working set on load (points, media, phases, etc.).
  // Shape mirrors exportFullState(). Missing fields keep their current value, so
  // the host can also send partial patches (e.g. only points + media on re-sync).
  [MSG.SET_USER_STATE]: (msg) => {
    if (!msg.state || typeof msg.state !== "object") { error(msg.requestId, "SET_USER_STATE requires { state: {...} }", ERR.BAD_PAYLOAD); return; }
    importFullState(msg.state);
    ack(msg.requestId, {
      floors: state.floors.length,
      points: state.points.length,
      phases: state.phases.length,
    });
  },
  // v0.3 — the host's Floor-plans manager toggles white-out per floor; we run the
  // (client-side) knockout on that floor and let it persist via STATE_CHANGED.
  [MSG.SET_FLOOR_KNOCKOUT]: (msg) => {
    if (!msg.floorId) { error(msg.requestId, "SET_FLOOR_KNOCKOUT requires { floorId }", ERR.BAD_PAYLOAD); return; }
    applyFloorKnockout(msg.floorId, !!msg.on);
    ack(msg.requestId, { floorId: msg.floorId, on: !!msg.on });
  },
});

// Notify the host whenever the working set changes (debounced in state.js), so it
// can persist authoritatively. Lightweight notice — host can GET_STATE { full:true }.
onStateChange((snap, savedLocally) => {
  emit(MSG.STATE_CHANGED, { floors: snap.floors.length, points: snap.points.length, savedLocally });
});

// Announce readiness to the host (goes to "*" until VIEWER_INIT locks the origin)
emit(MSG.VIEWER_READY, { version: "0.2", capabilities: CAPABILITIES });

// ── White-background knockout — applied per floor (by id). The toolbar button is
//    gone; this is driven by the host's Floor-plans manager (SET_FLOOR_KNOCKOUT)
//    when embedded, and stays available to the standalone floor manager.
async function applyFloorKnockout(floorId, on) {
  const floor = state.floors.find((f) => f.id === floorId);
  if (!floor) return;
  if (!on) {
    if (floor.knockout) {
      setFloorPlan(floor.id, floor.planOriginal || floor.plan);
      updateFloor(floor.id, { knockout: false });
      toast("White background restored");
    }
    return;
  }
  if (floor.knockout) return; // already knocked out
  toast("Knocking out white…");
  try {
    const { knockoutWhite } = await import("./floorplan/knockout.js");
    const out = await knockoutWhite(floor.plan);
    updateFloor(floor.id, { planOriginal: floor.plan, knockout: true });
    setFloorPlan(floor.id, out);
    toast("White background removed");
  } catch (err) {
    console.error("[explore] knockout failed", err);
    toast("Knock-out failed (cross-origin or load error)");
  }
}

// Toolbar "Floors" button: embedded → ask Castor to open its Floor-plans manager;
// standalone → open the module's own floor manager.
els.btnFloors.addEventListener("click", () => {
  if (isEmbedded) emit(MSG.OPEN_LEVELS, {});
  else openFloorManager();
});

// Header "Plans" gear: ask Castor to open a file-browser-style modal listing
// every uploaded / generated plan per storey (with per-image delete + "show
// this" toggle). Standalone mode has no such manager, so a click there only
// emits a no-op postMessage (no listener picks it up) and toasts a hint.
const btnPlansManager = document.getElementById("btnPlansManager");
if (btnPlansManager) {
  btnPlansManager.addEventListener("click", () => {
    if (isEmbedded) {
      emit(MSG.OPEN_PLANS_MANAGER, {});
    } else {
      toast("Plans Manager is available only inside Castor.");
    }
  });
}

// ── Boot ──
const BUILD = "build 6.40"; // bump on each change so a stale (cached) JS is obvious in the header
initModal();
// Restore a previously chosen standalone theme (host SET_THEME still overrides when embedded).
try { const savedTheme = localStorage.getItem(THEME_KEY); if (savedTheme) applyTheme(savedTheme); } catch (_) { /* ignore */ }
const buildEl = document.getElementById("mhBuild");
if (buildEl) buildEl.textContent = BUILD;
render();
toast("Explore ready · " + BUILD);
// eslint-disable-next-line no-console
console.log("[explore] ready —", BUILD, "· floors:", state.floors.length, "points:", state.points.length);
