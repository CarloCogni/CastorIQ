// floorplan.js — renders points (pins) onto the plan stage and wires plan
// interactions (click-to-place, select, drag-to-reposition).
//
// Coordinate model: each point stores x and y as a PERCENT (0–100) of the plan
// image box. Pins are positioned with left/top in %, so they stay correct at any
// render size or aspect. The pin layer overlays the image box exactly, so the
// same rect maps screen pixels ↔ percent for both placing and dragging.

const DRAG_THRESHOLD = 3; // px before a press counts as a drag, not a click

export function renderPins(layer, points, { selectedId = null, onSelect, onMove, colorFor, numbers = null, pad = 1 } = {}) {
  layer.innerHTML = "";
  points.forEach((pt, i) => {
    const raw = numbers ? numbers[pt.id] : i + 1;
    if (raw == null) return; // not in the active phase filter → hidden entirely
    const label = String(raw).padStart(pad, "0");
    const pin = document.createElement("div");
    pin.className = "pin" + (pt.id === selectedId ? " sel" : "");
    pin.style.left = pt.x + "%";
    pin.style.top = pt.y + "%";
    pin.dataset.id = pt.id;
    pin.title = pt.label || pt.globalId || "Point"; // name only as a native hover tooltip
    pin.innerHTML = `<div class="pin-dot"><span class="pin-num">${label}</span></div>`;
    if (colorFor) {
      const color = colorFor(pt);
      const dot = pin.querySelector(".pin-dot");
      dot.style.background = hexToRgba(color, 0.45);
      dot.style.borderColor = color;
      pin.style.setProperty("--pin-color", color);
    }
    attachPinInteractions(pin, pt, layer, { onSelect, onMove });
    layer.appendChild(pin);
  });
}

function hexToRgba(hex, a) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return hex;
  return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})`;
}

function attachPinInteractions(pin, pt, layer, { onSelect, onMove }) {
  let dragging = false;
  let moved = false;
  let startX = 0;
  let startY = 0;
  let curX = pt.x;
  let curY = pt.y;

  pin.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    pin.setPointerCapture(e.pointerId);
  });

  pin.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    if (!moved && Math.hypot(e.clientX - startX, e.clientY - startY) > DRAG_THRESHOLD) {
      moved = true;
    }
    if (moved) {
      const { x, y } = pctFromEvent(e, layer);
      curX = x;
      curY = y;
      // Move the DOM element directly during the drag; commit to state on release
      // so we don't re-render (and destroy this element) mid-gesture.
      pin.style.left = x + "%";
      pin.style.top = y + "%";
    }
  });

  pin.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    dragging = false;
    if (moved && onMove) onMove(pt.id, curX, curY);
  });

  // Selection happens on click (fires after pointerup); stopPropagation here keeps
  // the viewer's click handler from also firing and deselecting.
  pin.addEventListener("click", (e) => {
    e.stopPropagation();
    if (moved) { moved = false; return; } // a drag just ended — don't treat as select
    if (onSelect) onSelect(pt.id);
  });
}

// Wire the viewer surface: click empty plan to place (in placing mode) or deselect.
export function initFloorplan({ viewer, pinLayer, state, actions }) {
  viewer.addEventListener("click", (e) => {
    const r = pinLayer.getBoundingClientRect();
    const rawX = ((e.clientX - r.left) / r.width) * 100;
    const rawY = ((e.clientY - r.top) / r.height) * 100;
    const inside = rawX >= 0 && rawX <= 100 && rawY >= 0 && rawY <= 100;
    if (state.placing && inside) {
      actions.addPoint(rawX, rawY);
    } else if (!state.placing) {
      actions.deselect();
    }
  });
}

function pctFromEvent(e, box) {
  const r = box.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * 100;
  const y = ((e.clientY - r.top) / r.height) * 100;
  return { x: clamp(x, 0, 100), y: clamp(y, 0, 100) };
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
