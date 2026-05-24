// import.js — turn user-picked files into floor descriptors.
//
//   • image (PNG/JPG/WebP) → one floor (object URL)
//   • PDF                   → one floor PER PAGE (rendered to PNG via PDF.js)
//
// PDF.js is loaded from a CDN via the import map in index.html (no build step).
// Returns an array of { name, label, plan, planType:'image' } ready for addFloors().

import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.7.76/build/pdf.worker.min.mjs";

const PDF_RENDER_SCALE = 2; // crisp enough to place points on

export async function importFiles(fileList) {
  const floors = [];
  for (const file of Array.from(fileList)) {
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    if (isPdf) {
      const pages = await importPdf(file);
      floors.push(...pages);
    } else if (file.type.startsWith("image/")) {
      floors.push({
        name: shortName(file.name),
        label: baseName(file.name),
        // data URL (not object URL) so the imported plan survives a page reload / localStorage persistence
        plan: await fileToDataURL(file),
        planType: "image",
      });
    }
    // other types are ignored
  }
  return floors;
}

async function importPdf(file) {
  const data = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data }).promise;
  const out = [];
  const base = baseName(file.name);
  for (let i = 1; i <= pdf.numPages; i += 1) {
    const page = await pdf.getPage(i);
    const viewport = page.getViewport({ scale: PDF_RENDER_SCALE });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport }).promise;
    out.push({
      name: pdf.numPages > 1 ? "p" + i : shortName(file.name),
      label: pdf.numPages > 1 ? `${base} · page ${i}` : base,
      plan: canvas.toDataURL("image/png"),
      planType: "image",
    });
  }
  return out;
}

function fileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error || new Error("read failed"));
    r.readAsDataURL(file);
  });
}

function baseName(name) {
  return name.replace(/\.[^.]+$/, "");
}
function shortName(name) {
  const b = baseName(name);
  return b.length > 5 ? b.slice(0, 5) : b;
}
