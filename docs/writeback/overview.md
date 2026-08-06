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
   │                                 generic-name guards fire per-segment.
   │                                 PROPERTY carries `operation` (SET |
   │                                 REMOVE); RELATIONSHIP carries a
   │                                 `destination_phrase`
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
intent_assembler                   — builds the intent dict the validators
   │                                 consume
   ▼
T1 / T2 / T3 dispatchers           — validate, then build one MutationJournal
   ▼
MutationJournal                    — the single write IR for every tier
   ▼
render_diff(journal)               — the preview the user approves
   ▼
       ── human approval ──
   ▼
JournalExecutor.apply()            — temp copy → replay → atomic os.replace
   ▼
git commit  →  DB index sync
```

Every tier ends at the same artifact. See [The MutationJournal](#the-mutationjournal) below, and
[pipeline-architecture.md](../specs/writeback/pipeline-architecture.md) for the full design
rationale and the deterministic policy table.

## Three-Tier Escalation

### Tier 1 — GREEN (Certified Operations)

The LLM stages emit structured slots. Pre-coded, tested handler functions execute the changes.

- **Operations the router emits:** `SET_PROPERTY`, `REMOVE_PROPERTY`, `SET_ATTRIBUTE`. `ADD_PROPERTY` exists in the writer but is never routed to directly — `SET_PROPERTY` is an upsert on standard psets, and the validator converts it when the property is absent.
- **Validation:** Target pset exists, property exists (SET/REMOVE) or doesn't (ADD), type compatible, filter matches ≥1 entity. `SET_ATTRIBUTE` additionally checks the attribute is declared on the matched entities' IFC class, not merely safe-listed.
- **Approval:** Diff table (entity, old → new). Single "Approve" button. Green badge.
- **On failure:** Automatic escalation to Tier 2.
- **Reference:** [tier1-reference.md](tier1-reference.md)

### Tier 2 — ORANGE (Operation Planner)

Multi-step or PSET-family operations. Plans are assembled deterministically from segments — there is no plan-generating LLM.

- **Trigger:** Multi-segment requests, PSET ops, custom-pset properties, or Tier 1 validation failure
- **Operations:** `ADD_PSET`, `REMOVE_PSET`, `SET_CLASSIFICATION`, `SET_MATERIAL`, plus chained `SET_PROPERTY`/`SET_ATTRIBUTE`
- **Validation:** JSON Schema on each step, filter resolution per step, param requirements per operation, inter-step consistency checks
- **Approval:** Full plan review panel with per-step entity counts and impact summary. Orange badge.
- **On failure:** Escalates to Tier 3.
- **Reference:** [tier2-reference.md](tier2-reference.md)

### Tier 3 — RED (Entity Lifecycle)

Tier 3 tries **typed operations first** and generates code only when it cannot express the request. The LLM chooses *which* pre-coded operation to run rather than authoring the IfcOpenShell calls.

- **Trigger:** CREATE / DELETE / RELATIONSHIP segments (handled directly by the deterministic router), or Tier 2 escalation
- **Typed ops:** `CREATE_ENTITY`, `DELETE_ENTITY`, `ASSIGN_RELATIONSHIP` — schema-validated, and grounded against the DB so a hallucinated GlobalId cannot survive to execution
- **Approval (typed):** an ordinary diff preview and a normal Approve button — there is no code to review
- **Code fallback:** when the planner reports `cannot_express`, the reason is shown to the user and generated Python is produced instead. Seven-layer defence — forbidden pattern scan (×2), restricted globals, file copy isolation, a real subprocess timeout, return validation, Git snapshot, human code review
- **Approval (code):** syntax-highlighted code plus a mandatory review acknowledgement. The gate follows the *presence of code*, not the tier
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
    ├── move with no resolvable destination ──► Tier 0 reject (hint lists the model's storeys)
    ├── tier=1 ──► _dispatch_t1 ──► Tier1Validator ──► build_t1 ──► journal
    ├── tier=2 ──► _dispatch_t2 ──► assemble_tier2_intent ──► Tier2Validator ──► build_t2 ──► journal
    └── tier=3 ──► _dispatch_t3 ──► T3OpPlanner ──► build_t3 ──► journal
                       └── cannot_express ──► Tier3Planner ──► Tier3Reviewer ──► build_t3_code
    │
    ▼
render_diff(journal) ──► proposal (changes = journal, diff_preview = rows)
    │
    ▼
Guardian (RAV) check (non-blocking)
    │
    ▼
Proposal returned to user for approval
    │
    ├── Approve ──► Git snapshot ──► JournalExecutor.apply() ──► Git commit ──► Sync DB
    └── Reject ──► Proposal marked rejected
```

### Key Behaviors

- **Tier 0 rejection covers everything the router can't safely route:** vague request, geometry, missing slots, no entities matched, property with no pset. Rejections never crash the pipeline; the `HintGenerator` appends an actionable suggestion to the user-visible message.
- **Confidence threshold:** Proposals below 60% effective confidence (Tier 1/2) or 70% (Tier 3) are rejected with a "be more specific" message.
- **Auto-fallback (SET_PROPERTY → ADD_PROPERTY):** If Tier 1 validation fails because a standard pset property doesn't exist on entities yet, the validator auto-converts to ADD_PROPERTY and re-validates.
- **Multi-segment requests route to Tier 2 plan, not Tier 1 chain.** `ModificationProposal.message` is unique-per-row, so chained T1 proposals would IntegrityError. T2 plans use a single proposal with multiple steps; execution is atomic across steps.
- **SET_ATTRIBUTE guard:** SET_ATTRIBUTE operations on more than 10 entities route to Tier 2 instead of Tier 1, to avoid accidental mass renames.

---

## The MutationJournal

Every tier converges on one artifact. A `MutationJournal` is an immutable, serializable list of typed mutations pinned to a SHA-256 fingerprint of the file it targets, stored on `ModificationProposal.changes`.

This is what makes the preview and the write the same thing: `render_diff()` renders the journal, the user approves *that* journal, and `JournalExecutor` replays exactly it. There is no second code path that reinterprets the intent at execution time.

**Corruption resistance is structural, not procedural:**

- **Deferred bake.** Mutations apply to a temp copy created beside the original; only a fully successful run ends in an atomic `os.replace`. Any failure — a bad GlobalId, a rejected value, a crash — leaves the original byte-identical, with no partial write to unwind.
- **Fingerprint pinning.** If the file changed after the proposal was built, the fingerprint no longer matches and the journal is refused rather than applied to a model it was never validated against. Re-proposing is the fix, and the failure is classified `STALE_JOURNAL` / retryable.
- **Closed op set.** Twelve typed operations. `RUN_CODE` is the escape hatch and the only one that carries arbitrary Python — which is why the code-review gate follows the presence of code rather than the tier.
- **Old values re-read at apply time.** The journal carries a DB snapshot of each old value; the executor compares it against the model and flags drift, so a stale preview is visible rather than silent.

After a successful run the executor returns what it actually did, which drives the DB index sync — including entity creation and deletion, where the GlobalId is only known once IfcOpenShell mints it.

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
| `intent_assembler` | `writeback/services/intent_assembler.py` | Builds the intent dict shape the validators consume |
| `HintGenerator` | `writeback/services/hint_generator.py` | Composes user-visible hints on Tier 0 rejection. Three strategies: Templated → Registry-grounded → LLM-fallback (gated, see [pipeline-architecture.md](../specs/writeback/pipeline-architecture.md)) |
| `JournalBuilder` | `writeback/services/journal_builder.py` | Turns a validated T1 intent / T2 plan / T3 op list into one `MutationJournal`; snapshots old values and pins the file fingerprint |
| `JournalExecutor` | `ifc_processor/services/journal_executor.py` | Replays a journal onto a temp copy and atomically swaps it over the original |
| `render_diff` | `writeback/services/diff_renderer.py` | Renders a journal as the UI diff rows — the preview and the write share one source |

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

All three live in `ifc_processor/services/` — they are pure IfcOpenShell libraries with no LLM or Django coupling, so the FM export path can drive the same code.

- **`Tier1Writer`:** Operates on a single IFC file via IfcOpenShell API. Uses transactions (`begin_transaction` / `undo` on error). Handles SET/ADD/REMOVE_PROPERTY and SET_ATTRIBUTE.
- **`Tier2Writer`:** Wraps `Tier1Writer` for basic ops, adds `add_pset`, `remove_pset`, `set_classification`, `set_material`. Uses find-or-create patterns for classifications and materials.
- **`Tier3Writer`:** Extends `Tier2Writer` with `create_entity`, `delete_entity`, `assign_container`. Delete dispatches on class (an `IfcZone` is an `IfcGroup`, not an `IfcProduct`), and both delete and move assert their post-condition by re-reading the model — several IfcOpenShell APIs return `None` on a no-op rather than raising.
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

- **LLM model:** User-selectable via Settings page (persisted in `UserLLMConfig`). Resolved at runtime by `core.llm.get_llm(user)`. All services — RAG, pipeline stages, tier planners, reviewers — use this factory. The curated model registry (`core/llm_model_registry.py`) provides VRAM estimates and metadata for the UI. Default fallback: `settings.OLLAMA_MODEL` from `.env`.
- **Always validate** stage output before executing — Triage, SlotExtractor, Tier1Validator, Tier2Validator, Tier3Reviewer form a layered safety net; small-model drift in any one stage degrades gracefully into a localised rejection.
- **Request-scoped:** The modification service is instantiated per-request inside either the async WebSocket consumer (primary path, wrapped in `sync_to_async`) or the Django view (HTTP fallback). Both paths are functionally equivalent; the emitter parameter controls whether progress is streamed.
- **DB sync:** After execution, entity properties and names are synced back to the Django ORM so queries reflect the latest state.
- **`linked_conflict_ids` flow:** When a user clicks "Fix in Modify" on a conflict card, the modify tab receives `?conflict_ids=<uuid>,<uuid>` URL params and pre-fills the prompt with the suggested fix. The proposal stores `linked_conflict_ids` (JSONField). On approval, `_handle_approve()` bulk-sets all linked conflicts to `status=RESOLVED`.
- **Failure observability:** Every caught exception in `propose()` / `execute()` produces a `FailureRecord` (see [docs/metacastor/d3-failure-memory.md](../metacastor/d3-failure-memory.md)) that the chat UI surfaces as a help card with retry context.
