# Write-Back System

The write-back system is Castor's core innovation: the ability to modify IFC files through natural language with human approval and full traceability.

## Core Principle: Risk-Stratified Autonomous Action (RSAA)

Always attempt the safest execution tier first. Escalate only when the current tier cannot handle the request. The LLM never exercises more power than the task requires (**Minimal Authority**).

## Pipeline at a glance

The pipeline runs four narrow LLM stages plus one deterministic router. No single LLM call is asked to do more than one cognitively coherent job — narrow per-stage prompts keep small Ollama models on schema.

```
user message
   │
   ▼
TriageClassifier (LLM #1)          — splits into action segments
   │                                 [{kind, target_phrase, value_phrase}]
   │                                 kinds: PROPERTY | ATTRIBUTE | PSET |
   │                                 CREATE | DELETE | RELATIONSHIP |
   │                                 OUT_OF_SCOPE | UNCLEAR
   ▼
SlotExtractor (LLM #2 per segment) — per-kind narrow prompt fills a small
   │                                 fixed slot set; vague-value /
   │                                 generic-name guards fire per-segment
   ▼
EntityNameResolver (LLM #3)        — mode-aware:
   │                                 EXISTING_TARGET / PARENT_TARGET /
   │                                 NEW_TARGET / NO_TARGET
   ▼
tier_router.route()                — DETERMINISTIC, NO LLM
   │                                 single-segment + standard pset → T1
   │                                 single-segment + custom pset → T2
   │                                 multi-segment (any combination) → T2
   │                                 CREATE / DELETE / RELATIONSHIP → T3
   │                                 OUT_OF_SCOPE / UNCLEAR / no entities → T0
   ▼
intent_assembler                   — builds the intent dict the writers consume
   │
   ▼
T1 / T2 / T3 dispatchers
```

For the full design rationale and the deterministic policy table, see [pipeline-architecture.md](../specs/writeback/pipeline-architecture.md).

## Three-Tier Escalation

### Tier 1 — GREEN (Certified Operations)

The LLM stages emit structured slots. Pre-coded, tested handler functions execute the changes.

- **Operations:** `SET_PROPERTY`, `ADD_PROPERTY`, `REMOVE_PROPERTY`, `SET_ATTRIBUTE`
- **Validation:** Target pset exists, property exists (SET/REMOVE) or doesn't (ADD), type compatible, filter matches ≥1 entity. SET_PROPERTY upserts on standard psets, so the SET→ADD auto-fallback is folded into the validator.
- **Approval:** Diff table (entity, old → new). Single "Approve" button. Green badge.
- **On failure:** Automatic escalation to Tier 2.
- **Reference:** [tier1-reference.md](tier1-reference.md)

### Tier 2 — ORANGE (Operation Planner)

Multi-step or PSET-family operations. Plans are assembled deterministically from segments — `Tier2Planner` LLM is bypassed.

- **Trigger:** Multi-segment requests, PSET ops, custom-pset properties, or Tier 1 validation failure
- **Operations:** `ADD_PSET`, `REMOVE_PSET`, `SET_CLASSIFICATION`, `SET_MATERIAL`, plus chained `SET_PROPERTY`/`SET_ATTRIBUTE`
- **Validation:** JSON Schema on each step, filter resolution per step, param requirements per operation, inter-step consistency checks
- **Approval:** Full plan review panel with per-step entity counts and impact summary. Orange badge.
- **On failure:** Escalates to Tier 3.
- **Reference:** [tier2-reference.md](tier2-reference.md)

### Tier 3 — RED (Code Generation)

The LLM generates executable IfcOpenShell Python code, running in a sandboxed environment against a file copy.

- **Trigger:** CREATE / DELETE / RELATIONSHIP segments (handled directly by the deterministic router), or Tier 2 escalation
- **Code template:** `def modify_ifc(model): ... return {"summary": ..., "changes": [...]}`
- **Safety:** Seven-layer defence — forbidden pattern scan (×2), restricted globals, file copy isolation, timeout, return validation, Git snapshot, human code review
- **Approval:** Syntax-highlighted code display. Red "Execute" button. No pre-execution diff (code is the preview).
- **Reference:** [tier3-reference.md](tier3-reference.md)

### Out of Scope

Geometric modifications (move, resize, rotate, thicken, **author** physical elements) are excluded across all tiers. They surface as `OUT_OF_SCOPE` triage segments and short-circuit to Tier 0 rejection with a templated reminder.

---

## Request Lifecycle

```
User message
    │
    ├── [WebSocket] → ProposalConsumer._run_pipeline() [sync_to_async]
    │                       │
    │                       ▼
    │              ModificationService.propose(emitter=WebSocketEmitter)
    │              (phases streamed live to client)
    │
    └── [HTTP fallback] → ModifyView._handle_propose()
                                │
                                ▼
                       ModificationService.propose(emitter=NullEmitter)
```

Inside `propose()`:

```
    ▼
TriageClassifier.classify          → segments = [{kind, target_phrase, value_phrase}, ...]
    │
    ├── any segment is UNCLEAR ──► Tier 0 reject (HintGenerator: templated)
    ├── any segment is OUT_OF_SCOPE ──► Tier 0 reject (HintGenerator: templated)
    │
    ▼
SlotExtractor.extract              → slots filled per segment, kind-specific
    │
    ├── value missing / vague ──► SlotExtractionError ──► Tier 0 reject
    ├── pset_name not Pset_* ──► SlotExtractionError ──► Tier 0 reject
    │
    ▼
EntityNameResolver.resolve         → resolution per segment, mode-aware
    │
    ▼
tier_router.route(segments)        → deterministic, NO LLM
    │
    ├── 0 entities matched ──► Tier 0 reject (HintGenerator: registry-grounded fuzzy match)
    ├── property has no pset ──► Tier 0 reject (HintGenerator: registry-grounded "did you mean...")
    ├── tier=1 ──► _v2_dispatch_t1 ──► Tier1Validator ──► proposal
    ├── tier=2 ──► _v2_dispatch_t2 ──► assemble_tier2_intent ──► Tier2Validator ──► proposal
    └── tier=3 ──► _v2_dispatch_t3 ──► Tier3Planner ──► Tier3Reviewer ──► proposal
    │
    ▼
Guardian (RAV) check (non-blocking)
    │
    ▼
Proposal returned to user for approval
    │
    ├── Approve ──► Git snapshot ──► Execute ──► Git commit ──► Sync DB
    └── Reject ──► Proposal marked rejected
```

### Key Behaviors

- **Tier 0 rejection covers everything the router can't safely route:** vague request, geometry, missing slots, no entities matched, property with no pset. Rejections never crash the pipeline; the `HintGenerator` appends an actionable suggestion to the user-visible message.
- **Confidence threshold:** Proposals below 60% effective confidence (Tier 1/2) or 70% (Tier 3) are rejected with a "be more specific" message.
- **Auto-fallback (SET_PROPERTY → ADD_PROPERTY):** If Tier 1 validation fails because a standard pset property doesn't exist on entities yet, the validator auto-converts to ADD_PROPERTY and re-validates.
- **Multi-segment requests route to Tier 2 plan, not Tier 1 chain.** `ModificationProposal.message` is unique-per-row, so chained T1 proposals would IntegrityError. T2 plans use a single proposal with multiple steps; execution is atomic across steps.
- **SET_ATTRIBUTE guard:** SET_ATTRIBUTE operations on more than 10 entities route to Tier 2 instead of Tier 1, to avoid accidental mass renames.

---

## Service Architecture

### `ModificationService` — Orchestrator

Central service that coordinates the full propose → validate → execute → commit pipeline.

| Method | What it does |
|---|---|
| `propose()` | Triage → slots → resolve → route → dispatch ⇒ `ModificationProposal` |
| `execute()` | Git snapshot → run writer → Git commit → create `GitCommit` → sync DB |
| `reject()` | Mark proposal as rejected with optional reason |
| `restore_version()` | Revert to any historical commit, re-parse IFC into DB |

### Pipeline stages

| Service | File | Role |
|---|---|---|
| `TriageClassifier` | `writeback/services/triage_classifier.py` | Stage 1: segment the request, classify each segment by `kind` |
| `SlotExtractor` | `writeback/services/slot_extractor.py` | Stage 2: per-kind narrow prompts fill slot dicts; groundedness + value-shape guards |
| `EntityNameResolver` | `writeback/services/entity_resolver.py` | Stage 3: locate target entities, mode-aware (EXISTING / PARENT / NEW / NO target) |
| `tier_router.route` | `writeback/services/tier_router.py` | Stage 3.5: deterministic policy table; no LLM. Picks the initial tier |
| `intent_assembler` | `writeback/services/intent_assembler.py` | Builds the intent dict shape the writers consume |
| `HintGenerator` | `writeback/services/hint_generator.py` | Composes user-visible hints on Tier 0 rejection. Three strategies: Templated → Registry-grounded → LLM-fallback (gated, see [pipeline-architecture.md](../specs/writeback/pipeline-architecture.md)) |

### `FilterEngine` — Entity Resolution

Resolves filter specs to Django QuerySets. All filters AND together.

| Filter | Matching | DB lookup |
|---|---|---|
| `ifc_type` | Case-insensitive startswith | `ifc_type__istartswith` |
| `storey` | Case-insensitive contains | `building_storey__icontains` |
| `name_pattern` | Glob (`*`) → regex | `name__iregex` |
| `global_ids` | Exact list match | `global_id__in` |
| `property_match` | Exact JSON value | `properties__contains` |

Raises `ValueError` on empty filter or zero matches.

### `IFC Standard Psets Registry`

A static registry (~120 standard property sets) mapping pset → property → type info. Used for:

- **Type coercion:** Automatically converts slot output to correct types (string "true" → bool `true`, string "0.25" → float `0.25`, enum validation)
- **Standard pset detection:** Enables auto-creation of missing standard psets in ADD_PROPERTY
- **Pset inference:** `tier_router._maybe_infer_pset` fills `Pset_<Type>Common` from the registry when the user named a property without naming a pset
- **Hint generation:** `HintGenerator` Strategy 2 fuzzy-matches user-typed property names against the registry to surface "did you mean ..." suggestions

### `PipelineEmitter` — Progress Streaming Protocol

| Component | Role |
|-----------|------|
| `PipelineEmitter` | Protocol interface: `emit(phase, status, message, detail=None)` |
| `NullEmitter` | Silent; default when no WebSocket is available |
| `WebSocketEmitter` | Bridges emitter → `async_to_sync(send_json)` on the consumer |
| `CapturingEmitter` | Stores events in list; used in tests |

File: `writeback/services/emitters.py`

### Writers

- **`Tier1Writer`:** Operates on a single IFC file via IfcOpenShell API. Uses transactions (`begin_transaction` / `undo` on error). Handles SET/ADD/REMOVE_PROPERTY and SET_ATTRIBUTE.
- **`Tier2Writer`:** Wraps `Tier1Writer` for basic ops, adds `add_pset`, `remove_pset`, `set_classification`, `set_material`. Uses find-or-create patterns for classifications and materials.
- **`Tier3Reviewer`:** LLM code review step in `tier3_reviewer.py`; evaluates safety and correctness of generated Tier 3 code before the proposal is created. Review output stored in `proposal.review`.

---

## Retrieval-Augmented Verification (Guardian / RAV)

Before any modification proposal is presented for approval, the Guardian cross-references the proposed change against the project's document corpus.

- Builds a semantic search query from the intent (entity type, property, value)
- Retrieves relevant document chunks via pgvector cosine distance (threshold: 0.45)
- LLM evaluates: **CONFIRMED**, **CONFLICT**, or **NO_INFO**
- Verdict displayed alongside the approval interface

**Guardian advises — it never blocks.** The check runs in a try/except; failures are logged as warnings and don't prevent proposal creation.

→ **[Full documentation](guardian.md)**

---

## Git Integration

- A Git repository is initialized per project. IFC files are tracked as files.
- **Before modification:** automatic snapshot commit.
- **After approved modification:** commit with semantic metadata (tier label, affected count, author).
- Semantic diff stored in `GitCommit.diff_data` JSON (tier, operation, affected entities, changes).
- **Version restore:** Revert to any historical commit, then re-run the full IFC processing pipeline to sync the database.

---

## Implementation Notes

- **LLM model:** User-selectable via Settings page (persisted in `UserLLMConfig`). Resolved at runtime by `core.llm.get_llm(user)`. All services — RAG, pipeline stages, tier planners, reviewers — use this factory. The curated model registry (`core/model_registry.py`) provides VRAM estimates and metadata for the UI. Default fallback: `settings.OLLAMA_MODEL` from `.env`.
- **Always validate** stage output before executing — Triage, SlotExtractor, Tier1Validator, Tier2Validator, Tier3Reviewer form a layered safety net; small-model drift in any one stage degrades gracefully into a localised rejection.
- **Request-scoped:** The modification service is instantiated per-request inside either the async WebSocket consumer (primary path, wrapped in `sync_to_async`) or the Django view (HTTP fallback). Both paths are functionally equivalent; the emitter parameter controls whether progress is streamed.
- **DB sync:** After execution, entity properties and names are synced back to the Django ORM so queries reflect the latest state.
- **`linked_conflict_ids` flow:** When a user clicks "Fix in Modify" on a conflict card, the modify tab receives `?conflict_ids=<uuid>,<uuid>` URL params and pre-fills the prompt with the suggested fix. The proposal stores `linked_conflict_ids` (JSONField). On approval, `_handle_approve()` bulk-sets all linked conflicts to `status=RESOLVED`.
- **Failure observability:** Every caught exception in `propose()` / `execute()` produces a `FailureRecord` (see [docs/metacastor/d3-failure-memory.md](../metacastor/d3-failure-memory.md)) that the chat UI surfaces as a help card with retry context.
