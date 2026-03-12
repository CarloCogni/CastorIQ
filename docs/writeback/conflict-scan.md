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
2. **Build entity–chunk map** — for each requirement chunk, vector-search top-K IFC entities nearby in embedding space
3. **LLM compare** — one call per entity that matched at least one requirement chunk
4. **Persist** — upsert `Conflict` records using `content_hash` deduplication

**Why it's better:** Only scans entities that are actually referenced in specification requirements. Entities with no relevant requirements are never sent to the LLM.

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

## Confidence Threshold

`CONFIDENCE_THRESHOLD = 0.7` in `conflict_scan_service.py`.

Rationale: scores below 0.7 indicate the LLM is uncertain whether a genuine contradiction exists. At that confidence level, the risk of a false positive outweighs the benefit of surfacing the finding. Adjust this constant if the LLM used in a deployment tends to be systematically under- or over-confident.
