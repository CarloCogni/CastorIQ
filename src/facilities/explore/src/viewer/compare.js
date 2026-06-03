// compare.js — synchronised 360° compare.
//
// One sphere, one camera, TWO equirectangular textures. A fragment shader picks
// the left texture for screen-x < divider and the right texture otherwise, so the
// two panoramas stay perfectly aligned while you drag to look around. Photo (flat)
// compare is handled with a plain clip-path slider in main.js — no WebGL needed.

import * as THREE from "three";

export function createPanoCompare(mount, srcLeft, srcRight) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(75, aspect(mount), 0.1, 1100);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  mount.appendChild(renderer.domElement);

  const geometry = new THREE.SphereGeometry(500, 60, 40);
  geometry.scale(-1, 1, 1);

  const loader = new THREE.TextureLoader();
  const texL = loader.load(srcLeft, render);
  const texR = loader.load(srcRight, render);
  texL.colorSpace = THREE.SRGBColorSpace;
  texR.colorSpace = THREE.SRGBColorSpace;

  const uniforms = {
    texL: { value: texL },
    texR: { value: texR },
    uDivider: { value: 0.5 },
    uRes: { value: new THREE.Vector2(1, 1) },
  };
  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `
      varying vec2 vUv;
      uniform sampler2D texL; uniform sampler2D texR;
      uniform float uDivider; uniform vec2 uRes;
      void main() {
        float sx = gl_FragCoord.x / uRes.x;
        gl_FragColor = sx < uDivider ? texture2D(texL, vUv) : texture2D(texR, vUv);
      }`,
  });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  let lon = 0, lat = 0, fov = 75;
  let down = false, sx = 0, sy = 0, dLon = 0, dLat = 0;

  function render() {
    lat = clamp(lat, -85, 85);
    const phi = THREE.MathUtils.degToRad(90 - lat);
    const theta = THREE.MathUtils.degToRad(lon);
    camera.lookAt(Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta));
    renderer.render(scene, camera);
  }
  function resize() {
    camera.aspect = aspect(mount);
    camera.updateProjectionMatrix();
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    uniforms.uRes.value.set(renderer.domElement.width, renderer.domElement.height);
    render();
  }

  const el = renderer.domElement;
  const onDown = (e) => { down = true; sx = e.clientX; sy = e.clientY; dLon = lon; dLat = lat; };
  const onMove = (e) => { if (!down) return; lon = dLon - (e.clientX - sx) * 0.13; lat = dLat + (e.clientY - sy) * 0.13; render(); };
  const onUp = () => { down = false; };
  const onWheel = (e) => { e.preventDefault(); fov = clamp(fov + e.deltaY * 0.05, 30, 100); camera.fov = fov; camera.updateProjectionMatrix(); render(); };
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointerleave", onUp);
  el.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("resize", resize);

  resize();

  return {
    setDivider(frac) { uniforms.uDivider.value = clamp(frac, 0, 1); render(); },
    dispose() {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointerleave", onUp);
      el.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", resize);
      geometry.dispose(); material.dispose(); texL.dispose(); texR.dispose(); renderer.dispose();
      if (el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

function aspect(el) { return (el.clientWidth || 1) / (el.clientHeight || 1); }
function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
