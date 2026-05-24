// pano360.js — equirectangular 360° viewer (Three.js).
//
// Wraps an equirectangular image on the inside of a sphere and looks around from
// the centre: drag to rotate (lon/lat), wheel to zoom (FOV). Render-on-demand —
// no idle animation loop, so it's cheap. createPano() returns { dispose }.
//
// Three.js is resolved via the import map (CDN) — loaded lazily on first 360° open.

import * as THREE from "three";

export function createPano(mount, src, onReady) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(75, aspect(mount), 0.1, 1100);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  mount.appendChild(renderer.domElement);

  // Sphere viewed from the inside (flip X so the texture isn't mirrored).
  const geometry = new THREE.SphereGeometry(500, 60, 40);
  geometry.scale(-1, 1, 1);

  const material = new THREE.MeshBasicMaterial({ color: 0x222533 });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  const texture = new THREE.TextureLoader().load(src, () => {
    texture.colorSpace = THREE.SRGBColorSpace;
    material.map = texture;
    material.color.set(0xffffff);
    material.needsUpdate = true;
    render();
    const img = texture.image || {};
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (onReady) onReady({ width: w, height: h, ratio: h ? w / h : 0 });
  });

  let lon = 0, lat = 0, fov = 75;
  let isDown = false, downX = 0, downY = 0, downLon = 0, downLat = 0;

  function render() {
    lat = clamp(lat, -85, 85);
    const phi = THREE.MathUtils.degToRad(90 - lat);
    const theta = THREE.MathUtils.degToRad(lon);
    camera.lookAt(
      Math.sin(phi) * Math.cos(theta),
      Math.cos(phi),
      Math.sin(phi) * Math.sin(theta),
    );
    renderer.render(scene, camera);
  }

  // ── Interaction ──
  const el = renderer.domElement;
  function onDown(e) { isDown = true; downX = e.clientX; downY = e.clientY; downLon = lon; downLat = lat; el.setPointerCapture?.(e.pointerId); }
  function onMove(e) {
    if (!isDown) return;
    lon = downLon - (e.clientX - downX) * 0.13;
    lat = downLat + (e.clientY - downY) * 0.13;
    render();
  }
  function onUp() { isDown = false; }
  function onWheel(e) {
    e.preventDefault();
    fov = clamp(fov + e.deltaY * 0.05, 30, 100);
    camera.fov = fov;
    camera.updateProjectionMatrix();
    render();
  }
  function onResize() {
    camera.aspect = aspect(mount);
    camera.updateProjectionMatrix();
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    render();
  }

  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointerleave", onUp);
  el.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("resize", onResize);

  render();

  return {
    render,
    reset() { lon = 0; lat = 0; fov = 75; camera.fov = 75; camera.updateProjectionMatrix(); render(); },
    dispose() {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointerleave", onUp);
      el.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", onResize);
      geometry.dispose();
      material.dispose();
      if (texture) texture.dispose();
      renderer.dispose();
      if (el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

function aspect(el) {
  return (el.clientWidth || 1) / (el.clientHeight || 1);
}
function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}
