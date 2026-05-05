# Writeback Streaming — Architecture & Event Schema

The Modify chat streams pipeline progress over a WebSocket connection so the user sees live phase-by-phase feedback (classify → validate → diff → guardian → proposal card) instead of a 30-second spinner. This doc describes the transport contract; the pipeline itself is documented in [writeback/overview.md](../writeback/overview.md) and [writeback/pipeline-architecture.md](writeback/pipeline-architecture.md).

---

## Architecture

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
            ...})                             │
                                              │
       ... each phase streams ...             │
                                              │
       ◄── ws.recv({type: "proposal", ────────┤  proposal created
            proposal: {...}})                 │
                                              │
       ◄── ws.recv({type: "done"})  ──────────┘  pipeline complete
```

Approve / reject / Ask-mode queries remain HTTP POST. WebSocket is reserved for the streaming proposal pipeline.

---

## Components

| Component | File | Role |
|---|---|---|
| `ProposalConsumer` | `writeback/consumers.py` | `AsyncJsonWebsocketConsumer` — accepts WS connections at `ws/projects/<project_id>/modify/`, dispatches `propose` actions to the pipeline via `database_sync_to_async`. |
| `PipelineEmitter` (Protocol) | `writeback/services/emitters.py` | `emit(phase, status, message, detail=None)` — what services call to publish progress. |
| `WebSocketEmitter` | `writeback/services/emitters.py` | Bridges emitter → `async_to_sync(send_json)` on the consumer. |
| `NullEmitter` | `writeback/services/emitters.py` | Silent default for non-streaming contexts (tests, management commands, HTTP fallback). |
| `CapturingEmitter` | `writeback/services/emitters.py` | Stores events in `self.events` for test assertions. |
| ASGI router | `config/asgi.py` | `ProtocolTypeRouter` — HTTP to `django_asgi_app`, WebSocket to `AuthMiddlewareStack(URLRouter(websocket_urlpatterns))`. |
| WebSocket URL routing | `writeback/routing.py` | Maps `ws/projects/<uuid:project_id>/modify/` → `ProposalConsumer`. |
| Channel layer | `settings/base.py` | `InMemoryChannelLayer` (single-process). Swap to `channels_redis` for multi-worker deployment. |

---

## Event Schema

### Phase events

Emitted by the pipeline via the `PipelineEmitter` as each step starts and finishes.

```json
{
    "type": "phase",
    "phase": "triage | classify | extract | resolve | validate | diff | plan | codegen | review | guardian | feasibility | skills | context_budget",
    "status": "running | done | error | info",
    "message": "Human-readable status text",
    "detail": {
        // Optional, phase-specific metadata
    }
}
```

### Phase details

| Phase | Detail Fields |
|---|---|
| `triage` | `segments` (count) |
| `classify` | `tier`, `operation`, `confidence` |
| `validate` | `entities_count`, `groundedness` |
| `diff` | `rows` |
| `plan` | `steps_count` (Tier 2) |
| `codegen` | (none) (Tier 3) |
| `review` | `approved`, `reason` (Tier 3) |
| `guardian` | `verdict`, `result`, `source` |

### Terminal events

```json
// Proposal ready for review
{
    "type": "proposal",
    "proposal": { /* full proposal JSON */ }
}

// Pipeline complete
{ "type": "done" }

// Pipeline error
{
    "type": "error",
    "message": "Human-readable error description"
}
```

---

## Authentication

Channels' `AuthMiddlewareStack` reads the session cookie from the WebSocket handshake, so `self.scope["user"]` works the same as `request.user` in views. The consumer rejects anonymous connections with close code `4001` and unauthorised connections with `4003`. The frontend handles those close codes by redirecting to login or showing an access-denied message.

---

## Performance characteristics

A Tier 1 proposal makes serial LLM calls:

1. `TriageClassifier.classify()` — ~3–5 s on a small Ollama model (narrow segmentation prompt)
2. `SlotExtractor.extract()` — ~2–4 s per segment; single-segment requests = 1 call
3. `EntityNameResolver.resolve()` — ~3–6 s when GUID/step-ID regex doesn't pre-match; skipped when a GUID is present
4. `tier_router.route()` — deterministic, ~1 ms (no LLM)
5. `GuardianService.check()` — embedding (~2–4 s) + pgvector search (~0.3 s) + LLM evaluation (~8–12 s)

Tier 2 adds an `intent_assembler` step (deterministic, ~1 ms — the Tier2Planner LLM call is bypassed). Tier 3 adds planner + reviewer LLM calls. Multi-segment requests scale linearly with segment count at the SlotExtractor stage.

Total wall time is unchanged by streaming — all calls are sequential — but perceived latency drops from 30 s of dead spinner to immediate, meaningful feedback at each phase.

---

## Future extensions

Capabilities the Channels infrastructure naturally supports but that are not currently wired:

| Feature | Mechanism |
|---|---|
| Multi-user live proposal view | Channel groups per project |
| Live approval notifications | Send to project group when proposal approved/rejected |
| Interactive Tier 2/3 escalation | Bidirectional messages mid-pipeline (LLM asks clarifying questions) |
| Concurrent proposal awareness | Broadcast "user X is proposing..." to project group |
| Multi-worker deployment | Swap `InMemoryChannelLayer` → `channels_redis.core.RedisChannelLayer` |
