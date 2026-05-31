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

  // Drag to pan — only when zoomed in (zoom > 1). Falls through to native
  // event handling otherwise so existing pin click / drag still work at 1×.
  S.stage.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);

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
  // When zoomed in (zoom > 1) we allow the user to pan up to half the
  // overflow on each axis — enough to bring any corner to the centre but
  // not so far the image flies off entirely.
  if (S.zoom <= 1) {
    S.panX = 0;
    S.panY = 0;
    return;
  }
  const rect = S.stage.getBoundingClientRect();
  const overflowX = Math.max(0, (rect.width * S.zoom - rect.width) / 2);
  const overflowY = Math.max(0, (rect.height * S.zoom - rect.height) / 2);
  S.panX = Math.min(overflowX, Math.max(-overflowX, S.panX));
  S.panY = Math.min(overflowY, Math.max(-overflowY, S.panY));
}

function applyTransform() {
  if (!S.stage) return;
  S.stage.style.setProperty("--zoom", S.zoom.toFixed(4));
  S.stage.style.setProperty("--pan-x", S.panX.toFixed(2) + "px");
  S.stage.style.setProperty("--pan-y", S.panY.toFixed(2) + "px");
  S.viewer.classList.toggle("zoomed", S.zoom > 1.001);
  if (S.slider) S.slider.value = Math.round(S.zoom * 100);
  if (S.pctLabel) S.pctLabel.textContent = Math.round(S.zoom * 100) + " %";
}

function onPointerDown(e) {
  if (S.zoom <= 1.001) return; // pan disabled at fit
  // Don't pan when the user is interacting with a pin or the annotation
  // canvas — those need their own pointer events.
  if (e.target && (
      e.target.closest(".pin") ||
      e.target.tagName === "CANVAS" ||
      e.target.closest(".v-label") ||
      e.target.closest(".zoom-ctrl")
  )) return;
  S.dragging = true;
  S.dragStart = { mx: e.clientX, my: e.clientY, panX: S.panX, panY: S.panY };
  S.viewer.classList.add("panning");
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

function onPointerUp() {
  if (!S.dragging) return;
  S.dragging = false;
  S.viewer.classList.remove("panning");
}

function isOverPlan(e) {
  if (!S.viewer) return false;
  const r = S.viewer.getBoundingClientRect();
  return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
}
