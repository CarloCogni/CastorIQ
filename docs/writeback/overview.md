# Write-Back System

The write-back system is Castor's core innovation: the ability to modify IFC files through natural language with human approval and full traceability.

## Core Principle: Risk-Stratified Autonomous Action (RSAA)

Always attempt the safest execution tier first. Escalate only when the current tier cannot handle the request. The LLM never exercises more power than the task requires (**Minimal Authority**).

## Three-Tier Escalation

### Tier 1 — GREEN (Certified Operations)

The LLM performs intent classification and parameter extraction only. It never touches IfcOpenShell directly. Instead, it emits a structured JSON payload that is executed by pre-coded, tested handler functions.

- **Operations:** `SET_PROPERTY`, `ADD_PROPERTY`, `REMOVE_PROPERTY`, `SET_ATTRIBUTE`
- **LLM output:** `{ tier: 1, operation, filter, pset, property, new_value, explanation }`
- **Validation:** Target pset exists, property exists (SET/REMOVE) or doesn't (ADD), type compatible, filter matches ≥1 entity
- **Approval:** Diff table (entity, old → new). Single "Approve" button. Green badge.
- **On failure:** Automatic escalation to Tier 2.
- **Reference:** [tier1-reference.md](tier1-reference.md)

### Tier 2 — ORANGE (Operation Planner)

The LLM generates a structured operation plan — an ordered sequence of validated operations, each conforming to a JSON Schema. **Fully implemented.**

- **Trigger:** Tier 1 cannot handle the request (multi-step, compound operations, or Tier 1 validation failure)
- **Extra operations:** `ADD_PSET`, `REMOVE_PSET`, `SET_CLASSIFICATION`, `SET_MATERIAL`, `COPY_PROPERTIES`
- **LLM output:** `{ tier: 2, plan: [{step, op, filter, params, ...}, ...], explanation }`
- **Validation:** JSON Schema on each step, filter resolution per step, param requirements per operation, inter-step consistency checks (no contradictions)
- **Approval:** Full plan review panel with per-step entity counts and impact summary. Orange badge.
- **On failure:** Escalates to Tier 3.
- **Reference:** [tier2-reference.md](tier2-reference.md)

### Tier 3 — RED (Code Generation)

The LLM generates executable IfcOpenShell Python code, running in a sandboxed environment against a file copy. **Fully implemented.**

- **Trigger:** Tiers 1 and 2 both failed, or the classifier directly identifies entity creation/deletion/spatial/relationship operations.
- **Code template:** `def modify_ifc(model): ... return {"summary": ..., "changes": [...]}`
- **Safety:** Seven-layer defence — forbidden pattern scan (×2), restricted globals, file copy isolation, timeout, return validation, Git snapshot, human code review.
- **Approval:** Syntax-highlighted code display. Red "Execute" button. No pre-execution diff (code is the preview).
- **Reference:** [tier3-reference.md](tier3-reference.md)

### Out of Scope

Geometric modifications (e.g., "make the wall 20cm thicker") are excluded across all tiers.

---

## Request Lifecycle

```
User message
    │
    ▼
Message Normalizer (aliases → canonical names)
    │
    ▼
Intent Classifier (LLM → JSON intent or intent array)
    │
    ├── Single intent, tier=1 ──► Tier 1 Validator ──► Proposal
    ├── Intent array (chain) ──► Validate each ──► Chained Proposals
    ├── Single intent, tier=2 ──► Tier 2 Planner + Validator ──► Proposal
    └── Tier 1 validation fails ──► Auto-escalate to Tier 2
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

- **Confidence threshold:** Proposals below 60% confidence are rejected with a "be more specific" message.
- **Auto-fallback (SET_PROPERTY → ADD_PROPERTY):** If Tier 1 validation fails because a standard pset property doesn't exist on entities yet, the system automatically converts the operation to ADD_PROPERTY and re-validates.
- **Chain intents:** The classifier can return a JSON array of Tier 1 intents for requests like "Set fire rating to EI120 and IsExternal to true for all walls". Each becomes a separate proposal linked by a chain ID.
- **Tier 2 escalation:** If any chain element fails Tier 1 validation, the entire request is re-routed to the Tier 2 planner.
- **SET_ATTRIBUTE guard:** SET_ATTRIBUTE operations are capped at 10 entities to prevent accidental mass renames.

---

## Service Architecture

### `ModificationService` — Orchestrator

Central service that coordinates the full propose → validate → execute → commit pipeline.

| Method | What it does |
|---|---|
| `propose()` | Classify intent → validate → create `ModificationProposal` → Guardian check |
| `execute()` | Git snapshot → run writer → Git commit → create `GitCommit` → sync DB |
| `reject()` | Mark proposal as rejected with optional reason |
| `restore_version()` | Revert to any historical commit, re-parse IFC into DB |

### `IntentClassifier` — LLM Intent Extraction

Parses natural language into structured JSON. Returns either a single intent dict or a list (chain).

- Uses `get_llm(user, format_json=True, temperature=0.1)` — resolves the user's preferred Ollama model at runtime
- Builds entity context from the project's IFC entities (types, psets, sample properties, applicable standard psets)
- Normalizes confidence from 0.0–1.0 to 0–100 scale

### `MessageNormalizer` — Input Preprocessing

Translates common aliases before the LLM sees the message:

- **Property aliases:** "fire rating" → `FireRating`, "u-value" → `ThermalTransmittance`, "load bearing" → `IsLoadBearing`
- **Entity aliases:** "ext wall" → "external wall", "col" → "column"
- **Value aliases:** "yes" → "true", "no" → "false"

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

- **Type coercion:** Automatically converts LLM output to correct types (string "true" → bool `true`, string "0.25" → float `0.25`, enum validation)
- **Standard pset detection:** Enables auto-creation of missing standard psets in ADD_PROPERTY
- **Applicable pset hints:** Provides the classifier with available standard psets per entity type

### Writers

- **`Tier1Writer`:** Operates on a single IFC file via IfcOpenShell API. Uses transactions (`begin_transaction` / `undo` on error). Handles SET/ADD/REMOVE_PROPERTY and SET_ATTRIBUTE.
- **`Tier2Writer`:** Wraps `Tier1Writer` for basic ops, adds `add_pset`, `remove_pset`, `set_classification`, `set_material`, `copy_properties`. Uses find-or-create patterns for classifications and materials.

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
- **Rollback:** `git checkout <hash> -- file.ifc`, then copy back to media storage. ##### IS IT USED? DON'T THINK SO! 
- **Version restore:** Revert to any historical commit, then re-run the full IFC processing pipeline to sync the database.

---

## Implementation Notes

- **LLM model:** User-selectable via Settings page (persisted in `UserLLMConfig`). Resolved at runtime by `core.llm.get_llm(user)`. All services — RAG, intent classifier, tier planners, reviewers — use this factory. The curated model registry (`core/model_registry.py`) provides VRAM estimates and metadata for the UI. Default fallback: `settings.OLLAMA_MODEL` from `.env`.
- **Docstring quality** is the #1 factor for classifier reliability (system prompt engineering).
- **Always validate** LLM output before executing — the classifier, validators, and coercion layer form a three-stage safety net.
- **Request-scoped:** The modification service is instantiated per-request inside Django views, not as a long-running process.
- **DB sync:** After execution, entity properties and names are synced back to the Django ORM so queries reflect the latest state.
