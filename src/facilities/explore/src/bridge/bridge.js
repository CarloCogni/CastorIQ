// bridge.js — postMessage transport to/from the Castor host.
//
// Step 6 scope: outbound emit + ACK/ERROR helpers, and an inbound router that
// dispatches by message type to registered handlers. Step 3 will layer the
// deferred-origin handshake (VIEWER_READY → VIEWER_INIT) and origin locking on
// top of setTargetOrigin(); the handler map below stays the same.

import { SOURCE, MSG, ERR } from "./protocol.js";

let targetOrigin = "*";          // locked from VIEWER_INIT
let lockedOrigin = null;         // trusted parent origin (once handshaked)
let handlers = {};

export function setTargetOrigin(origin) {
  if (origin) targetOrigin = origin;
}

// Lock the trusted parent origin from the VIEWER_INIT message's event.origin.
// A sandboxed iframe without allow-same-origin reports origin "null" — in that
// case we can't target it specifically, so we stay on "*". (Documented in README.)
export function lockOrigin(origin) {
  if (origin && origin !== "null" && /^https?:\/\//.test(origin)) {
    lockedOrigin = origin;
    targetOrigin = origin;
  }
}
export function getLockedOrigin() {
  return lockedOrigin;
}

export function emit(type, payload = {}, requestId) {
  const msg = { source: SOURCE, type, ...payload };
  if (requestId) msg.requestId = requestId;
  try {
    window.parent.postMessage(msg, targetOrigin);
  } catch (e) {
    console.warn("[bridge] emit failed", e);
  }
}

export function ack(requestId, extra = {}) {
  emit(MSG.ACK, { status: "ok", ...extra }, requestId);
}
export function error(requestId, message, code = ERR.BAD_PAYLOAD) {
  emit(MSG.ERROR, { status: "fail", code, message }, requestId);
}

// Register inbound handlers keyed by message type: { FOCUS_ELEMENT: (msg, event) => {} }
export function initBridge(map) {
  handlers = map || {};
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.source === SOURCE) return; // ignore our own outbound (top-level echo / same window)

    if (data.type === MSG.VIEWER_INIT) {
      lockOrigin(event.origin);            // establish the trusted parent
    } else if (lockedOrigin && event.origin !== lockedOrigin) {
      return;                              // reject messages from any other origin
    }

    const handler = handlers[data.type];
    if (handler) {
      handler(data, event);
    } else if (data.type) {
      // Unknown but well-formed message — acknowledge as an error so the host isn't left hanging.
      error(data.requestId, `Unknown message type: ${data.type}`, ERR.UNKNOWN_TYPE);
    }
  });
}
