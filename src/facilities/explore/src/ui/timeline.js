// timeline.js — chronological strip of the selected point's photos for ONE archive
// (Photos or 360°). Two layouts: 'thumbs' (compact) and 'details' (cards with the
// photo's metadata). Click an item to open the media editor.

export function renderTimeline(track, point, { type = "photo", view = "thumbs", sort = { key: "date", dir: "asc" }, selectedMediaId = null, onSelect } = {}) {
  if (!point) {
    track.innerHTML = `<span class="etl-empty">Select a point to see its photos</span>`;
    return;
  }
  const items = point.media.filter((m) => m.type === type);
  if (!items.length) {
    track.innerHTML = `<span class="etl-empty">No ${type === "360" ? "360°" : "photos"} yet for this point</span>`;
    return;
  }

  const keyOf = (m) =>
    sort.key === "name" ? (m.label || "").toLowerCase() :
    sort.key === "description" ? (m.description || "").toLowerCase() :
    sort.key === "time" ? (m.time || "") + (m.date || "") :
    (m.date || "") + (m.time || ""); // date (default)
  const sorted = [...items].sort((a, b) => {
    const ka = keyOf(a), kb = keyOf(b);
    const c = ka > kb ? 1 : ka < kb ? -1 : 0;
    return sort.dir === "desc" ? -c : c;
  });

  track.innerHTML = view === "details"
    ? sorted.map((m) => detailsCard(m, m.id === selectedMediaId)).join("")
    : sorted.map((m, i) => (i > 0 ? `<span class="etl-conn"></span>` : "") + thumbSnap(m, m.id === selectedMediaId)).join("");

  track.querySelectorAll("[data-mid]").forEach((el) => {
    el.addEventListener("click", () => onSelect && onSelect(point.id, el.dataset.mid));
  });
}

function thumbSnap(m, sel) {
  return (
    `<div class="tl-snap${sel ? " on" : ""}" data-mid="${m.id}" title="${escapeAttr(m.label || "")} — click to edit">` +
    `<div class="tl-th">${m.type === "360" ? `<span class="tl-360">360°</span>` : ""}<img src="${escapeAttr(m.src)}" alt="" /></div>` +
    `<div class="tl-date">${fmtDate(m.date)}${m.time ? " " + escapeAttr(m.time) : ""}</div>` +
    `</div>`
  );
}

function detailsCard(m, sel) {
  const meta = [m.code, fmtDate(m.date) + (m.time ? " " + m.time : "")].filter(Boolean).join("  ·  ");
  return (
    `<div class="tl-card${sel ? " on" : ""}" data-mid="${m.id}" title="Click to edit">` +
    `<div class="tl-card-th">${m.type === "360" ? `<span class="tl-360">360°</span>` : ""}<img src="${escapeAttr(m.src)}" alt="" /></div>` +
    `<div class="tl-card-info">` +
      `<div class="tl-card-name">${escapeAttr(m.label || "(no name)")}</div>` +
      `<div class="tl-card-meta">${escapeAttr(meta)}</div>` +
      (m.description ? `<div class="tl-card-desc">${escapeAttr(m.description)}</div>` : "") +
    `</div>` +
    `</div>`
  );
}

function fmtDate(d) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
  return m ? `${m[1]}-${m[2]}-${m[3]}` : (d || "—");
}
function escapeAttr(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
