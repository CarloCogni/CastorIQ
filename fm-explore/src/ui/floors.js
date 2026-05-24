// floors.js — vertical floor switcher + floor-manager modal content.

export function renderFloorSwitcher(container, floors, activeFloorId, onSelect, onEdit) {
  container.innerHTML = "";
  floors.forEach((f) => {
    const b = document.createElement("button");
    b.className = "floor-btn" + (f.id === activeFloorId ? " on" : "");
    b.textContent = f.name;
    b.title = f.label;
    b.addEventListener("click", (e) => { e.stopPropagation(); onSelect(f.id); });
    container.appendChild(b);
  });
  if (onEdit) {
    const edit = document.createElement("button");
    edit.className = "floor-btn floor-edit";
    edit.textContent = "✎";
    edit.title = "Edit floors";
    edit.addEventListener("click", (e) => { e.stopPropagation(); onEdit(); });
    container.appendChild(edit);
  }
}

// Build the floor-manager body node for the modal.
export function buildFloorManager(floors, handlers) {
  const wrap = document.createElement("div");
  const canDelete = floors.length > 1;
  floors.forEach((f, idx) => {
    const row = document.createElement("div");
    row.className = "fm-row";
    row.innerHTML =
      `<div class="fm-ord">` +
        `<button class="fm-mv" data-dir="-1" ${idx === 0 ? "disabled" : ""} title="Move up">↑</button>` +
        `<button class="fm-mv" data-dir="1" ${idx === floors.length - 1 ? "disabled" : ""} title="Move down">↓</button>` +
      `</div>` +
      `<input class="fld-in fm-name" value="${esc(f.name)}" title="Short name (switcher button)" />` +
      `<input class="fld-in fm-label" value="${esc(f.label)}" title="Full label" />` +
      `<button class="btn btn-r btn-sm fm-del" ${canDelete ? "" : "disabled title=\"Can't delete the last floor\""}>✕</button>`;
    const nameI = row.querySelector(".fm-name");
    const labelI = row.querySelector(".fm-label");
    nameI.addEventListener("change", () => handlers.onRename(f.id, { name: nameI.value }));
    labelI.addEventListener("change", () => handlers.onRename(f.id, { label: labelI.value }));
    row.querySelectorAll(".fm-mv").forEach((b) => {
      if (!b.disabled) b.addEventListener("click", () => handlers.onMove(f.id, Number(b.dataset.dir)));
    });
    const del = row.querySelector(".fm-del");
    if (canDelete) del.addEventListener("click", () => handlers.onDelete(f.id));
    wrap.appendChild(row);
  });

  const add = document.createElement("button");
  add.className = "btn btn-sm fm-add";
  add.textContent = "＋ Add floor (import plan)";
  add.addEventListener("click", () => handlers.onAdd());
  wrap.appendChild(add);

  const note = document.createElement("div");
  note.className = "fm-note";
  note.textContent = "↑↓ reorder · rename inline · ＋ add a plan (image/PDF) · ✕ delete (also removes its points)";
  wrap.appendChild(note);
  return wrap;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
