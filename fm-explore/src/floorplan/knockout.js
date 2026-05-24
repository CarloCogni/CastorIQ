// knockout.js — make near-white pixels of a plan image transparent.
//
// Best-effort: works well for clean black-line-on-white drawings; plans with
// gray/colored fills will lose those too. Returns a PNG data URL with alpha.
// Same-origin / blob / dataURL sources only (canvas would otherwise be tainted).

export function knockoutWhite(src, threshold = 235) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const w = img.naturalWidth || 800;
      const h = img.naturalHeight || 500;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, w, h);
      try {
        const data = ctx.getImageData(0, 0, w, h);
        const a = data.data;
        for (let i = 0; i < a.length; i += 4) {
          if (a[i] >= threshold && a[i + 1] >= threshold && a[i + 2] >= threshold) {
            a[i + 3] = 0; // transparent
          }
        }
        ctx.putImageData(data, 0, 0);
        resolve(canvas.toDataURL("image/png"));
      } catch (e) {
        reject(e); // tainted canvas (cross-origin) etc.
      }
    };
    img.onerror = () => reject(new Error("Could not load plan image for knockout"));
    img.src = src;
  });
}
