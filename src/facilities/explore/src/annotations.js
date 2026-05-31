// annotations.js — Paint-style drawing overlay over the floor-plan image.
//
// Architecture:
//   * The DOM has three layers stacked inside .plan-stage:
//     1. <img.plan-img>   — the floor plan (background)
//     2. <canvas.annot-canvas>  — Fabric.js drawing surface (this module)
//     3. <div.pin-layer>  — user pins (clickable in View mode)
//   * .exp-viewer carries `.draw-mode` while the drawing toolbar is open.
//     In that mode the annot-canvas captures pointer events; otherwise
//     events fall through to the pin layer (so pins stay clickable).
//   * Annotations are SERIALISED with `canvas.toJSON()` and stored per
//     storey in the floor descriptor (Castor side: ExploreFloorPlan
//     .annotations_json). Persistence is a debounced fetch POST.
//
// One Fabric canvas is reused across floor switches; we wipe + reload it
// when the active floor changes. Drawings scale with the plan image via a
// ResizeObserver that resizes the canvas to match the IMG's box.

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
  // Save URL is per-storey; host supplies it via `attachStorey(id, saveUrl)`.
  saveUrl: null,
  // The currently-selected tool and visual style.
  tool: "select",   // 'select' | 'pencil' | 'eraser' | 'text' | 'shape:line' | 'shape:rect' | ...
  brush: 4,
  color: "#1f6feb",
  // Shape-drawing state (mousedown anchor + ghost object) — only used while
  // the user is dragging out a new shape with one of the Tvary tools.
  drag: null,
  // Undo / redo stacks store JSON snapshots; we cap them so a long session
  // doesn't grow into megabytes.
  history: { past: [], future: [], snapshotting: false },
  // Suppress save while we're hydrating (loadFromJSON fires object:added).
  hydrating: false,
  // Debounced save handle.
  saveTimer: null,
};

const HISTORY_CAP = 60;

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

  // Fabric canvas. We don't enable selection by default — the Select tool
  // turns it on; other tools disable it so a click-drag is interpreted as
  // "draw a new shape" rather than "move a thing".
  const canvas = new window.fabric.Canvas(canvasEl, {
    backgroundColor: "transparent",
    selection: true,
    preserveObjectStacking: true,
  });
  STATE.canvas = canvas;

  syncCanvasToImage(planImg, canvas);
  // ResizeObserver re-syncs whenever the image's rendered box changes
  // (timeline drag, window resize, image swap). Same trick we use for pins.
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => syncCanvasToImage(planImg, canvas));
    ro.observe(planImg);
  }
  planImg.addEventListener("load", () => {
    syncCanvasToImage(planImg, canvas);
  });

  // Wire all toolbar buttons.
  paintSwatches();
  bindToolbar();
  bindCanvas(canvas, viewer);
  bindKeyboard();
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
  // Selecting a tool re-applies the right cursor / canvas-selection flag.
  applyTool(STATE.tool);
}

/** Hydrate from a floor descriptor's annotations payload. Safe to call
 *  any time; clears the canvas first and skips save during load. */
export function loadAnnotations(annotations) {
  const canvas = STATE.canvas;
  if (!canvas) return;
  STATE.hydrating = true;
  canvas.clear();
  const payload = annotations && typeof annotations === "object" ? annotations : null;
  if (payload && payload.objects && payload.objects.length) {
    canvas.loadFromJSON(payload, () => {
      canvas.renderAll();
      STATE.hydrating = false;
      // Reset history — the loaded state is the new "blank slate".
      STATE.history.past = [];
      STATE.history.future = [];
      pushHistory();
    });
  } else {
    STATE.hydrating = false;
    STATE.history.past = [];
    STATE.history.future = [];
    pushHistory();
  }
}

/** Tell the module which storey is active + which URL to POST saves to. */
export function attachStorey(storeyId, saveUrl) {
  STATE.storeyId = storeyId;
  STATE.saveUrl = saveUrl || null;
}

// ── Internals ───────────────────────────────────────────────────────────────

function syncCanvasToImage(planImg, canvas) {
  const w = planImg.offsetWidth || planImg.clientWidth || 1;
  const h = planImg.offsetHeight || planImg.clientHeight || 1;
  if (canvas.getWidth() === w && canvas.getHeight() === h) return;
  canvas.setWidth(w);
  canvas.setHeight(h);
  // Re-render so existing objects stay drawn at the new pixel size. We
  // intentionally do NOT rescale the objects — they're authored in canvas
  // pixel space; if the user resizes drastically they can wipe + redraw.
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
  // Pencil colour reflects the new choice straight away.
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
    if (!STATE.canvas) return;
    if (!confirm("Clear all annotations on this floor?")) return;
    STATE.canvas.clear();
    pushHistory();
    triggerSave();
  });
}

function applyTool(tool) {
  STATE.tool = tool;
  const canvas = STATE.canvas;
  if (!canvas) return;
  // Default: nothing selectable, no free drawing.
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
  // Eraser: click an object → remove it.
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
      canvas.add(STATE.drag.ghost);
      canvas.requestRenderAll();
    }
  });

  canvas.on("mouse:up", () => {
    if (STATE.drag && STATE.drag.ghost) {
      STATE.drag.ghost.selectable = false;
      STATE.drag.ghost.evented = false;
      STATE.drag = null;
      pushHistory();
      triggerSave();
    } else {
      STATE.drag = null;
    }
  });

  canvas.on("path:created", () => {
    // Pencil committed a new path.
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
  const canvas = STATE.canvas;
  if (!canvas || !STATE.saveUrl) return;
  const payload = canvas.toJSON();
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
  // Look up Django's CSRF cookie. The iframe shares the parent's cookie
  // jar because they're same-origin, so this works under embedded mode.
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

// ── Undo / Redo ─────────────────────────────────────────────────────────────

function pushHistory() {
  if (STATE.history.snapshotting) return;
  const canvas = STATE.canvas;
  if (!canvas) return;
  const snap = JSON.stringify(canvas.toJSON());
  const last = STATE.history.past[STATE.history.past.length - 1];
  if (last === snap) return;
  STATE.history.past.push(snap);
  if (STATE.history.past.length > HISTORY_CAP) STATE.history.past.shift();
  STATE.history.future.length = 0;
}

function undoStep() {
  if (STATE.history.past.length < 2) return;
  const canvas = STATE.canvas;
  if (!canvas) return;
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
  canvas.loadFromJSON(JSON.parse(snapshot), () => {
    canvas.renderAll();
    STATE.history.snapshotting = false;
    triggerSave();
  });
}

function bindKeyboard() {
  document.addEventListener("keydown", (e) => {
    const bar = $("annotBar");
    if (!bar || bar.hidden) return;
    // Ignore when the user is typing into a text annotation.
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
