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
  const customSymbols = ui.customSymbols || [];

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
    identificationBlock(point, rooms, phases, room, idProps, propLabel, customSymbols) +
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

  // Custom point: rename its type (commits on blur) + change its symbol.
  const customNameInp = body.querySelector("[data-customname]");
  if (customNameInp) customNameInp.addEventListener("change", () => handlers.onSetPointKindLabel && handlers.onSetPointKindLabel(point.id, customNameInp.value));
  body.querySelectorAll("[data-customsym]").forEach((b) =>
    b.addEventListener("click", () => handlers.onSetPointKind && handlers.onSetPointKind(point.id, "custom", b.dataset.customsym)));

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

  // Linked data: add-table picker + element picker + remove buttons
  const addTbl = body.querySelector("[data-addtable]");
  if (addTbl) addTbl.addEventListener("change", () => {
    if (addTbl.value) { handlers.onAddTable && handlers.onAddTable(point.id, addTbl.value); }
  });
  const addEl = body.querySelector("[data-addelement]");
  if (addEl) addEl.addEventListener("change", () => {
    if (addEl.value) {
      const opt = addEl.options[addEl.selectedIndex];
      const label = (opt && opt.textContent ? opt.textContent.split(" — ")[0] : "").trim();
      handlers.onAddElementTable && handlers.onAddElementTable(point.id, addEl.value, label);
    }
  });
  body.querySelectorAll("[data-rmtable]").forEach((b) => {
    b.addEventListener("click", () => handlers.onRemoveTable && handlers.onRemoveTable(point.id, b.dataset.rmtable));
  });
  // Element-table property checkboxes (inside the ⚙ <details>). Collect all
  // checked keys for that table and push them into state.
  body.querySelectorAll("[data-elprop]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const key = cb.dataset.tblkey;
      const checked = Array.from(
        body.querySelectorAll(`[data-elprop][data-tblkey="${key}"]:checked`)
      ).map((c) => c.value);
      handlers.onSetElementTableProps && handlers.onSetElementTableProps(point.id, key, checked);
    });
  });
  // Per-table text filter: hide rows that don't contain the term (matches any
  // cell incl. the dynamic "Parameters" column). Pure DOM — no re-render.
  body.querySelectorAll("[data-tblsearch]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const term = inp.value.trim().toLowerCase();
      const tbody = body.querySelector(`tbody[data-tblbody="${inp.dataset.tblsearch}"]`);
      if (!tbody) return;
      tbody.querySelectorAll("tr").forEach((tr) => {
        tr.style.display = (!term || tr.textContent.toLowerCase().includes(term)) ? "" : "none";
      });
    });
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
// point.tables = [{ key, filterBy }] for catalog tables, or
// { key: "element:<id>", elementId, elementName, props } for per-element
// property tables. Rows for catalog tables come from getRows(key, filterBy);
// element tables read the element's structured props off catalog.elements.
function linkedDataBlock(point, catalog, filterKeys, getRows, roomName) {
  const tables = point.tables || [];
  const elementRows = (catalog.elements && catalog.elements.rows) || [];

  const tablesHtml = tables.map((entry) => {
    const { key, filterBy } = entry;

    // ── Per-element property table ──
    if (entry.elementId) {
      const el = elementRows.find((r) => r.id === entry.elementId);
      const allProps = (el && el.props && typeof el.props === "object") ? el.props : {};
      const allKeys = Object.keys(allProps);
      const chosen = (entry.props && entry.props.length) ? entry.props.filter((k) => k in allProps) : allKeys;
      const title = entry.elementName || (el ? el.name : "(element)");
      const bodyRows = chosen.length
        ? chosen.map((k) => `<tr><td>${escapeAttr(k)}</td><td>${escapeAttr(String(allProps[k]))}</td></tr>`).join("")
        : `<tr><td colspan="2"><span class="dp-empty-sm">${el ? "No properties selected" : "Element not found in catalog"}</span></td></tr>`;
      // ⚙ config: native <details> keeps open/close state without JS.
      const cfg = allKeys.length
        ? `<details class="dp-elprops"><summary title="Choose which properties to show">⚙</summary>` +
          `<div class="dp-elprops-list">` +
          allKeys.map((k) =>
            `<label><input type="checkbox" data-elprop data-tblkey="${escapeAttr(key)}" value="${escapeAttr(k)}" ${(!entry.props || !entry.props.length || entry.props.includes(k)) ? "checked" : ""}/> ${escapeAttr(k)}</label>`).join("") +
          `</div></details>`
        : "";
      const search = `<input class="dp-tblsearch" data-tblsearch="${escapeAttr(key)}" placeholder="filter…" title="Filter properties" />`;
      return (
        `<div class="dp-tblwrap">` +
        `<div class="dp-tblhead"><span class="dp-tbllabel">${escapeAttr(title)}${el && el.ifc_type ? ` <span class="dp-roomtag">${escapeAttr(el.ifc_type)}</span>` : ""}</span>${search}${cfg}<button class="dp-tblx" data-rmtable="${escapeAttr(key)}" title="Remove">✕</button></div>` +
        `<table class="dp-tbl"><thead><tr><th>Property</th><th>Value</th></tr></thead><tbody data-tblbody="${escapeAttr(key)}">${bodyRows}</tbody></table>` +
        `</div>`
      );
    }

    // ── Catalog table (assets / work / permits / requests / elements) ──
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
    // Room matching is implicit (GlobalID of the point's room) — the old
    // per-table join-key dropdown is gone; the text filter stays.
    const search = `<input class="dp-tblsearch" data-tblsearch="${key}" placeholder="filter…" title="Filter rows by any value / parameter" />`;
    return (
      `<div class="dp-tblwrap">` +
      `<div class="dp-tblhead"><span class="dp-tbllabel">${escapeAttr(def.label)}</span>${search}<button class="dp-tblx" data-rmtable="${key}" title="Remove">✕</button></div>` +
      `<table class="dp-tbl"><thead>${head}</thead><tbody data-tblbody="${key}">${bodyRows}</tbody></table>` +
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

  // Element picker: elements the IFC places in the point's room, minus ones
  // already added. Lets the user pin down ONE element and watch its chosen
  // properties.
  const roomElements = point.roomId
    ? elementRows.filter((r) => r.globalId === point.roomId && !used.has(`element:${r.id}`))
    : [];
  const elementOpts = roomElements
    .map((r) => `<option value="${escapeAttr(r.id)}">${escapeAttr(r.name || "(unnamed)")} — ${escapeAttr(r.ifc_type)}</option>`)
    .join("");

  return (
    `<div class="dp-sec">` +
    `<div class="dp-sec-lbl">Linked data ${roomName ? `<span class="dp-roomtag">${escapeAttr(roomName)}</span>` : ""}</div>` +
    tablesHtml +
    (optgroups ? `<select class="phase-sel dp-addtbl" data-addtable><option value="">＋ Add table…</option>${optgroups}</select>` : `<div class="dp-empty-sm">All catalog tables added</div>`) +
    (elementOpts ? `<select class="phase-sel dp-addtbl" data-addelement><option value="">＋ Element table (in this room)…</option>${elementOpts}</select>` : "") +
    `</div>`
  );
}

function identificationBlock(point, rooms, phases, room, idProps, propLabel, customSymbols = []) {
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
    // Custom point: editable type name + symbol picker.
    (point.kind === "custom"
      ? `<label class="fld"><span>Custom type</span><input class="fld-in" data-customname value="${escapeAttr(point.kindLabel || "")}" placeholder="e.g. Exit, Hydrant" /></label>` +
        `<div class="dp-syms dp-syms-edit">` +
        customSymbols.map((s) => `<button class="dp-sym${s === (point.symbol || "") ? " on" : ""}" data-customsym="${escapeAttr(s)}" title="Symbol">${escapeAttr(s)}</button>`).join("") +
        `</div>`
      : "") +
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
