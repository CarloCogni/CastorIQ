// protocol.js — single source of truth for the postMessage protocol with Castor.
//
// Envelope: inbound  { type, id?, requestId?, ...payload }
//           outbound { source:'fm-explore', type, requestId?, ...payload }
//
// Step 6 uses FOCUS_ELEMENT / HOTSPOT_CLICKED / ACK / ERROR. Step 3 adds the full
// handshake (VIEWER_READY → VIEWER_INIT), origin locking and SET_* messages.

export const SOURCE = "fm-explore";

export const MSG = {
  // ── inbound (Castor → Explore) ──
  VIEWER_INIT: "VIEWER_INIT",
  FOCUS_ELEMENT: "FOCUS_ELEMENT",
  SET_THEME: "SET_THEME",
  SET_FLOORS: "SET_FLOORS",
  SET_ROOMS: "SET_ROOMS",
  SET_TABLE_CATALOG: "SET_TABLE_CATALOG",
  // v0.2 — hydrates the working set from server-persisted data on load.
  SET_USER_STATE: "SET_USER_STATE",
  GET_STATE: "GET_STATE",

  // ── outbound (Explore → Castor) ──
  VIEWER_READY: "VIEWER_READY",
  ACK: "ACK",
  ERROR: "ERROR",
  HOTSPOT_CLICKED: "HOTSPOT_CLICKED",
  SCENE_CHANGED: "SCENE_CHANGED",
  STATE: "STATE",
  STATE_CHANGED: "STATE_CHANGED", // debounced notice that the working set changed (so the host can persist)
};

export const ERR = {
  UNKNOWN_GLOBAL_ID: "UNKNOWN_GLOBAL_ID",
  BAD_PAYLOAD: "BAD_PAYLOAD",
  UNKNOWN_TYPE: "UNKNOWN_TYPE",
};
