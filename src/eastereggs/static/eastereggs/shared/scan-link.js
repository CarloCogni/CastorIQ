// eastereggs/shared/scan-link.js
// Passive observer: listens for scan-phase events forwarded from the opener
// tab via window.postMessage. Never opens its own WebSocket — the main tab
// owns the scan connection.
//
// Games consume this via the global window.ScanLink API.

const cfg = window.CASTOR_SLUG_CONFIG || {};
const statusEl = document.getElementById("scan-status");
const bannerEl = document.getElementById("scan-done-banner");
const returnEl = document.getElementById("scan-done-return");

const state = {
    hasScanLink: Boolean(cfg.hasScanLink),
    scanDone: false,
    lastMessage: null,
    current: 0,
    total: 0,
};

function setStatus(text, isLive) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("live", Boolean(isLive));
}

function showScanDoneBanner() {
    if (!bannerEl) return;
    bannerEl.classList.add("show");
}

if (returnEl) {
    returnEl.addEventListener("click", () => window.close());
}

function handleScanEvent(payload) {
    if (!payload || typeof payload !== "object") return;

    if (payload.type === "phase") {
        state.lastMessage = payload.message || "";
        if (payload.detail && typeof payload.detail.current === "number") {
            state.current = payload.detail.current;
            state.total = payload.detail.total || 0;
            setStatus(
                `SCAN ${state.current}/${state.total || "?"} · ${state.lastMessage}`,
                true,
            );
        } else {
            setStatus(`SCAN · ${state.lastMessage}`, true);
        }
        return;
    }

    if (payload.type === "scan_complete") {
        state.scanDone = true;
        setStatus("SCAN DONE", true);
        showScanDoneBanner();
        return;
    }

    if (payload.type === "error") {
        setStatus("SCAN · error (game continues)", false);
        return;
    }
}

// Listen for messages from the opener tab.
// Security: only accept messages from our own origin.
window.addEventListener("message", (event) => {
    const expected = cfg.expectedOrigin || window.location.origin;
    if (event.origin !== expected) return;

    const msg = event.data;
    if (!msg || msg.channel !== "castor-scan") return;
    handleScanEvent(msg.payload);
});

// Ping opener once on boot so it knows where to send events.
if (state.hasScanLink && window.opener && !window.opener.closed) {
    try {
        window.opener.postMessage(
            { channel: "castor-scan", payload: { type: "popup_ready" } },
            window.location.origin,
        );
    } catch (_err) {
        // Cross-origin opener or closed tab — just skip.
    }
} else if (!state.hasScanLink) {
    setStatus("STANDALONE", false);
} else {
    setStatus("NO OPENER (standalone)", false);
}

// Public API consumed by the game modules.
window.ScanLink = {
    isDone: () => state.scanDone,
    progress: () => ({ current: state.current, total: state.total }),
    hasLink: () => state.hasScanLink,
};
