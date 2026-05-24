// modal.js — one reusable modal shell (sandbox-safe; no native prompt/confirm).
// openModal(title, contentNode) puts arbitrary content in the body.

export function initModal() {
  const overlay = document.getElementById("modalOverlay");
  document.getElementById("modalX").addEventListener("click", closeModal);
  // Note: NO backdrop-click-to-close — editing modals must close only via
  // ✕ / Cancel / OK so a stray click outside doesn't discard edits. Esc still closes.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.hidden) closeModal();
  });
}

export function openModal(title, contentNode) {
  document.getElementById("modalTitle").textContent = title;
  const body = document.getElementById("modalBody");
  body.innerHTML = "";
  body.appendChild(contentNode);
  document.getElementById("modalOverlay").hidden = false;
  return { close: closeModal };
}

export function closeModal() {
  document.getElementById("modalOverlay").hidden = true;
  document.getElementById("modalBody").innerHTML = "";
}
