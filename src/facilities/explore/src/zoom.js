// zoom.js — pan + zoom for the floor-plan stage.
//
// The plan stage carries a CSS-variable-driven transform:
//   transform: translate(var(--pan-x), var(--pan-y)) scale(var(--zoom));
// This module owns the JS side: zoom buttons + slider + mouse wheel + drag-to-pan.
// The image, annotation canvas and pin layer all live inside .plan-stage, so
// they scale + translate as a single unit — pins stay anchored to the same
// floor coordinates and annotations track the image pixel-for-pixel.
//
// Boundaries: when zoomed in, panning is clamped so the user can't pan the
// image entirely out of the viewer. At zoom 1 panning is disabled (the image
// is already centred via the viewer's flex layout).

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 4.0;
const ZOOM_STEP = 1.15;

const S = {
  zoom: 1,
  panX: 0,
  panY: 0,
  dragging: false,
  dragStart: null,   // { mx, my, panX, panY }
  stage: null,
  viewer: null,
  slider: null,
  pctLabel: null,
};

export function initZoom() {
  S.stage = document.getElementById("planStage");
  S.viewer = document.querySelector(".exp-viewer");
  S.slider = document.getElementById("zoomSlider");
  S.pctLabel = document.getElementById("zoomPct");
  if (!S.stage || !S.viewer) return;

  document.getElementById("zoomIn")?.addEventListener("click", () => setZoom(S.zoom * ZOOM_STEP));
  document.getElementById("zoomOut")?.addEventListener("click", () => setZoom(S.zoom / ZOOM_STEP));
  document.getElementById("zoomFit")?.addEventListener("click", reset);
  if (S.slider) {
    S.slider.addEventListener("input", () => setZoom(parseFloat(S.slider.value) / 100));
  }

  // Mouse wheel zoom — anchored on the cursor so zooming in feels like the
  // image dives toward the pointer. Block native scroll so the parent page
  // doesn't pan beneath us in the embedded case.
  S.viewer.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey && !e.shiftKey && !isOverPlan(e)) {
      // Plain wheel outside the plan: let the page scroll normally.
      return;
    }
    e.preventDefault();
    const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    zoomAt(e.clientX, e.clientY, S.zoom * factor);
  }, { passive: false });

  // Drag to pan — pointerdown on the viewer (outermost element) so any
  // pointer hit inside the plan area triggers it regardless of which child
  // (img, canvas, pin-layer) was clicked. pointermove / pointerup live on
  // the *document* so a fast drag that briefly leaves the viewer doesn't
  // drop the gesture — and setPointerCapture pins all subsequent events
  // for that pointer to the viewer for good measure.
  S.viewer.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);

  applyTransform();
}

export function getZoom() { return S.zoom; }

export function reset() {
  S.zoom = 1;
  S.panX = 0;
  S.panY = 0;
  applyTransform();
}

function setZoom(z) {
  const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  if (Math.abs(next - S.zoom) < 1e-4) return;
  S.zoom = next;
  clampPan();
  applyTransform();
}

function zoomAt(clientX, clientY, nextZoom) {
  const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
  if (Math.abs(next - S.zoom) < 1e-4) return;
  // Anchor the zoom on the cursor: keep the point under the mouse in place
  // after the scale change. The math expresses "the displacement of the
  // cursor from the stage's centre should grow / shrink with zoom".
  const rect = S.stage.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const dx = clientX - cx;
  const dy = clientY - cy;
  const ratio = next / S.zoom;
  S.panX = S.panX - dx * (ratio - 1) / next;
  S.panY = S.panY - dy * (ratio - 1) / next;
  S.zoom = next;
  clampPan();
  applyTransform();
}

function clampPan() {
  // At fit-to-view there's nothing to pan; force back to centre.
  if (S.zoom <= 1.001) {
    S.panX = 0;
    S.panY = 0;
    return;
  }
  // Real overflow per side = (stage_rendered_size − viewer_size) / 2.
  // ``getBoundingClientRect`` already accounts for the CSS scale, so
  // stageRect.width is the visual width after the transform — no extra
  // multiplication by S.zoom needed (that was the earlier double-zoom bug).
  const stageRect = S.stage.getBoundingClientRect();
  const viewerRect = S.viewer.getBoundingClientRect();
  const overflowX = Math.max(0, (stageRect.width - viewerRect.width) / 2);
  const overflowY = Math.max(0, (stageRect.height - viewerRect.height) / 2);
  S.panX = Math.min(overflowX, Math.max(-overflowX, S.panX));
  S.panY = Math.min(overflowY, Math.max(-overflowY, S.panY));
}

function applyTransform() {
  if (!S.stage) return;
  S.stage.style.setProperty("--zoom", S.zoom.toFixed(4));
  S.stage.style.setProperty("--pan-x", S.panX.toFixed(2) + "px");
  S.stage.style.setProperty("--pan-y", S.panY.toFixed(2) + "px");
  S.viewer.classList.toggle("zoomed", S.zoom > 1.001);
  // ``has-overflow`` mirrors the actual situation rather than the zoom level:
  // even at 1× the plan can be larger than the viewer (rare but possible if
  // the IMG's intrinsic constraints don't quite resolve to a fit). When that
  // happens the cursor turns to grab and the pan handler accepts drags too.
  S.viewer.classList.toggle("has-overflow", hasOverflow());
  if (S.slider) S.slider.value = Math.round(S.zoom * 100);
  if (S.pctLabel) S.pctLabel.textContent = Math.round(S.zoom * 100) + " %";
}

function hasOverflow() {
  if (!S.stage || !S.viewer) return false;
  const s = S.stage.getBoundingClientRect();
  const v = S.viewer.getBoundingClientRect();
  return s.width > v.width + 1 || s.height > v.height + 1;
}

function onPointerDown(e) {
  // Pan is allowed whenever the stage extends beyond the viewer — at any
  // zoom level. That covers both "user zoomed in" and "image happens to be
  // larger than the viewer" (e.g. a tall plan that doesn't quite fit).
  if (S.zoom <= 1.001 && !hasOverflow()) return;
  // Don't hijack pointer events from UI elements that need them. The pin
  // layer carries pin elements with .pin class; the annotation canvas needs
  // events in Draw mode; the zoom controls speak for themselves.
  if (e.target && (
      e.target.closest(".pin") ||
      e.target.closest(".v-label") ||
      e.target.closest(".zoom-ctrl") ||
      e.target.closest(".annot-bar")
  )) return;
  if (S.viewer.classList.contains("draw-mode")) return;
  S.dragging = true;
  S.dragStart = { mx: e.clientX, my: e.clientY, panX: S.panX, panY: S.panY };
  S.viewer.classList.add("panning");
  try { S.viewer.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
  e.preventDefault();
}

function onPointerMove(e) {
  if (!S.dragging) return;
  const { mx, my, panX, panY } = S.dragStart;
  S.panX = panX + (e.clientX - mx);
  S.panY = panY + (e.clientY - my);
  clampPan();
  applyTransform();
}

function onPointerUp(e) {
  if (!S.dragging) return;
  S.dragging = false;
  S.viewer.classList.remove("panning");
  if (e && e.pointerId !== undefined) {
    try { S.viewer.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ }
  }
}

function isOverPlan(e) {
  if (!S.viewer) return false;
  const r = S.viewer.getBoundingClientRect();
  return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
}
