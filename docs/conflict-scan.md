# Conflict Scan Subsystem

## Overview

The conflict scan detects contradictions between IFC model properties and project document requirements. It uses a **document-first strategy** and streams live progress via WebSocket — the same infrastructure as the Modify tab.

---

## Design Rationale: Document-First Strategy

### Old approach (entity-first, discarded)
- Loop over ALL IFC entities → vector-search doc chunks for each
- Problem: hundreds of entities scanned; most have no relevant requirements in the documents

### New approach (document-first)
1. **Gather requirement chunks** — filter `DocumentChunk` records that contain AEC compliance keywords (`shall`, `must`, `required`, `fire rating`, `EI`, `REI`, etc.)
2. **Build entity–chunk map** — for each requirement chunk, three passes whose results are unioned per entity (see below)
3. **LLM compare** — one call per entity that matched at least one requirement chunk
4. **Verify** — the current value a finding claims is replaced by the value the index holds before anything is stored (see below)
5. **Persist** — upsert `Conflict` records using `content_hash` deduplication

**Why it's better:** Only scans entities that are actually referenced in specification requirements. Entities with no relevant requirements are never sent to the LLM.

### Entity–chunk mapping: lookups first, embeddings last

The first measured run (`docs/evaluation/2026-08-30-rav-benchmark.md`) showed that
per-chunk embedding top-K is a lottery among near-identical entities: of three
identical external walls two were reached and one was not, and no thermal-spec
chunk reached any wall or window at all, so whole conflict cases were
structurally invisible to the model. The mapping is therefore built in three
passes per requirement chunk, in this order, and an entity keeps every chunk any
pass gave it:

| Pass | What it matches | Why |
|---|---|---|
| **reference** | the chunk quotes an entity's model reference (`Pset_*.Reference`, e.g. `Wall-Ext_102Bwk-75Ins-100LBlk-12P`) or a `:`-separated segment of its name, whole-word, case-insensitive. A string qualifies only if it looks like a type code (a digit, `_` or `-`) or is a multi-word name; `Glazed` or `Floor` would match prose | specifications cite elements by their type mark; this is an exact lookup, not retrieval |
| **label** | the chunk names an element class (`wall(s)`, `partition(s)`, `door(s)`, `window(s)`, `slab(s)`, `floor`, `roof`, …) **and** a property (`U-value`, `fire resistance`, `R'w`, …); every entity of that class is added, nearest by embedding first, capped at `ENTITY_TYPE_QUOTA` per class | "external walls shall …" applies to all of them, not to whichever five embed closest |
| **embedding** | top-`ENTITY_TOP_K` entities within `ENTITY_RELEVANCE_THRESHOLD` cosine distance | the original pass; catches what the two lookups have no vocabulary for |

The label table (`ENTITY_FIRST_LABELS`) is deliberately narrower than the
post-LLM type gate: "wall" does not pull in curtain walls, a requirement that
means them says "curtain wall". Both lookup passes are switched off together by
`entity_first=False` (benchmark: `--no-entity-first`), which restores the
embedding-only behaviour for ablation. `full_scan()` reports how many
(entity, chunk) pairs each pass contributed under `stats["retrieval"]`.

### Value verification

The model reads the entity's properties from the prompt and still invents
current values (`EI60` on a wall that carries no `FireRating`). Before a finding
is stored, `_verify_ifc_value` looks the claimed property up in the entity's
indexed properties through the shared alias table
(`writeback/services/property_aliases.py`, the same table the RAV benchmark
scores with):

- property present → the stored `ifc_value` is the index's value, whatever the model claimed;
- property absent → the stored `ifc_value` is `(not set)`; a claimed value on an absent property is counted as `values_unset` in the scan stats, a corrected one as `values_corrected`.

The equal-value guard then compares the verified value with the document's:
exact after squashing, numerically equal at the document's own precision
(`0.35 as designed` describes `0.350998`), or a boolean the wording asserts
(`non-load-bearing` describes `False`). Off with `verify_values=False`
(`--no-verify-values`).

### Finding attribution

The model names the excerpt a finding came from by `source_chunk_index`, and is
sometimes wrong (a fire-rating conflict cited to the thermal spec). Each finding
is filed against the chunk that best quotes its `document_value` and names its
property; the model's choice stands on a tie.

---

## False-Positive Protection

The original scan prompt had a critical flaw: it would flag `FireRating: EI120` in the IFC as a conflict with a document requiring `EI120` — a no-op "fix".

The new prompt uses a **mandatory two-step process**:

1. **EXTRACT** — identify every specific requirement (property + value) from the document excerpts
2. **COMPARE** — for each extracted requirement, compare against the IFC entity's current value:
   - Values match or are equivalent → **NOT a conflict**
   - Values differ → **flag as conflict**
   - Property absent in IFC → **NOT a conflict** (missing data ≠ contradiction)
   - Requirement ambiguous → **NOT a conflict**

Additionally, each finding includes a `confidence` score (0.0–1.0). Only findings with `confidence >= 0.7` are stored.

---

## Scanner Configuration Constants

All constants live in `writeback/services/conflict_scan_service.py`:

| Constant | Value | Meaning |
|----------|-------|---------|
| `CONFIDENCE_THRESHOLD` | `0.7` | Minimum confidence to persist a finding |
| `ENTITY_RELEVANCE_THRESHOLD` | `0.45` | Cosine distance cutoff for the embedding pass |
| `ENTITY_TOP_K` | `5` | Max entities per requirement chunk, embedding pass |
| `ENTITY_TYPE_QUOTA` | `25` | Max entities per element class per chunk, label pass |
| `MIN_REFERENCE_LENGTH` / `MIN_PHRASE_LENGTH` | `6` / `10` | What counts as a reference string in the reference pass |

All of these (plus the element-type gate, the requirement-keyword filter, the entity-first passes and value verification) are overridable per-instance via keyword-only constructor arguments — production callers use the defaults; `manage.py benchmark_rav --ablate` switches each mitigation off in turn to measure what it buys (see `docs/benchmarks.md` §Harness D).

Adjust these constants if the deployed LLM is systematically over- or under-confident, or if the entity-to-requirement matching produces too many or too few pairs.

`LOW_VALUE_IFC_TYPES` lists entity types excluded when `skip_low_value=true`: `IfcSpace`, `IfcProject`, `IfcBuilding`, `IfcSite`, `IfcBuildingStorey`, `IfcSystem`, `IfcZone`, and `IfcRelXxx` subtypes. These types rarely carry property values that can conflict with document requirements.

---

## Scan Lifecycle

Each scan creates a `ScanRun` record:

| Field | Purpose |
|-------|---------|
| `project` | Which project was scanned |
| `triggered_by` | User who started the scan |
| `scan_type` | `full` / `targeted_doc` / `targeted_ifc` / `post_modify` |
| `status` | `pending` → `running` → `completed` / `failed` |
| `entities_scanned` | How many IFC entities were compared |
| `conflicts_found` | New conflict records created |
| `llm_model_used` | Model name for audit |
| `error_message` | Populated on failure |

The `ConflictsView` passes `last_scan_run` (most recent completed `ScanRun`) to the template instead of the old `last_scan_at` hack.

---

## Conflict Deduplication

Each `Conflict` record has a `content_hash`:

```
content_hash = SHA-256(f"{entity.id}:{chunk.id}:{property_name}")
```

Upsert logic on re-scan:
- **DISMISSED** → skip (never recreate a dismissed conflict)
- **OPEN** → update in place (title, description, values, confidence)
- **RESOLVED / not found** → create fresh record

The `UniqueConstraint(fields=["content_hash"], condition=Q(status="open"))` prevents duplicate open conflicts across scans.

---

## WebSocket Protocol

**Route:** `ws/projects/<project_id>/conflicts/scan/`
**Consumer:** `ScanConsumer` in `writeback/consumers.py`

```
Client → Server:
  {"action": "start_scan", "skip_low_value": true}

Server → Client (streaming):
  {"type": "phase", "phase": "init",         "status": "running", "message": "Starting…"}
  {"type": "phase", "phase": "requirements", "status": "done",    "message": "Found 23 sections", "detail": {"count": 23}}
  {"type": "phase", "phase": "matching",     "status": "running", "message": "Finding entities…"}
  {"type": "phase", "phase": "matching",     "status": "done",    "message": "38 pairs to compare", "detail": {"pairs": 38}}
  {"type": "phase", "phase": "compare",      "status": "running", "message": "Comparing 1/38…", "detail": {"current": 1, "total": 38}}
  ...
  {"type": "scan_complete", "stats": {"entities_scanned": 38, "conflicts_found": 2, "conflicts_updated": 0}}
  {"type": "done"}

On error:
  {"type": "error", "message": "..."}
  {"type": "done"}
```

The frontend `ScanEngine` in `_conflicts.html` opens the WebSocket on button click, renders phase steps in real time, and reloads the page on completion.

---

## Integration Points

- **`ConflictScanService.full_scan(emitter)`** — accepts any `PipelineEmitter` (same protocol as `ModificationService.propose()`)
- **`NullEmitter`** — used when no WebSocket is available (e.g. management command)
- **`CapturingEmitter`** — used in tests to assert phase events

Future: post-modify re-scan, triggered automatically after a modification is applied using `ScanType.POST_MODIFY`.

---

## Conflict Management

### Status Lifecycle

`OPEN` → `RESOLVED` (via "Fix in Modify" approval) | `IGNORED` (manual) | `DISMISSED` (manual, permanent — never recreated by scan)

### Bulk Actions

HTTP views in `writeback/views.py` handle batch operations:

| View | Action |
|------|--------|
| `BulkDismissView` | Permanently dismiss; skipped on future scans |
| `BulkIgnoreView` | Mark as ignored (non-permanent) |
| `BulkResolveView` | Mark as manually resolved |
| `DeleteAllConflictsView` | Hard-delete all conflicts for a project |

All bulk views accept `conflict_ids=all` (select all) or a comma-separated list of UUIDs.

### "Fix in Modify" Auto-Resolve

Clicking "Fix in Modify" opens the Modify tab with `?conflict_ids=<uuid>,...` in the URL, pre-filling the prompt with the conflict's `suggested_fix`. On proposal approval, `_handle_approve()` bulk-sets all `linked_conflict_ids` to `RESOLVED`.

### UI Grouping

`ConflictsView` groups conflicts by `(title, ifc_value, document_value)` — the same contradiction affecting multiple entities appears as a single grouped card with an entity count badge.

---

## Confidence Threshold

`CONFIDENCE_THRESHOLD = 0.7` — see Scanner Configuration Constants above for rationale and tuning guidance.
