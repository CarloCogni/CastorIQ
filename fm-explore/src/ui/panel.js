// panel.js — renders the right-hand detail panel for the selected point.
//
// Receives element refs (title/sub/body/actions) and the selected point (or null).
// Field edits commit on 'change' (blur/Enter), so the panel can be safely rebuilt
// on the next render without stealing input focus mid-typing.

export function renderPanel(els, point, handlers = {}, ui = {}) {
  const { title, sub, body, actions } = els;
  const attachType = ui.attachType || "photo";
  const attachPhase = ui.attachPhase || "";
  const phases = ui.phases || ["Construction", "Fit-out", "Occupied"];
  const rooms = ui.rooms || [];
  const catalog = ui.catalog || {};
  const filterKeys = ui.filterKeys || {};
  const roomName = ui.roomName || "";
  const getRows = ui.getRows || (() => ({ columns: [], rows: [] }));
  const room = ui.room || null;
  const idProps = ui.idProps || [];
  const propLabel = ui.propLabel || ((k) => k);

  if (!point) {
    title.textContent = "No point selected";
    sub.textContent = "Click + Point, then click the plan";
    body.innerHTML =
      `<div class="dp-empty">` +
      `<div class="dp-empty-icon">◎</div>` +
      `<div class="dp-empty-text">Press <b>+ Point</b>, then click anywhere on the floor plan to drop a point. Existing points are clickable and draggable.</div>` +
      `</div>`;
    actions.hidden = true;
    actions.innerHTML = "";
    return;
  }

  title.textContent = point.label || "Point";
  sub.textContent = `x ${point.x}%  ·  y ${point.y}%`;

  body.innerHTML =
    identificationBlock(point, rooms, phases, room, idProps, propLabel) +
    archiveSection(point, "photo", "Photos") +
    archiveSection(point, "360", "360°") +
    linkedDataBlock(point, catalog, filterKeys, getRows, roomName);

  body.querySelectorAll(".fld-in[data-f]").forEach((inp) => {
    inp.addEventListener("change", () => {
      if (handlers.onField) handlers.onField(point.id, inp.dataset.f, inp.value);
    });
  });
  const roomSel = body.querySelector("[data-room]");
  if (roomSel) roomSel.addEventListener("change", () => handlers.onSelectRoom && handlers.onSelectRoom(point.id, roomSel.value));
  const cfgBtn = body.querySelector('[data-cfg="idprops"]');
  if (cfgBtn) cfgBtn.addEventListener("click", () => handlers.onConfigIdProps && handlers.onConfigIdProps());
  const pointPhaseSel = body.querySelector("[data-pointphase]");
  if (pointPhaseSel) pointPhaseSel.addEventListener("change", () => {
    if (pointPhaseSel.value === "__add__") {
      pointPhaseSel.value = point.phase || "";
      handlers.onAddPointPhase && handlers.onAddPointPhase(point.id);
    } else {
      handlers.onSetPointPhase && handlers.onSetPointPhase(point.id, pointPhaseSel.value);
    }
  });

  // Hero of each archive: click to view, ✕ to remove
  body.querySelectorAll(".dp-hero").forEach((hero) => {
    const mid = hero.dataset.mid;
    const del = hero.querySelector(".thumb-del");
    if (del) del.addEventListener("click", (e) => { e.stopPropagation(); handlers.onRemoveMedia && handlers.onRemoveMedia(point.id, mid); });
    hero.addEventListener("click", () => handlers.onViewMedia && handlers.onViewMedia(point.id, mid));
  });
  // Per-archive Upload / Camera (type fixed by the section)
  body.querySelectorAll('[data-act="upload"]').forEach((b) =>
    b.addEventListener("click", () => handlers.onUpload && handlers.onUpload(point.id, b.dataset.type)));
  body.querySelectorAll('[data-act="camera"]').forEach((b) =>
    b.addEventListener("click", () => handlers.onCamera && handlers.onCamera(point.id, b.dataset.type)));

  // Linked data: add-table picker + remove buttons + per-table filter key
  const addTbl = body.querySelector("[data-addtable]");
  if (addTbl) addTbl.addEventListener("change", () => {
    if (addTbl.value) { handlers.onAddTable && handlers.onAddTable(point.id, addTbl.value); }
  });
  body.querySelectorAll("[data-rmtable]").forEach((b) => {
    b.addEventListener("click", () => handlers.onRemoveTable && handlers.onRemoveTable(point.id, b.dataset.rmtable));
  });
  body.querySelectorAll("[data-tblfilter]").forEach((sel) => {
    sel.addEventListener("change", () => handlers.onSetTableFilter && handlers.onSetTableFilter(point.id, sel.dataset.tblfilter, sel.value));
  });

  actions.hidden = false;
  actions.innerHTML =
    `<button class="btn btn-r btn-sm" data-act="delete">Delete point</button>` +
    `<button class="btn btn-sm" data-act="focus3d" ${point.globalId ? "" : "disabled title=\"Set a GlobalID first\""}>Focus in 3D</button>`;
  const del = actions.querySelector('[data-act="delete"]');
  if (del) del.addEventListener("click", () => handlers.onDelete && handlers.onDelete(point.id));
  const f3d = actions.querySelector('[data-act="focus3d"]');
  if (f3d) f3d.addEventListener("click", () => handlers.onFocus3D && handlers.onFocus3D(point.id));
}

// One archive (Photos or 360°): hero = newest of that type + its own upload/camera.
function archiveSection(point, type, label) {
  const items = point.media.filter((m) => m.type === type);
  const hero = items.length ? newestMedia(items) : null;
  const heroHtml = hero
    ? `<div class="dp-hero" data-mid="${hero.id}" title="Open / edit">` +
        `<img src="${escapeAttr(hero.src)}" alt="" />` +
        (type === "360" ? `<span class="thumb-360">360°</span>` : "") +
        `<button class="thumb-del" data-mid="${hero.id}" title="Remove">✕</button>` +
        `<div class="dp-hero-cap">${escapeAttr(hero.label || fmtDate(hero.date))}${hero.phase ? " · " + escapeAttr(hero.phase) : ""}</div>` +
        `</div>` +
        `<div class="dp-hero-note">Latest · ${items.length} in timeline ↓</div>`
    : `<div class="dp-photos-empty">No ${type === "360" ? "360°" : "photos"} yet — add below</div>`;
  return (
    `<div class="dp-sec">` +
    `<div class="dp-sec-lbl">${escapeAttr(label)} · ${items.length}</div>` +
    heroHtml +
    `<div class="attach-row">` +
    `<button class="btn btn-sm" data-act="upload" data-type="${type}">⤓ Upload</button>` +
    `<button class="btn btn-sm" data-act="camera" data-type="${type}">◉ Camera</button>` +
    (type === "360" ? `<span class="attach-hint">equirectangular 2:1</span>` : "") +
    `</div>` +
    `</div>`
  );
}

function newestMedia(media) {
  return [...media].sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? 1 : -1;
    return (b.addedAt || 0) - (a.addedAt || 0);
  })[0];
}

function fmtDate(d) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
  return m ? `${m[1]}-${m[2]}-${m[3]}` : (d || "—");
}

// Linked Facility/Schedule tables (read-only) for the point's room.
// point.tables = [{ key, filterBy }]; rows come from getRows(key, filterBy).
function linkedDataBlock(point, catalog, filterKeys, getRows, roomName) {
  const tables = point.tables || [];
  const tablesHtml = tables.map(({ key, filterBy }) => {
    const def = catalog[key];
    if (!def) return "";
    const { columns, rows } = getRows(key, filterBy);
    const head = `<tr>${columns.map((c) => `<th>${escapeAttr(c.label)}</th>`).join("")}</tr>`;
    const bodyRows = rows.length
      ? rows.map((r) =>
          `<tr>` + columns.map((c, i) =>
            (i === 0
              ? `<td>${r._status ? `<span class="st-dot st-${r._status}"></span>` : ""}${escapeAttr(r[c.field])}</td>`
              : `<td>${escapeAttr(r[c.field])}</td>`)).join("") + `</tr>`).join("")
      : `<tr><td colspan="${columns.length}"><span class="dp-empty-sm">No matching records</span></td></tr>`;
    const filterSel =
      `<select class="dp-tblfilter" data-tblfilter="${key}" title="Match rooms by">` +
      Object.keys(filterKeys).map((fk) => `<option value="${fk}" ${fk === filterBy ? "selected" : ""}>${escapeAttr(filterKeys[fk].label)}</option>`).join("") +
      `</select>`;
    return (
      `<div class="dp-tblwrap">` +
      `<div class="dp-tblhead"><span class="dp-tbllabel">${escapeAttr(def.label)}</span>${filterSel}<button class="dp-tblx" data-rmtable="${key}" title="Remove">✕</button></div>` +
      `<table class="dp-tbl"><thead>${head}</thead><tbody>${bodyRows}</tbody></table>` +
      `</div>`
    );
  }).join("");

  const used = new Set(tables.map((t) => t.key));
  const groups = {};
  Object.keys(catalog).forEach((k) => {
    if (used.has(k)) return;
    const g = catalog[k].group || "Other";
    (groups[g] = groups[g] || []).push(k);
  });
  const optgroups = Object.keys(groups)
    .map((g) => `<optgroup label="${escapeAttr(g)}">` + groups[g].map((k) => `<option value="${k}">${escapeAttr(catalog[k].label)}</option>`).join("") + `</optgroup>`)
    .join("");

  return (
    `<div class="dp-sec">` +
    `<div class="dp-sec-lbl">Linked data ${roomName ? `<span class="dp-roomtag">${escapeAttr(roomName)}</span>` : ""}</div>` +
    tablesHtml +
    (optgroups ? `<select class="phase-sel dp-addtbl" data-addtable><option value="">＋ Add table…</option>${optgroups}</select>` : `<div class="dp-empty-sm">All catalog tables added</div>`) +
    `</div>`
  );
}

function identificationBlock(point, rooms, phases, room, idProps, propLabel) {
  const linked = !!point.roomId;
  const roomOpts =
    `<option value="">— Custom (manual)</option>` +
    rooms.map((r) => `<option value="${escapeAttr(r.globalId)}" ${r.globalId === point.roomId ? "selected" : ""}>${escapeAttr(r.name)}</option>`).join("");
  const ro = linked ? "readonly" : "";
  const cur = point.phase || "";
  const phaseList = cur && !phases.includes(cur) ? [...phases, cur] : phases;
  const phaseOpts =
    `<option value="" ${cur === "" ? "selected" : ""}>— none</option>` +
    phaseList.map((p) => `<option value="${escapeAttr(p)}" ${p === cur ? "selected" : ""}>${escapeAttr(p)}</option>`).join("") +
    `<option value="__add__">➕ Add phase…</option>`;
  // identification fields from the linked IFC room's properties (configured via ⚙)
  const props = (room && room.props) || {};
  const idFields = idProps
    .filter((k) => props[k] !== undefined && props[k] !== "")
    .map((k) => `<div class="info-row"><span class="info-k">${escapeAttr(propLabel(k))}</span><span class="info-v">${escapeAttr(props[k])}</span></div>`)
    .join("");
  return (
    `<div class="dp-sec">` +
    `<div class="dp-sec-lbl">Identification <button class="dp-cfg" data-cfg="idprops" title="Configure identification fields">⚙</button></div>` +
    `<label class="fld"><span>Room (from IFC)</span><select class="fld-in" data-room>${roomOpts}</select></label>` +
    field("Label", "label", point.label, "Name this point") +
    `<label class="fld"><span>Phase (room state)</span><select class="fld-in" data-pointphase>${phaseOpts}</select></label>` +
    `<label class="fld"><span>GlobalID</span><input class="fld-in" data-f="globalId" value="${escapeAttr(point.globalId)}" ${ro} placeholder="pick a room above" /></label>` +
    `<label class="fld"><span>IFC type</span><input class="fld-in" data-f="ifcType" value="${escapeAttr(point.ifcType)}" ${ro} placeholder="IfcSpace…" /></label>` +
    (idFields ? `<div class="info-rows">${idFields}</div>` : "") +
    `</div>`
  );
}

function field(label, key, value, placeholder) {
  return (
    `<label class="fld"><span>${label}</span>` +
    `<input class="fld-in" data-f="${key}" value="${escapeAttr(value || "")}" placeholder="${escapeAttr(placeholder || "")}" />` +
    `</label>`
  );
}

function escapeAttr(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
