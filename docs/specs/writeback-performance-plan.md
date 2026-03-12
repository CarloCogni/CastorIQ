# Writeback Performance Improvement — Implementation Guide (v2)

## Context

This document is an implementation plan for **Claude Code** to execute against the Castor codebase. Read the entire document before making any changes. Where items are marked **⚠️ INVESTIGATE**, inspect the actual codebase before proceeding — do NOT assume structure.

### What Changed from v1

| Decision | v1 (Superseded) | v2 (Current) |
|---|---|---|
| Transport | SSE via `StreamingHttpResponse` + in-process `queue.Queue` | Django Channels WebSocket |
| Guardian timing | Async (parallel thread, late arrival) | Sequential (streams inline with other phases) |
| Event infrastructure | Custom `StreamEventBus` singleton | Channels layer (in-memory → Redis) |
| Background execution | Daemon threads spawned from views | Pipeline runs inside WebSocket consumer |
| Emitter pattern | `stream_id` parameter threaded through methods | `PipelineEmitter` protocol injected into services |

**Rationale:** Castor will need bidirectional real-time communication for collaborative features (multi-user proposal review, interactive Tier 2/3 escalation, live approval notifications). Django Channels provides this with a clean upgrade path: in-memory layer now, Redis layer on first deploy, WebSocket consumers alongside HTTP views with no rewrites.

---

## Problem Statement

The writeback `propose()` flow takes 30+ seconds in local development. The bottleneck is **sequential LLM calls** via Ollama (local inference), not Python compute or DB queries.

A Tier 1 proposal currently makes these **serial** calls:

1. `IntentClassifier.classify()` — LLM call (~8–15s)
2. `GuardianService.check()` — which internally does:
   - `EmbeddingService.embed_query()` — embedding call (~2–4s)
   - pgvector similarity search (~0.3s)
   - LLM evaluation call (~8–12s)

Tier 2 adds a planner LLM call. Tier 3 adds planner + reviewer. Up to 5 serial LLM calls.

**The total wall time doesn't change** — all calls remain sequential. But by streaming progress to the user in real-time, perceived latency drops from 30s of dead spinner to immediate, meaningful feedback at each phase.

---

## Solution: WebSocket Streaming with Django Channels

Replace the synchronous POST → wait → JSON response with a **WebSocket connection** that streams progress events as the pipeline executes. The user watches each phase complete live (like a CI/CD build pipeline). The proposal card renders at the end with all data populated, including the Guardian verdict.

### Architecture

```
Frontend (WebSocket)                    Backend (Channels Consumer)
────────────────────                    ───────────────────────────
User types message
       │
       ▼
ws.send({                          ──→  ProposalConsumer.receive_json()
  action: "propose",                          │
  message: "Set fire rating..."               ▼
})                                       Pipeline runs sequentially
                                         inside consumer (sync_to_async)
                                              │
       ◄── ws.recv({type: "phase",  ──────────┤  classify
            phase: "classify",                │
            status: "running", ...})          │
                                              │
       ◄── ws.recv({type: "phase",  ──────────┤  classify done
            phase: "classify",                │
            status: "done", ...})             │
                                              │
       ◄── ws.recv({type: "phase",  ──────────┤  validate
            ...})                             │
                                              │
       ... each phase streams ...             │
                                              │
       ◄── ws.recv({type: "phase",  ──────────┤  guardian done
            phase: "guardian",                │
            status: "done", ...})             │
                                              │
       ◄── ws.recv({type: "proposal", ────────┤  proposal created
            proposal: {...}})                 │
                                              │
       ◄── ws.recv({type: "done"})  ──────────┘  pipeline complete
```

Key properties:

- **Single connection** — opened when modify tab loads, stays open for the session
- **Bidirectional** — the frontend sends proposal requests, the backend streams progress. Future: LLM asks clarifying questions mid-pipeline.
- **Sequential pipeline** — classify → validate → diff → guardian → proposal card. No race conditions, no late-arriving UI updates.
- **HTTP for everything else** — approve, reject, ask-mode queries remain normal POST requests. WebSocket is only for the streaming proposal pipeline.

---

## Part A: Django Channels Infrastructure

### A1. Install Dependencies

```bash
uv add channels channels["daphne"]
```

⚠️ **INVESTIGATE:** Check if `daphne` or `uvicorn` is already listed in `pyproject.toml`. If the project uses `uvicorn`, install `channels["uvicorn"]` instead.

### A2. ASGI Configuration

**File:** `config/asgi.py` (or wherever the ASGI entrypoint lives)

⚠️ **INVESTIGATE:** Check the actual project structure. The ASGI file may be at `castor/asgi.py` or `config/asgi.py`. Look for the existing `asgi.py` that Django scaffolded.

```python
"""
ASGI entrypoint for Castor.
Routes HTTP through standard Django, WebSocket through Channels.
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from writeback.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
```

### A3. Settings Changes

**File:** `config/settings.py` (or equivalent)

```python
INSTALLED_APPS = [
    "daphne",  # Must be BEFORE django.contrib.staticfiles
    # ... existing apps ...
    "channels",
    # ...
]

# Channels
ASGI_APPLICATION = "config.asgi.application"

# In-memory channel layer for local dev. Swap to Redis for production.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}
```

⚠️ **INVESTIGATE:** Check `settings.py` for:
- `WSGI_APPLICATION` — it should stay as-is; Daphne handles both HTTP and WebSocket.
- `MIDDLEWARE` — look for `GZipMiddleware` or any response-manipulating middleware. These don't affect WebSocket connections (Channels bypasses Django middleware for WS), but verify nothing else depends on the WSGI path.
- `CONN_MAX_AGE` — if non-zero, database connections persist per-thread. Channels uses an async event loop, so connections need to be managed carefully. `database_sync_to_async` handles this, but verify.

### A4. WebSocket Routing

**File:** `writeback/routing.py` (new)

```python
from django.urls import path
from writeback.consumers import ProposalConsumer

websocket_urlpatterns = [
    path("ws/projects/<uuid:project_id>/modify/", ProposalConsumer.as_asgi()),
]
```

⚠️ **INVESTIGATE:** Check the URL patterns in `writeback/urls.py` to confirm the project PK parameter name. It may be `pk` instead of `project_id`. Match whatever the existing views use.

---

## Part B: Pipeline Emitter Abstraction

Before touching the consumer or the pipeline, define the abstraction that decouples pipeline progress from the transport layer.

### B1. Emitter Protocol

**File:** `writeback/services/emitters.py` (new)

```python
"""
Pipeline emitter abstraction.

Services emit progress events through this protocol without knowing
whether they're going to a WebSocket, a test harness, or nowhere.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PipelineEmitter(Protocol):
    """Protocol for pipeline progress emission."""

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None: ...


class NullEmitter:
    """
    Silent emitter for non-streaming contexts.
    Used when the pipeline runs synchronously (tests, management commands,
    or backward-compat code paths).
    """

    def emit(self, phase: str, status: str, message: str, detail=None) -> None:
        logger.debug(f"[pipeline] {phase}: {status} — {message}")


class WebSocketEmitter:
    """
    Emits pipeline events over a Channels WebSocket connection.

    Accepts an async `send_json` callable (from the consumer) and
    calls it synchronously via async_to_sync. This is safe because
    the pipeline runs in a sync thread via database_sync_to_async.
    """

    def __init__(self, send_fn):
        """
        Args:
            send_fn: An async callable that sends JSON to the WebSocket.
                     Typically `self.send_json` from the consumer.
        """
        from asgiref.sync import async_to_sync
        self._send = async_to_sync(send_fn)

    def emit(self, phase: str, status: str, message: str, detail=None) -> None:
        self._send({
            "type": "phase",
            "phase": phase,
            "status": status,
            "message": message,
            "detail": detail,
        })


class CapturingEmitter:
    """
    Captures all events in a list. Useful for testing.

    Usage:
        emitter = CapturingEmitter()
        service.propose(..., emitter=emitter)
        assert emitter.events[0]["phase"] == "classify"
    """

    def __init__(self):
        self.events: list[dict] = []

    def emit(self, phase: str, status: str, message: str, detail=None) -> None:
        self.events.append({
            "phase": phase,
            "status": status,
            "message": message,
            "detail": detail,
        })
```

### B2. Instrument `ModificationService.propose()` with Emitter

**File:** `writeback/services/modification_service.py`

Replace all `stream_id` references from v1 with the emitter. The `propose()` method receives an optional `emitter` parameter, defaulting to `NullEmitter`.

**Key changes:**

```python
from writeback.services.emitters import PipelineEmitter, NullEmitter


class ModificationService:

    def propose(self, user_message, user, ifc_file=None, message_obj=None,
                emitter: PipelineEmitter | None = None):
        """
        Run the full proposal pipeline.

        Args:
            emitter: Optional emitter for streaming progress.
                     When None, uses NullEmitter (silent, backward-compat).
        """
        if emitter is None:
            emitter = NullEmitter()

        # --- Phase: Classify ---
        emitter.emit("classify", "running", "Classifying intent...")

        # ⚠️ INVESTIGATE: Check current entity fetching logic here.
        # The entity context build may need caching (see Part D).
        entity_context = self._get_entity_context()
        classified = self.classifier.classify(user_message, entity_context)

        tier = classified.get("tier", "?")
        operation = classified.get("operation", "?")
        confidence = classified.get("confidence")

        emitter.emit("classify", "done",
                     f"Tier {tier} — {operation}",
                     detail={"tier": tier, "operation": operation,
                             "confidence": confidence})

        # --- Check confidence threshold ---
        # ⚠️ INVESTIGATE: Find where confidence rejection currently happens.
        # Emit an error via emitter if confidence is too low.
        if confidence and confidence < 60:
            emitter.emit("classify", "error",
                         f"Low confidence ({confidence}%). Please be more specific.")
            return None  # or raise ModificationError

        # --- Phase: Validate ---
        emitter.emit("validate", "running",
                     f"Validating against matched entities...")

        # ... existing validation logic ...
        # ⚠️ INVESTIGATE: The validation step matches entities and checks
        # groundedness. Wrap the existing logic, emitting done/error after.

        emitter.emit("validate", "done",
                     "Validation passed",
                     detail={"entities_count": len(matched_entities),
                             "groundedness": groundedness_score})

        # --- Phase: Diff ---
        emitter.emit("diff", "running", "Generating modification preview...")

        # ... existing diff/proposal creation logic ...

        emitter.emit("diff", "done", "Preview ready")

        # --- Phase: Guardian (sequential) ---
        emitter.emit("guardian", "running", "Checking project documents...")

        try:
            GuardianService(user=user).check(proposal)

            status_labels = {
                "verified": "Confirmed by documents",
                "conflict": "Possible conflict detected",
                "unknown": "No relevant documents found",
                "failed": "Check unavailable",
            }
            verdict = proposal.verification_status
            message = status_labels.get(verdict, "Check complete")
            if proposal.verification_source:
                message += f" — {proposal.verification_source}"

            emitter.emit("guardian", "done", message,
                         detail={"verdict": verdict,
                                 "result": proposal.verification_result,
                                 "source": proposal.verification_source})

        except Exception as e:
            logger.warning(f"Guardian check failed (non-blocking): {e}")
            emitter.emit("guardian", "done", "Document check unavailable",
                         detail={"verdict": "failed"})

        # --- Phase: Complete ---
        # The consumer handles emitting the final proposal JSON and "done"
        # event, since it needs to serialize the proposal with DRF/custom logic.

        return proposal  # (or list of proposals for chains)
```

**Apply the same emitter pattern to:**
- `_propose_chain()` — emit events per chain element
- `_propose_tier2()` — emit planning phase events
- `_propose_tier3()` — emit code gen + review phase events

**Important:** When `emitter` is `NullEmitter` (default), the method behaves exactly as before — synchronous, no side effects beyond logging. This preserves backward compatibility for tests, management commands, and any code path that calls `propose()` directly.

---

## Part C: WebSocket Consumer

### C1. Consumer Implementation

**File:** `writeback/consumers.py` (new)

```python
"""
WebSocket consumer for streaming proposal pipeline progress.
"""
import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)


class ProposalConsumer(AsyncJsonWebsocketConsumer):
    """
    Handles WebSocket connections for the modify tab.

    Lifecycle:
    1. Client opens WS on tab load
    2. Client sends {action: "propose", message: "..."} to start pipeline
    3. Server streams {type: "phase", ...} events as pipeline runs
    4. Server sends {type: "proposal", ...} with the final result
    5. Server sends {type: "done"} to signal completion
    6. Connection stays open for subsequent proposals

    Approve/reject remain HTTP POST — only the propose flow uses WS.
    """

    async def connect(self):
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]
        self.user = self.scope["user"]

        # Reject unauthenticated connections
        if self.user.is_anonymous:
            await self.close(code=4001)
            return

        # Verify project access
        has_access = await self._check_project_access()
        if not has_access:
            await self.close(code=4003)
            return

        await self.accept()

    async def disconnect(self, close_code):
        pass  # No cleanup needed yet. Future: leave group channels.

    async def receive_json(self, content, **kwargs):
        """
        Dispatch incoming messages by action.
        Currently only 'propose' is supported.
        """
        action = content.get("action")

        if action == "propose":
            await self._handle_propose(content)
        else:
            await self.send_json({
                "type": "error",
                "message": f"Unknown action: {action}",
            })

    async def _handle_propose(self, content):
        """
        Run the proposal pipeline and stream progress to the client.
        """
        message_text = content.get("message", "").strip()
        if not message_text:
            await self.send_json({
                "type": "error",
                "message": "Message is required.",
            })
            return

        session_id = content.get("session_id")  # Optional, for resuming sessions

        try:
            result = await self._run_pipeline(message_text, session_id)

            if result is None:
                # Pipeline returned None (e.g., low confidence rejection)
                # Error was already emitted via the emitter
                await self.send_json({"type": "done"})
                return

            # Serialize and send the proposal(s)
            serialized = await self._serialize_result(result)
            await self.send_json({
                "type": "proposal",
                **serialized,
            })

            await self.send_json({"type": "done"})

        except Exception as e:
            logger.exception(f"Proposal pipeline failed: {e}")
            await self.send_json({
                "type": "error",
                "message": "An unexpected error occurred. Please try again.",
            })

    @database_sync_to_async
    def _run_pipeline(self, message_text, session_id=None):
        """
        Runs the synchronous proposal pipeline in a sync thread.
        database_sync_to_async handles DB connection management.
        """
        from writeback.services.modification_service import ModificationService
        from writeback.services.emitters import WebSocketEmitter
        from chat.models import ChatSession, Message

        # ⚠️ INVESTIGATE: Check how ModificationService is currently
        # instantiated in the view. It likely takes (project, user=user).
        # Also check how _resolve_session() works — it may create or
        # fetch a ChatSession. Replicate that logic here.

        project = self._get_project_sync()
        session = self._resolve_session_sync(project, session_id)

        # Create the user message in the DB
        user_msg = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=message_text,
        )

        # Create the emitter that sends events to *this* WebSocket
        emitter = WebSocketEmitter(self.send_json)

        # Run the pipeline
        svc = ModificationService(project, user=self.user)
        result = svc.propose(
            user_message=message_text,
            user=self.user,
            message_obj=user_msg,
            emitter=emitter,
        )

        # ⚠️ INVESTIGATE: After propose(), the current view creates an
        # assistant Message and links proposals to it. Replicate that here.
        # Check _handle_propose() in ModifyView for the exact logic.

        return result

    @database_sync_to_async
    def _check_project_access(self):
        """Check if the user has access to the project."""
        from environments.models import Project
        try:
            project = Project.objects.get(pk=self.project_id)
            return project.user_has_access(self.user)
        except Project.DoesNotExist:
            return False

    def _get_project_sync(self):
        """Get project (called from sync context)."""
        from environments.models import Project
        return Project.objects.get(pk=self.project_id)

    def _resolve_session_sync(self, project, session_id=None):
        """
        Resolve or create a chat session.

        ⚠️ INVESTIGATE: Check how ModifyView._resolve_session() works.
        Replicate that logic here. It likely:
        1. If session_id provided, fetch it
        2. Otherwise, get-or-create a modify session for this project/user
        """
        from chat.models import ChatSession
        # Placeholder — replace with actual session resolution logic
        if session_id:
            return ChatSession.objects.get(pk=session_id)
        return ChatSession.objects.filter(
            project=project,
            user=self.user,
            # ⚠️ INVESTIGATE: Check for session_type or mode field
        ).order_by("-created_at").first()

    @database_sync_to_async
    def _serialize_result(self, result):
        """
        Serialize proposal(s) to JSON for the WebSocket response.

        ⚠️ INVESTIGATE: Check how the current view serializes proposals.
        It may use DRF serializers or manual dict construction.
        Replicate that format here so the frontend doesn't need changes
        beyond the transport layer.
        """
        # Placeholder — replace with actual serialization
        if isinstance(result, list):
            return {
                "chain": True,
                "proposals": [self._serialize_proposal_sync(p) for p in result],
            }
        return {"proposal": self._serialize_proposal_sync(result)}

    def _serialize_proposal_sync(self, proposal):
        """
        Serialize a single proposal to dict.

        ⚠️ INVESTIGATE: Match the exact JSON shape that the frontend
        currently expects from _handle_propose(). Fields likely include:
        id, tier, operation, status, diff, verification_status, etc.
        """
        return {
            "id": str(proposal.pk),
            # ... fill in from existing serialization logic ...
        }
```

### C2. Authentication Notes

Channels' `AuthMiddlewareStack` reads the session cookie from the WebSocket handshake, so `self.scope["user"]` works the same as `request.user` in views — **provided the user is logged in via Django sessions**.

⚠️ **INVESTIGATE:** Verify the project uses session-based auth (not token-based or JWT). If it uses `django.contrib.auth` with session middleware (which is typical for Django template apps), this works out of the box.

The consumer rejects anonymous connections with close code `4001` and unauthorized connections with `4003`. The frontend should handle these close codes and redirect to login or show an access denied message.

---

## Part D: Frontend Changes

### D1. WebSocket Connection Manager

**File:** `writeback/templates/writeback/tabs/_modify.html`

⚠️ **INVESTIGATE:** Verify this is the correct template. Check how the modify tab is included — it may be a partial loaded by HTMX or included in a parent template.

Add a WebSocket connection manager to `ModifyChat`:

```javascript
// Inside ModifyChat object (or class, depending on current implementation)

_ws: null,
_wsReconnectAttempts: 0,
_maxReconnectAttempts: 5,

initWebSocket() {
    // ⚠️ INVESTIGATE: Check how projectPk is currently available
    // in the template. It may be a data attribute, a JS variable
    // set by Django template tag, or passed via HTMX.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/projects/${this.projectPk}/modify/`;

    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
        console.log('[modify] WebSocket connected');
        this._wsReconnectAttempts = 0;
    };

    this._ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        this._handleWsMessage(data);
    };

    this._ws.onclose = (event) => {
        console.log(`[modify] WebSocket closed: ${event.code}`);

        if (event.code === 4001) {
            // Unauthenticated — redirect to login
            window.location.href = '/accounts/login/';
            return;
        }
        if (event.code === 4003) {
            this._appendBubble('assistant', '⚠️ Access denied to this project.');
            return;
        }

        // Attempt reconnect for unexpected closures
        this._attemptReconnect();
    };

    this._ws.onerror = (error) => {
        console.error('[modify] WebSocket error:', error);
    };
},

_attemptReconnect() {
    if (this._wsReconnectAttempts >= this._maxReconnectAttempts) {
        this._appendBubble('assistant',
            '❌ Connection lost. Please refresh the page.');
        return;
    }
    this._wsReconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this._wsReconnectAttempts), 10000);
    console.log(`[modify] Reconnecting in ${delay}ms (attempt ${this._wsReconnectAttempts})`);
    setTimeout(() => this.initWebSocket(), delay);
},
```

### D2. Message Dispatch

Replace the current `sendMessage()` HTTP POST with a WebSocket send:

```javascript
async sendMessage() {
    const text = this.inputEl.value.trim();
    if (!text) return;

    // Check connection
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
        this._appendBubble('assistant', '⚠️ Not connected. Reconnecting...');
        this.initWebSocket();
        return;
    }

    this._appendBubble('user', text);
    this.inputEl.value = '';
    this.inputEl.style.height = 'auto';

    // Show the progress tracker
    this._showProgressTracker();

    // Send over WebSocket
    this._ws.send(JSON.stringify({
        action: 'propose',
        message: text,
        session_id: this.sessionId || null,
    }));
},
```

### D3. Incoming Message Handler

```javascript
_handleWsMessage(data) {
    switch (data.type) {
        case 'phase':
            this._updateProgressPhase(data);
            break;

        case 'proposal':
            this._hideProgressTracker();
            if (data.chain && data.proposals) {
                // ⚠️ INVESTIGATE: Check how chain proposals are currently
                // rendered. There's likely an _appendChainCard() or similar.
                data.proposals.forEach(p => this._appendProposalCard(p));
            } else {
                this._appendProposalCard(data.proposal);
            }
            break;

        case 'error':
            this._hideProgressTracker();
            this._appendBubble('assistant', `⚠️ ${data.message}`);
            break;

        case 'done':
            // Pipeline complete. Progress tracker should already be hidden
            // (removed when proposal arrives). This is a safety cleanup.
            this._hideProgressTracker();
            break;

        default:
            console.warn('[modify] Unknown WS message type:', data.type);
    }
},
```

### D4. Progress Tracker UI

Replace the spinner with a phased progress display. This is the same concept as v1, now wired to WebSocket messages.

```javascript
_showProgressTracker() {
    this._progressTrackerVisible = true;
    const html = `
    <div class="message message-assistant" id="modify-progress">
        <div class="message-avatar" style="background-color: var(--castor-accent);">
            <i class="bi bi-tools"></i>
        </div>
        <div class="message-content">
            <div class="d-flex flex-column gap-2" id="progress-phases">
                <!-- Phases added dynamically -->
            </div>
        </div>
    </div>`;
    this.chatEl.insertAdjacentHTML('beforeend', html);
    this.scrollToBottom();
},

_updateProgressPhase(data) {
    const container = document.getElementById('progress-phases');
    if (!container) return;

    const phaseId = `progress-phase-${data.phase}`;
    let phaseEl = document.getElementById(phaseId);

    if (!phaseEl) {
        phaseEl = document.createElement('div');
        phaseEl.id = phaseId;
        phaseEl.className = 'd-flex align-items-center gap-2 fs-8';
        container.appendChild(phaseEl);
    }

    const icons = {
        running: '<div class="spinner-border spinner-border-sm text-primary" role="status"></div>',
        done: '<i class="bi bi-check-circle-fill text-success"></i>',
        error: '<i class="bi bi-x-circle-fill text-danger"></i>',
    };

    phaseEl.innerHTML = `
        ${icons[data.status] || icons.running}
        <span class="${data.status === 'done' ? 'text-body' : 'text-secondary'}">
            ${data.message}
        </span>
    `;

    // Render phase-specific detail badges
    if (data.status === 'done' && data.detail) {
        const badge = this._renderPhaseDetail(data.phase, data.detail);
        if (badge) phaseEl.insertAdjacentHTML('beforeend', badge);
    }

    this.scrollToBottom();
},

_renderPhaseDetail(phase, detail) {
    if (phase === 'classify' && detail.tier) {
        const tierColors = { 1: 'var(--castor-success)', 2: '#f97316', 3: '#ef4444' };
        return `<span class="badge rounded-pill text-white ms-1"
                      style="background-color: ${tierColors[detail.tier]}; font-size: 0.65rem;">
                    Tier ${detail.tier}
                </span>`;
    }
    if (phase === 'validate' && detail.entities_count) {
        return `<span class="text-secondary ms-1">(${detail.entities_count} entities)</span>`;
    }
    if (phase === 'guardian') {
        const verdictColors = {
            verified: 'var(--castor-success)',
            conflict: '#f97316',
            unknown: '#6b7280',
            failed: '#6b7280',
        };
        const verdictIcons = {
            verified: 'bi-shield-check',
            conflict: 'bi-exclamation-triangle',
            unknown: 'bi-question-circle',
            failed: 'bi-dash-circle',
        };
        const color = verdictColors[detail.verdict] || '#6b7280';
        const icon = verdictIcons[detail.verdict] || 'bi-dash-circle';
        return `<i class="bi ${icon} ms-1" style="color: ${color};"></i>`;
    }
    return '';
},

_hideProgressTracker() {
    this._progressTrackerVisible = false;
    document.getElementById('modify-progress')?.remove();
},
```

⚠️ **INVESTIGATE:** The current template references a `CastorLoader` global object (see `_showLoader` method). Verify how it's defined and loaded. The old loader should be kept for approve/reject actions (which remain synchronous POST). Only the propose flow uses the new progress tracker.

### D5. Initialize on Tab Load

```javascript
// ⚠️ INVESTIGATE: Check how ModifyChat is currently initialized.
// It may be in a DOMContentLoaded listener, an HTMX afterSwap event,
// or called from a parent template. Add initWebSocket() to the
// existing initialization flow.

// Example (adjust to actual codebase):
document.addEventListener('DOMContentLoaded', () => {
    ModifyChat.init();        // existing
    ModifyChat.initWebSocket(); // new
});
```

---

## Part E: Additional Optimizations (Lower Priority)

### E1. Cache Entity Context Per Project

**File:** `writeback/services/modification_service.py`

⚠️ **INVESTIGATE:** Check if Django's cache framework is configured in `settings.py` (look for `CACHES`). If not, use a simple module-level dict with TTL.

The `build_entity_context()` result is the same for every proposal in the same project (until an IFC file is uploaded or modified). Cache it:

```python
from django.core.cache import cache

def _get_entity_context(self):
    cache_key = f"entity_context_{self.project.pk}"
    context = cache.get(cache_key)
    if context is None:
        all_entities = list(
            IFCEntity.objects.filter(
                ifc_file__project_id=self.project.pk,
                ifc_file__status="completed",
            )[:100]
        )
        context = self.classifier.build_entity_context(all_entities)
        cache.set(cache_key, context, timeout=300)  # 5 min TTL
    return context
```

**Invalidate** the cache in `execute()` (after a successful modification) and in the IFC upload pipeline.

⚠️ **INVESTIGATE:** Find where IFC file upload/processing happens (likely in `ifc_processor/` app) and add cache invalidation there too:

```python
cache.delete(f"entity_context_{project.pk}")
```

### E2. Smaller Model for Guardian

⚠️ **INVESTIGATE:** Check these files to understand the current LLM selection mechanism:
- `core/llm.py` — the `get_llm()` factory function
- `core/models.py` — `UserLLMConfig` model
- `core/model_registry.py` — model registry with VRAM estimates

The Guardian and Tier3Reviewer don't need the user's selected model. They perform simpler classification tasks (CONFIRMED / CONFLICT / NO_INFO). Consider:

1. Adding a `get_llm(user, role="guardian")` variant that selects a smaller model
2. Or adding a `GUARDIAN_MODEL` setting in `.env` / `settings.py`
3. Or allowing per-task model overrides in `UserLLMConfig`

This is a design decision — discuss with the project owner before implementing.

---

## Event Schema Reference

### Phase Events

Emitted by the pipeline via the `PipelineEmitter` as each step starts and finishes.

```json
{
    "type": "phase",
    "phase": "classify | validate | diff | plan | codegen | review | guardian",
    "status": "running | done | error",
    "message": "Human-readable status text",
    "detail": {
        // Optional, phase-specific metadata
    }
}
```

### Phase Details by Phase

| Phase | Detail Fields |
|---|---|
| `classify` | `tier`, `operation`, `confidence` |
| `validate` | `entities_count`, `groundedness` |
| `diff` | (none) |
| `plan` | `steps_count` (Tier 2) |
| `codegen` | (none) (Tier 3) |
| `review` | `approved`, `reason` (Tier 3) |
| `guardian` | `verdict`, `result`, `source` |

### Terminal Events

```json
// Proposal ready for review
{
    "type": "proposal",
    "proposal": { /* full proposal JSON */ }
}

// Chain of proposals
{
    "type": "proposal",
    "chain": true,
    "proposals": [ /* array of proposal JSON */ ]
}

// Pipeline complete (close is optional, connection stays open)
{ "type": "done" }

// Pipeline error
{
    "type": "error",
    "message": "Human-readable error description"
}
```

---

## Implementation Order

Execute in this order to minimize risk. Each step should be independently testable.

### Step 1: Install and Configure Channels
- [ ] `uv add channels channels["daphne"]`
- [ ] Create `config/asgi.py` with `ProtocolTypeRouter`
- [ ] Add `daphne`, `channels` to `INSTALLED_APPS`
- [ ] Add `CHANNEL_LAYERS` with `InMemoryChannelLayer`
- [ ] Set `ASGI_APPLICATION`
- [ ] Verify `manage.py runserver` still works (Daphne takes over)
- [ ] Manual test: confirm all existing HTTP views still work

### Step 2: Emitter Abstraction
- [ ] Create `writeback/services/emitters.py` with all four emitter classes
- [ ] Unit test: `CapturingEmitter` captures events correctly
- [ ] Unit test: `NullEmitter` doesn't raise

### Step 3: Minimal WebSocket Consumer
- [ ] Create `writeback/routing.py`
- [ ] Create `writeback/consumers.py` with a stub `ProposalConsumer` that echoes messages
- [ ] Wire routing into `config/asgi.py`
- [ ] Manual test: connect via browser console `new WebSocket(...)`, send JSON, verify echo

### Step 4: Frontend WebSocket Plumbing
- [ ] Add `initWebSocket()` and `_handleWsMessage()` to modify template
- [ ] Implement progress tracker UI (`_showProgressTracker`, `_updateProgressPhase`, `_hideProgressTracker`)
- [ ] Manual test: send a mock `{action: "propose", message: "test"}`, verify progress tracker renders from hardcoded echo events

### Step 5: Instrument `ModificationService.propose()` with Emitter
- [ ] Add `emitter` parameter (optional, default `NullEmitter`)
- [ ] Add `emitter.emit()` calls at each pipeline phase
- [ ] Verify: when `emitter=None` / `NullEmitter`, behavior is identical to current
- [ ] Unit test with `CapturingEmitter`: verify correct phases and ordering

### Step 6: Wire Consumer to Real Pipeline
- [ ] Replace echo stub with `_run_pipeline()` calling `ModificationService.propose()`
- [ ] Handle session resolution, message creation, proposal serialization
- [ ] Handle DB connections (`database_sync_to_async`)
- [ ] End-to-end test: type a real proposal, watch progress, see proposal card

### Step 7: Chain + Tier 2/3 Support
- [ ] Instrument `_propose_chain()` with emitter
- [ ] Instrument `_propose_tier2()` with planning phase events
- [ ] Instrument `_propose_tier3()` with code gen + review phase events
- [ ] Test each tier end-to-end

### Step 8: Polish and Harden
- [ ] Error handling: ensure all code paths in the pipeline emit error events or get caught by the consumer's try/except
- [ ] Connection handling: test reconnect logic (kill server, verify client reconnects)
- [ ] Edge cases: empty messages, rapid-fire proposals, connection drop mid-pipeline
- [ ] Remove any dead code from the old synchronous propose path (if fully replaced)
- [ ] Keep the HTTP POST path working for approve/reject
- [ ] Full end-to-end test with a real Ollama model across all tiers

---

## Things to Investigate Before Coding

These items **must be verified in the actual codebase** before implementation:

### Infrastructure

1. **⚠️ ASGI entrypoint** — Does `asgi.py` already exist? Where is it? (`config/asgi.py` or `castor/asgi.py`?)

2. **⚠️ Django cache configuration** — Is `CACHES` configured in `settings.py`? If not, entity context caching needs a different approach (module-level dict with TTL, or skip for now).

3. **⚠️ Middleware stack** — List all middleware in `settings.py`. Check for `GZipMiddleware` or any response-manipulating middleware. These don't affect WebSocket (Channels bypasses Django middleware for WS), but verify nothing has unexpected side effects.

4. **⚠️ WSGI/ASGI config** — Check if the project uses `gunicorn`, `uvicorn`, or just `manage.py runserver`. Daphne replaces the dev server automatically when listed in `INSTALLED_APPS`.

5. **⚠️ `CONN_MAX_AGE`** — Check if set to non-zero. `database_sync_to_async` manages connections correctly, but verify.

### Views and Services

6. **⚠️ `core/llm.py` internals** — What does `get_llm()` return? Is it a LangChain `ChatOllama`? Is it safe to call from a `database_sync_to_async` context? Verify no thread-local or request-scoped state.

7. **⚠️ `EmbeddingService`** — Used by `GuardianService._search_documents()`. Check if it holds model state or connections that aren't safe across async boundaries.

8. **⚠️ `ModificationService` constructor** — What arguments does it take? Does it store any request-scoped state? Verify it's safe to instantiate inside the consumer.

9. **⚠️ `_handle_propose` in `ModifyView`** — Study the full flow: how it creates Messages, links proposals to assistant Messages, handles chains, serializes the response JSON. The consumer must replicate this logic exactly.

10. **⚠️ `_resolve_session()` in `ModifyView`** — How does session creation/resolution work? What fields does it use?

### Frontend

11. **⚠️ `CastorLoader` global** — Where is it defined? It's referenced in `_showLoader()`. Keep it for approve/reject; replace only for propose.

12. **⚠️ Template inclusion** — How is `_modify.html` loaded? HTMX swap? Django include tag? This determines where `initWebSocket()` should be called.

13. **⚠️ `projectPk` availability** — How does the frontend currently know the project UUID? Data attribute? Template variable? Check and use the same mechanism.

14. **⚠️ Proposal card rendering** — The `_appendProposalCard()` method. What data shape does it expect? The consumer's serialized JSON must match.

15. **⚠️ `sessionId` availability** — Is the chat session ID available on the frontend? Check if it's stored in a JS variable, data attribute, or URL parameter.

### Auth and Access Control

16. **⚠️ Authentication method** — Session-based (Django default) or token-based? Channels' `AuthMiddlewareStack` needs session auth.

17. **⚠️ `project.user_has_access()`** — Verify this is a simple DB query, safe to call from `database_sync_to_async`.

---

## What NOT to Do

- **Do NOT add Redis or Celery.** The in-memory channel layer is sufficient for local dev. When deploying to production, swap to `channels_redis` (one-line config change).

- **Do NOT refactor the entire `ModifyView`.** Keep approve/reject as synchronous HTTP POST. Only the propose flow moves to WebSocket.

- **Do NOT break the non-streaming path.** The `emitter=None` default (→ `NullEmitter`) must keep `ModificationService.propose()` working exactly as before. This is critical for testability and rollback.

- **Do NOT remove the existing HTTP propose endpoint immediately.** Keep it as a fallback until the WebSocket flow is fully tested. Delete it in Step 8 (Polish) once you're confident.

- **Do NOT over-engineer the consumer.** No channel groups, no room management, no pub/sub — just a direct connection between one client and one pipeline execution. Future collaboration features will add group channels later.

---

## Future Extensions (Not in Scope)

These are natural next steps enabled by the Channels infrastructure, but should **not** be implemented now:

| Feature | Channels Mechanism |
|---|---|
| Multi-user live proposal view | Channel groups per project |
| Live approval notifications | Send to project group when proposal approved/rejected |
| Interactive Tier 2/3 escalation | Bidirectional messages mid-pipeline (LLM asks questions) |
| Concurrent proposal awareness | Broadcast "user X is proposing..." to project group |
| Redis channel layer for deployment | Swap `InMemoryChannelLayer` → `channels_redis.core.RedisChannelLayer` |

---

## Summary

| Change | Impact | Risk |
|---|---|---|
| Django Channels + WebSocket | Proper real-time infrastructure with upgrade path | Medium — new dependency, but isolated to propose flow |
| Sequential streaming pipeline | Perceived latency drops from 30s to <1s (user sees activity immediately) | Low — same logic, just with emitter calls added |
| Emitter abstraction | Clean separation of pipeline logic from transport | Low — new code, no existing code changes |
| Progress tracker UI | Replaces spinner with meaningful live feedback | Low — frontend only |
| Entity context caching | Saves ~0.5s per request | Low — simple cache |

The user experience transforms from "stare at spinner for 30 seconds" to "watch a live pipeline that shows classification → validation → document check → proposal card, with each phase completing in real time."