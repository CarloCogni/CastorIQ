// annotations.js — Paint-style drawing overlay over the floor-plan image,
// with multiple named layers per storey.
//
// Architecture:
//   * The DOM has three layers stacked inside .plan-stage:
//     1. <img.plan-img>   — the floor plan (background)
//     2. <canvas.annot-canvas>  — Fabric.js drawing surface (this module)
//     3. <div.pin-layer>  — user pins (clickable in View mode)
//   * .exp-viewer carries `.draw-mode` while the drawing toolbar is open.
//     In that mode the annot-canvas captures pointer events; otherwise
//     events fall through to the pin layer (so pins stay clickable).
//
// Layers (v2 data shape):
//   annotations_json = {
//     "version": 2,
//     "layers": [
//       { "id": "lyr_xxx", "name": "Layer 1", "visible": true, "fabric": { ... } }
//     ],
//     "activeLayerId": "lyr_xxx"
//   }
//   * Every Fabric object carries a `layerId` custom property so we know which
//     layer to attach it to on save.
//   * Drawing always lands on the active layer (highlighted radio in the
//     Vrstvy panel).
//   * Hiding a layer flips `visible` on its objects (Fabric's per-object
//     visibility), so the user can compare with / without it.
//
// Backward-compat:
//   * v1 (raw Fabric JSON with .objects) hydrates as a single layer named
//     "Layer 1". The next save upgrades the stored shape to v2.

const COLOR_PRESETS = [
  // Row 1 — strong saturated palette + transparent slot
  "#000000", "#7e7e7e", "#7a1b1b", "#b30000", "#e64a19", "#ff9100",
  "#fbc02d", "#388e3c", "#1976d2", "#7b1fa2", "transparent",
  // Row 2 — soft pastels + skin / wood / paper tones
  "#ffffff", "#bdbdbd", "#a37b6b", "#f0a4a4", "#ffcdd2", "#ffe0b2",
  "#fff59d", "#c8e6c9", "#bbdefb", "#d1c4e9",
];

const STATE = {
  canvas: null,
  storeyId: null,
  saveUrl: null,
  tool: "select",
  brush: 4,
  color: "#000000",
  drag: null,
  history: { past: [], future: [], snapshotting: false },
  hydrating: false,
  saveTimer: null,
  // Layer registry. Persisted as part of annotations_json (v2).
  layers: [],            // [{ id, name, visible }]
  activeLayerId: null,
};

const HISTORY_CAP = 60;
const LAYER_OBJECT_KEYS = ["layerId"];  // Fabric serializer extra props

// Tiny event helpers so the rest of the module reads cleanly.
const $ = (id) => document.getElementById(id);
const all = (sel) => document.querySelectorAll(sel);

// ── Public API ──────────────────────────────────────────────────────────────

export function initAnnotations() {
  if (!window.fabric) {
    console.warn("[annotations] fabric.js not loaded yet — retrying once on next tick");
    setTimeout(initAnnotations, 80);
    return null;
  }
  const canvasEl = $("annotCanvas");
  const planImg = $("planImg");
  const viewer = document.querySelector(".exp-viewer");
  if (!canvasEl || !planImg) return null;

  const canvas = new window.fabric.Canvas(canvasEl, {
    backgroundColor: "transparent",
    selection: true,
    preserveObjectStacking: true,
  });
  STATE.canvas = canvas;

  syncCanvasToImage(planImg, canvas);
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => syncCanvasToImage(planImg, canvas));
    ro.observe(planImg);
  }
  planImg.addEventListener("load", () => {
    syncCanvasToImage(planImg, canvas);
  });

  paintSwatches();
  bindToolbar();
  bindCanvas(canvas, viewer);
  bindKeyboard();
  bindLayerControls();
  return canvas;
}

/** Show / hide the drawing toolbar and flip the viewer into draw mode. */
export function setDrawMode(on) {
  const bar = $("annotBar");
  const viewer = document.querySelector(".exp-viewer");
  const btn = $("btnAnnotate");
  if (!bar || !viewer) return;
  bar.hidden = !on;
  viewer.classList.toggle("draw-mode", !!on);
  if (btn) btn.classList.toggle("on", !!on);
  applyTool(STATE.tool);
}

/** Hydrate from a floor descriptor's annotations payload. Safe to call
 *  any time; clears the canvas first and skips save during load. */
export function loadAnnotations(annotations) {
  const canvas = STATE.canvas;
  if (!canvas) return;
  STATE.hydrating = true;
  canvas.clear();
  const { layers, activeLayerId, objectsFlat } = parseLayers(annotations);
  STATE.layers = layers;
  STATE.activeLayerId = activeLayerId || (layers[0] ? layers[0].id : null);

  if (objectsFlat.length) {
    // Build one Fabric JSON payload with all objects (each carrying its
    // layerId) and let Fabric instantiate them in one go. Visibility is
    // then applied to each object based on its layer's `visible` flag.
    canvas.loadFromJSON({ objects: objectsFlat }, () => {
      applyLayerVisibility();
      canvas.renderAll();
      STATE.hydrating = false;
      STATE.history.past = [];
      STATE.history.future = [];
      pushHistory();
      renderLayerList();
    });
  } else {
    STATE.hydrating = false;
    STATE.history.past = [];
    STATE.history.future = [];
    pushHistory();
    renderLayerList();
  }
}

/** Tell the module which storey is active + which URL to POST saves to.
 *  Critical: we flush any pending save against the OLD saveUrl first.
 *  Without that, the 600 ms debounce timer for the previous storey's edits
 *  would fire after we've swapped the URL, sending the (now reloaded) new
 *  storey's data to the new URL and silently dropping the previous storey's
 *  unsaved drawings on the floor. */
export function attachStorey(storeyId, saveUrl) {
  if (STATE.saveTimer && STATE.saveUrl) {
    clearTimeout(STATE.saveTimer);
    STATE.saveTimer = null;
    saveNow();  // saves using the CURRENT (old) saveUrl
  }
  STATE.storeyId = storeyId;
  STATE.saveUrl = saveUrl || null;
}

// ── Layer parsing / serialization ──────────────────────────────────────────

function parseLayers(annotations) {
  // Returns { layers, activeLayerId, objectsFlat } where objectsFlat is a
  // single concatenated array of Fabric objects (each tagged with layerId).
  if (annotations && annotations.version === 2 && Array.isArray(annotations.layers)) {
    const layers = annotations.layers.map((l) => ({
      id: l.id || newLayerId(),
      name: l.name || "Layer",
      visible: l.visible !== false,
    }));
    const objectsFlat = [];
    annotations.layers.forEach((l, idx) => {
      const lyrId = layers[idx].id;
      const fabricObjs = (l.fabric && Array.isArray(l.fabric.objects)) ? l.fabric.objects : [];
      fabricObjs.forEach((o) => {
        objectsFlat.push({ ...o, layerId: lyrId });
      });
    });
    return {
      layers: layers.length ? layers : [defaultLayer()],
      activeLayerId: annotations.activeLayerId || (layers[0] ? layers[0].id : null),
      objectsFlat,
    };
  }
  // v1 / unknown shape — treat as a single "Layer 1".
  const layer = defaultLayer();
  const objs = (annotations && Array.isArray(annotations.objects)) ? annotations.objects : [];
  const objectsFlat = objs.map((o) => ({ ...o, layerId: layer.id }));
  return { layers: [layer], activeLayerId: layer.id, objectsFlat };
}

function defaultLayer() {
  return { id: newLayerId(), name: "Vrstva 1", visible: true };
}

function newLayerId() {
  return "lyr_" + Math.random().toString(36).slice(2, 10);
}

function serializeLayers() {
  // Pull objects out of Fabric, group them by their layerId, and pack into
  // the v2 shape. Objects that ended up unsmagged (no layerId, e.g. legacy
  // imports) are forced onto the active layer so they're not lost.
  const canvas = STATE.canvas;
  if (!canvas) return { version: 2, layers: [], activeLayerId: null };
  const buckets = new Map();
  STATE.layers.forEach((l) => buckets.set(l.id, []));
  const fallbackId = STATE.activeLayerId || (STATE.layers[0] ? STATE.layers[0].id : null);
  const objects = canvas.toJSON(LAYER_OBJECT_KEYS).objects || [];
  objects.forEach((o) => {
    const id = o.layerId && buckets.has(o.layerId) ? o.layerId : fallbackId;
    if (!id) return;
    if (!buckets.has(id)) buckets.set(id, []);
    buckets.get(id).push(o);
  });
  return {
    version: 2,
    layers: STATE.layers.map((l) => ({
      id: l.id,
      name: l.name,
      visible: l.visible,
      fabric: { version: window.fabric ? window.fabric.version : null, objects: buckets.get(l.id) || [] },
    })),
    activeLayerId: STATE.activeLayerId,
  };
}

function applyLayerVisibility() {
  const canvas = STATE.canvas;
  if (!canvas) return;
  const byId = new Map(STATE.layers.map((l) => [l.id, l]));
  canvas.forEachObject((o) => {
    const layer = byId.get(o.layerId);
    o.visible = layer ? layer.visible !== false : true;
  });
  canvas.requestRenderAll();
}

// ── Layer list rendering + controls ────────────────────────────────────────

function renderLayerList() {
  const mount = $("annotLayers");
  if (!mount) return;
  mount.innerHTML = "";
  STATE.layers.forEach((layer) => {
    const row = document.createElement("div");
    row.className = "annot-layer" + (layer.id === STATE.activeLayerId ? " on" : "") + (layer.visible === false ? " hidden" : "");
    row.dataset.id = layer.id;

    // Eye toggle
    const eye = document.createElement("button");
    eye.type = "button";
    eye.className = "annot-layer-eye";
    eye.textContent = layer.visible === false ? "🚫" : "👁";
    eye.title = layer.visible === false ? "Show layer" : "Hide layer";
    eye.addEventListener("click", () => toggleLayer(layer.id));

    // Active radio
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "annot-layer-active";
    radio.className = "annot-layer-radio";
    radio.checked = layer.id === STATE.activeLayerId;
    radio.title = "Make this the active layer (new strokes land here)";
    radio.addEventListener("change", () => setActiveLayer(layer.id));

    // Editable name — always live, no double-click gate. Click anywhere in
    // the field to drop the cursor; type, then click out / press Enter to
    // commit. Escape reverts.
    const name = document.createElement("input");
    name.type = "text";
    name.className = "annot-layer-name";
    name.value = layer.name;
    name.title = "Click to rename this layer";
    name.spellcheck = false;
    name.addEventListener("blur", () => {
      const val = (name.value || "").trim() || layer.name;
      renameLayer(layer.id, val);
    });
    name.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); name.blur(); }
      if (e.key === "Escape") { name.value = layer.name; name.blur(); }
    });

    // Delete (only if >1 layer)
    const del = document.createElement("button");
    del.type = "button";
    del.className = "annot-layer-del";
    del.textContent = "🗑";
    del.title = "Delete this layer + its drawings";
    del.disabled = STATE.layers.length <= 1;
    del.addEventListener("click", () => deleteLayer(layer.id));

    row.append(eye, radio, name, del);
    mount.appendChild(row);
  });
}

function bindLayerControls() {
  const addBtn = $("annotLayerAdd");
  if (addBtn) addBtn.addEventListener("click", addLayer);
}

function addLayer() {
  const idx = STATE.layers.length + 1;
  const layer = { id: newLayerId(), name: "Vrstva " + idx, visible: true };
  STATE.layers.push(layer);
  STATE.activeLayerId = layer.id;
  renderLayerList();
  pushHistory();
  triggerSave();
}

function setActiveLayer(id) {
  if (!STATE.layers.some((l) => l.id === id)) return;
  STATE.activeLayerId = id;
  renderLayerList();
  triggerSave();
}

function renameLayer(id, name) {
  const layer = STATE.layers.find((l) => l.id === id);
  if (!layer) return;
  if (layer.name === name) return;
  layer.name = name;
  renderLayerList();
  triggerSave();
}

function toggleLayer(id) {
  const layer = STATE.layers.find((l) => l.id === id);
  if (!layer) return;
  layer.visible = !layer.visible;
  applyLayerVisibility();
  renderLayerList();
  triggerSave();
}

function deleteLayer(id) {
  if (STATE.layers.length <= 1) return;
  const layer = STATE.layers.find((l) => l.id === id);
  if (!layer) return;
  if (!confirm(`Delete layer "${layer.name}" and all its drawings?`)) return;
  const canvas = STATE.canvas;
  if (canvas) {
    const toRemove = canvas.getObjects().filter((o) => o.layerId === id);
    toRemove.forEach((o) => canvas.remove(o));
  }
  STATE.layers = STATE.layers.filter((l) => l.id !== id);
  if (STATE.activeLayerId === id) {
    STATE.activeLayerId = STATE.layers[0] ? STATE.layers[0].id : null;
  }
  renderLayerList();
  pushHistory();
  triggerSave();
}

// ── Canvas sizing / toolbar / tools ────────────────────────────────────────

function syncCanvasToImage(planImg, canvas) {
  const w = planImg.offsetWidth || planImg.clientWidth || 1;
  const h = planImg.offsetHeight || planImg.clientHeight || 1;
  // Position Fabric's wrapper to match the IMG's offset inside the stage —
  // otherwise the wrapper sits at (0, 0) of the stage and the user's strokes
  // misalign with the image whenever it's letterboxed (object-fit: contain
  // around a non-square plan). Re-apply every sync, not just on dim change:
  // the IMG's offsetLeft/Top can shift while w×h stays the same (e.g. when
  // a sibling element resizes around it), and the stale top/left would leave
  // big swaths of the plan undrawable.
  if (canvas.wrapperEl) {
    canvas.wrapperEl.style.position = "absolute";
    canvas.wrapperEl.style.left = planImg.offsetLeft + "px";
    canvas.wrapperEl.style.top = planImg.offsetTop + "px";
  }
  if (canvas.getWidth() === w && canvas.getHeight() === h) return;
  canvas.setWidth(w);
  canvas.setHeight(h);
  canvas.requestRenderAll();
}

function paintSwatches() {
  const mount = $("annotColors");
  if (!mount) return;
  mount.innerHTML = "";
  COLOR_PRESETS.forEach((color, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "annot-color" + (color === "transparent" ? " transparent" : "");
    if (color !== "transparent") btn.style.background = color;
    btn.dataset.color = color;
    btn.title = color === "transparent" ? "No fill (outline only)" : color;
    if (i === 0) btn.classList.add("on");
    btn.addEventListener("click", () => selectColor(color, btn));
    mount.appendChild(btn);
  });
  STATE.color = COLOR_PRESETS[0];

  const custom = $("annotColorCustom");
  if (custom) {
    custom.addEventListener("input", () => {
      selectColor(custom.value, null);
    });
  }
}

function selectColor(color, btn) {
  STATE.color = color;
  all(".annot-color").forEach((el) => el.classList.remove("on"));
  if (btn) btn.classList.add("on");
  const canvas = STATE.canvas;
  if (canvas && canvas.freeDrawingBrush) {
    canvas.freeDrawingBrush.color = color === "transparent" ? "#000000" : color;
  }
}

function bindToolbar() {
  all('[data-tool]').forEach((btn) => {
    btn.addEventListener("click", () => {
      all('[data-tool]').forEach((el) => el.classList.remove("on"));
      all('[data-shape]').forEach((el) => el.classList.remove("on"));
      btn.classList.add("on");
      applyTool(btn.dataset.tool);
    });
  });
  all('[data-shape]').forEach((btn) => {
    btn.addEventListener("click", () => {
      all('[data-tool]').forEach((el) => el.classList.remove("on"));
      all('[data-shape]').forEach((el) => el.classList.remove("on"));
      btn.classList.add("on");
      applyTool("shape:" + btn.dataset.shape);
    });
  });
  all('[data-brush]').forEach((btn) => {
    btn.addEventListener("click", () => {
      all('[data-brush]').forEach((el) => el.classList.remove("on"));
      btn.classList.add("on");
      STATE.brush = parseFloat(btn.dataset.brush);
      const canvas = STATE.canvas;
      if (canvas && canvas.freeDrawingBrush) {
        canvas.freeDrawingBrush.width = STATE.brush;
      }
    });
  });
  const undo = $("annotUndo");
  const redo = $("annotRedo");
  const clear = $("annotClear");
  if (undo) undo.addEventListener("click", undoStep);
  if (redo) redo.addEventListener("click", redoStep);
  if (clear) clear.addEventListener("click", () => {
    if (!STATE.canvas || !STATE.activeLayerId) return;
    const layer = STATE.layers.find((l) => l.id === STATE.activeLayerId);
    if (!confirm(`Clear all drawings on layer "${layer ? layer.name : "active"}"?`)) return;
    const toRemove = STATE.canvas.getObjects().filter((o) => o.layerId === STATE.activeLayerId);
    toRemove.forEach((o) => STATE.canvas.remove(o));
    pushHistory();
    triggerSave();
  });
}

function applyTool(tool) {
  STATE.tool = tool;
  const canvas = STATE.canvas;
  if (!canvas) return;
  canvas.isDrawingMode = false;
  canvas.selection = false;
  canvas.forEachObject((o) => { o.selectable = false; o.evented = false; });

  if (tool === "select") {
    canvas.selection = true;
    canvas.forEachObject((o) => { o.selectable = true; o.evented = true; });
    canvas.defaultCursor = "default";
  } else if (tool === "pencil") {
    canvas.isDrawingMode = true;
    if (canvas.freeDrawingBrush) {
      canvas.freeDrawingBrush.color = STATE.color === "transparent" ? "#000000" : STATE.color;
      canvas.freeDrawingBrush.width = STATE.brush;
    }
  } else if (tool === "eraser") {
    canvas.defaultCursor = "not-allowed";
    canvas.forEachObject((o) => { o.evented = true; });
  } else if (tool === "text") {
    canvas.defaultCursor = "text";
  }
  canvas.requestRenderAll();
}

function bindCanvas(canvas, viewer) {
  canvas.on("mouse:down", (opt) => {
    if (STATE.tool === "eraser" && opt.target) {
      canvas.remove(opt.target);
      pushHistory();
      triggerSave();
      return;
    }
    if (STATE.tool === "text") {
      const p = canvas.getPointer(opt.e);
      const txt = new window.fabric.IText("text", {
        left: p.x, top: p.y,
        fontFamily: "Inter, sans-serif",
        fontSize: 16 + STATE.brush * 2,
        fill: STATE.color === "transparent" ? "#000000" : STATE.color,
        editable: true,
        layerId: STATE.activeLayerId,
      });
      canvas.add(txt);
      canvas.setActiveObject(txt);
      txt.enterEditing();
      pushHistory();
      triggerSave();
      return;
    }
    if (typeof STATE.tool === "string" && STATE.tool.startsWith("shape:")) {
      const p = canvas.getPointer(opt.e);
      STATE.drag = { x0: p.x, y0: p.y, ghost: null };
    }
  });

  canvas.on("mouse:move", (opt) => {
    if (!STATE.drag) return;
    const p = canvas.getPointer(opt.e);
    const { x0, y0 } = STATE.drag;
    if (STATE.drag.ghost) canvas.remove(STATE.drag.ghost);
    const shape = STATE.tool.slice("shape:".length);
    STATE.drag.ghost = buildShape(shape, x0, y0, p.x, p.y);
    if (STATE.drag.ghost) {
      STATE.drag.ghost.selectable = false;
      STATE.drag.ghost.evented = false;
      STATE.drag.ghost.layerId = STATE.activeLayerId;
      canvas.add(STATE.drag.ghost);
      canvas.requestRenderAll();
    }
  });

  canvas.on("mouse:up", () => {
    if (STATE.drag && STATE.drag.ghost) {
      // The ghost becomes the committed shape — keep it on the active layer.
      STATE.drag.ghost.layerId = STATE.activeLayerId;
      STATE.drag.ghost.selectable = false;
      STATE.drag.ghost.evented = false;
      STATE.drag = null;
      pushHistory();
      triggerSave();
    } else {
      STATE.drag = null;
    }
  });

  canvas.on("path:created", (e) => {
    // Pencil committed a new path — assign it to the active layer.
    if (e && e.path) e.path.layerId = STATE.activeLayerId;
    pushHistory();
    triggerSave();
  });
  canvas.on("object:modified", () => {
    pushHistory();
    triggerSave();
  });
}

function buildShape(kind, x0, y0, x1, y1) {
  const F = window.fabric;
  if (!F) return null;
  const left = Math.min(x0, x1);
  const top = Math.min(y0, y1);
  const w = Math.abs(x1 - x0);
  const h = Math.abs(y1 - y0);
  const stroke = STATE.color === "transparent" ? "#000000" : STATE.color;
  const strokeWidth = STATE.brush;
  const fillColor = STATE.color === "transparent" ? "transparent" : STATE.color;
  const common = { stroke, strokeWidth, fill: "transparent" };

  switch (kind) {
    case "line":
      return new F.Line([x0, y0, x1, y1], { stroke, strokeWidth, strokeLineCap: "round" });
    case "rect":
      return new F.Rect({ left, top, width: w, height: h, ...common });
    case "rect-fill":
      return new F.Rect({ left, top, width: w, height: h, stroke, strokeWidth, fill: fillColor });
    case "ellipse":
      return new F.Ellipse({ left, top, rx: w / 2, ry: h / 2, ...common });
    case "ellipse-fill":
      return new F.Ellipse({ left, top, rx: w / 2, ry: h / 2, stroke, strokeWidth, fill: fillColor });
    case "triangle":
      return new F.Triangle({ left, top, width: w, height: h, ...common });
    case "arrow":
      return buildArrow(x0, y0, x1, y1, stroke, strokeWidth);
    case "star":
      return buildStar((x0 + x1) / 2, (y0 + y1) / 2, Math.max(w, h) / 2, stroke, strokeWidth, fillColor);
    default:
      return null;
  }
}

function buildArrow(x0, y0, x1, y1, stroke, strokeWidth) {
  const F = window.fabric;
  const angle = Math.atan2(y1 - y0, x1 - x0);
  const head = Math.max(10, strokeWidth * 3);
  const hx = x1 - head * Math.cos(angle);
  const hy = y1 - head * Math.sin(angle);
  const left = head * Math.sin(angle);
  const top = -head * Math.cos(angle);
  const points = [
    { x: x0, y: y0 },
    { x: hx, y: hy },
    { x: hx + left * 0.6, y: hy + top * 0.6 },
    { x: x1, y: y1 },
    { x: hx - left * 0.6, y: hy - top * 0.6 },
    { x: hx, y: hy },
  ];
  return new F.Polyline(points, { stroke, strokeWidth, fill: "transparent", strokeLineJoin: "round" });
}

function buildStar(cx, cy, r, stroke, strokeWidth, fill) {
  const F = window.fabric;
  const pts = [];
  for (let i = 0; i < 10; i++) {
    const a = (Math.PI / 5) * i - Math.PI / 2;
    const rad = i % 2 === 0 ? r : r * 0.45;
    pts.push({ x: cx + rad * Math.cos(a), y: cy + rad * Math.sin(a) });
  }
  return new F.Polygon(pts, { stroke, strokeWidth, fill });
}

// ── Persistence ─────────────────────────────────────────────────────────────

function triggerSave() {
  if (STATE.hydrating) return;
  if (!STATE.saveUrl) return;
  if (STATE.saveTimer) clearTimeout(STATE.saveTimer);
  STATE.saveTimer = setTimeout(saveNow, 600);
}

function saveNow() {
  STATE.saveTimer = null;
  if (!STATE.saveUrl) return;
  const payload = serializeLayers();
  const csrf = getCsrf();
  fetch(STATE.saveUrl, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRFToken": csrf } : {}),
    },
    body: JSON.stringify(payload),
  }).catch((err) => console.warn("[annotations] save failed", err));
}

function getCsrf() {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

// ── Undo / Redo ─────────────────────────────────────────────────────────────

function pushHistory() {
  if (STATE.history.snapshotting) return;
  const snap = JSON.stringify(serializeLayers());
  const last = STATE.history.past[STATE.history.past.length - 1];
  if (last === snap) return;
  STATE.history.past.push(snap);
  if (STATE.history.past.length > HISTORY_CAP) STATE.history.past.shift();
  STATE.history.future.length = 0;
}

function undoStep() {
  if (STATE.history.past.length < 2) return;
  const current = STATE.history.past.pop();
  STATE.history.future.push(current);
  const prev = STATE.history.past[STATE.history.past.length - 1];
  applySnapshot(prev);
}

function redoStep() {
  if (!STATE.history.future.length) return;
  const next = STATE.history.future.pop();
  STATE.history.past.push(next);
  applySnapshot(next);
}

function applySnapshot(snapshot) {
  const canvas = STATE.canvas;
  if (!canvas || !snapshot) return;
  STATE.history.snapshotting = true;
  const data = JSON.parse(snapshot);
  const { layers, activeLayerId, objectsFlat } = parseLayers(data);
  STATE.layers = layers;
  STATE.activeLayerId = activeLayerId;
  canvas.clear();
  canvas.loadFromJSON({ objects: objectsFlat }, () => {
    applyLayerVisibility();
    canvas.renderAll();
    STATE.history.snapshotting = false;
    renderLayerList();
    triggerSave();
  });
}

function bindKeyboard() {
  document.addEventListener("keydown", (e) => {
    const bar = $("annotBar");
    if (!bar || bar.hidden) return;
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) redoStep(); else undoStep();
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      const canvas = STATE.canvas;
      const active = canvas && canvas.getActiveObjects && canvas.getActiveObjects();
      if (active && active.length) {
        e.preventDefault();
        active.forEach((o) => canvas.remove(o));
        canvas.discardActiveObject();
        canvas.requestRenderAll();
        pushHistory();
        triggerSave();
      }
    }
  });
}
