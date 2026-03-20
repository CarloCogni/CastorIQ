# SPEC-001: Unified File Upload — Castor

**Status:** Approved — Implementing Option A  
**Date:** 2026-03-20  
**Author:** Castor Dev  

## 0. Implementation Prompt

"""
You are a senior Django engineer doing a code review before implementing a feature.
Attached is "docs/unified-file-upload-spec.md". Read it, but do NOT treat it as ground
truth — it was written without full codebase visibility and may contain wrong
assumptions about file paths, model fields, URL names, or existing logic.

BEFORE writing a single line of code:

1. AUDIT THE CODEBASE
   - Find every view, URL, template, and service related to file upload
   - Find every reference to UploadIFCView, FileUploadView, upload_ifc, and any
     upload-related URL name in urls.py and in templates ({% url %} tags)
   - Find the exact field names on IFCFile and Document models — especially
     entity_count, chunk_count, error_message, status
   - Find where ProjectAccessMixin lives and what get_project() returns
   - Check if Redis is already in the stack (settings, requirements, docker-compose)

2. CHALLENGE THE SPEC
   - List every assumption in the spec that you cannot verify from the code
   - Flag anything that contradicts what you find in the codebase
   - If the spec says "remove UploadIFCView", confirm there are zero callers first —
     templates, JS, other views, API clients — before recommending deletion
   - If chunk_count or entity_count don't exist as model fields, say so

3. APPLY THESE ENGINEERING STANDARDS (non-negotiable)
   - Clean Code (Uncle Bob) + Zen of Python + DRY
   - Negative Space Programming: guard clauses over nesting, design by omission
   - Views and Forms are dumb — business logic stays in services/
   - Type hints on all signatures and return types
   - Docstrings on every module, class, and public method
   - File header comment: # app/path/to/file.py
   - Logging only (never print), all log messages in English
   - select_related / prefetch_related on every queryset
   - No boilerplate — use context managers and base classes

4. PRESENT A REVISED PLAN before writing code
   - Correct any spec errors you found
   - List exactly which files you will touch and why
   - Ask for confirmation if you need to touch more than 3 files beyond the spec

5. THEN IMPLEMENT — in focused chunks, not full files
   - For modifications: show exact location with 3–5 lines of surrounding context
   - For HTML: reference surrounding elements precisely, never "add this somewhere"
   - The JS queue logic must be sequential (one XHR at a time) — do NOT send files
     in parallel, the pipeline is CPU-bound and synchronous
   - The upload response JSON contract must match exactly what the spec defines in
     Section 4 — if the current view returns different keys, fix the view too
   - The drop zone must remain active while the queue is processing
   - The summary section must only appear after the queue fully drains

6. FUTURE-PROOFING NOTE (do not implement, just keep in mind)
   - The correct production path is: POST → 202 + task_id → Celery worker →
     Django Channels WebSocket fan-out per task_id
   - Do not architect the current solution in a way that makes this migration harder
   - Specifically: keep run_pipeline() and process() calls isolated in one place so
     they can be swapped for .delay() calls later

Strip all filler and validation from your responses. If my thinking is wrong, say so
directly. Show me the audit results before the plan, and the plan before any code.
"""

---

## 1. Context

Castor currently exposes two separate upload surfaces: one for IFC models and one
for PDF/DOCX documents. Each is a distinct page with a distinct URL. Upload is
single-file only. After upload the user is redirected away with no per-file
feedback during processing.

This spec covers the redesign into a single, multi-file upload view with in-page
processing feedback and a final result summary.

---

## 2. Goals

- One upload URL, one view, one template for all file types
- Multiple files selectable/droppable in a single interaction
- Automatic routing to the correct processor by file extension
  (`.ifc` → `IFCProcessingService`, `.pdf/.docx/.txt` → `DocumentProcessor`)
- Per-file real-time status feedback (queued → uploading → processing → result)
- In-page summary on completion — no redirect
- Return-to-project CTA visible at all times; prominent after batch completes
- No new infrastructure required for this iteration

---

## 3. Out of Scope (This Iteration)

- Background task processing (Celery/Redis)
- Real-time server-side progress via WebSockets
- Chunked upload for very large IFC files
- Upload cancellation mid-flight

---

## 4. Decision: Option A — Sequential XHR Queue

### Rationale

The project runs on Django Channels + Daphne and already has WebSocket
infrastructure. However, `IFCProcessingService.run_pipeline()` and
`DocumentProcessor.process()` are **synchronous, CPU-bound operations**
(ifcopenshell parsing + embedding). Making them emit real-time progress requires
offloading to a background worker (Celery). Introducing Celery mid-project is a
coordination overhead incompatible with the current team constraints.

Option A is chosen as the **correct pragmatic trade-off**: zero new
infrastructure, honest UX, and a clear migration path to Option B.

### Mechanism

1. User selects or drops N files (any mix of `.ifc`, `.pdf`, `.docx`, `.txt`)
2. Files enter a **client-side queue array** — no files are sent yet
3. A queue processor picks `queue[0]` and sends it via `XMLHttpRequest POST`
   to `/projects/<pk>/upload/`
4. The HTTP connection stays open for the full duration of `run_pipeline()` /
   `process()` — acceptable for a thesis-scale system
5. On response, the file row is marked success or error; processor moves to
   `queue[1]`
6. The drop zone remains active throughout — new files can be added at any time
7. When the queue drains, a summary banner appears with a "Back to Project" button

### Backend Contract

`POST /projects/<pk>/upload/` — one file per request via `multipart/form-data`.

**Request field:** `file`

**Success response (IFC):**
```json
{
  "success": true,
  "file_type": "ifc",
  "file_name": "building.ifc",
  "entity_count": 1024
}
```
**Success response (Document):**
```json

{
  "success": true,
  "file_type": "pdf",
  "file_name": "spec.pdf",
  "chunk_count": 48
}
```


**Error response:**
```json

{
  "success": false,
  "error": "Failed to parse IFC file: ..."
}
```


**UI State Machine (per file row)**
queued → uploading (XHR progress %) → processing (spinner) → success | error


| State         | Visual                                                                    |
| ------------- | ------------------------------------------------------------------------- |
| queued        | Grey clock icon, "Waiting"                                                |
| uploading     | Animated progress bar with byte percentage                                |
| processing    | Indeterminate spinner — pipeline running on server, HTTP not yet returned |
| success (IFC) | Green check, entity count                                                 |
| success (doc) | Green check, chunk count                                                  |
| error         | Red X, inline error message from server                                   |

Summary Section (appears when queue drains)

- "N of M files processed successfully"
- List of failures with filenames and error reasons if any
- Prominent "Back to Project" button
- "Upload more files" link to reset the drop zone

## 5. Decision: Option A — Sequential XHR Queue

| File                                    | Change                                                                       |
| --------------------------------------- | ---------------------------------------------------------------------------- |
| apps/projects/views.py                  | FileUploadView._handle_document_upload — add chunk_count to success response |
| apps/projects/views.py                  | Remove UploadIFCView (dead code, superseded by FileUploadView)               |
| apps/projects/urls.py                   | Remove URL entry for UploadIFCView                                           |
| templates/environments/file_upload.html | Full overhaul — queue UI, per-file rows, summary section                     |

FileUploadView.post() routing logic requires no changes — it already
correctly dispatches by extension.


## 6. Future Path — Option B (Production / Full Scale)

This section is for reference only. Not implemented in this iteration.

If Castor is deployed at scale or processing time becomes a UX blocker, the
correct professional approach is:

**Architecture**

Browser                          Django / Channels
  |                                    |
  |-- POST /upload (file) -----------> | ← saves file, creates DB record
  |<-- 202 Accepted {task_id} -------- | ← returns immediately
  |                                    |
  |== WS: subscribe(task_id) ========> |
  |                                    | ← Celery worker picks up task
  |<== {stage: "parsing",   pct: 30} ==|
  |<== {stage: "embedding", pct: 70} ==|
  |<== {done: true, entity_count: 512} |

**Components Required**

| Component                    | Role                                                                   |
| ---------------------------- | ---------------------------------------------------------------------- |
| Celery                       | Offloads run_pipeline() / process() to a background worker process     |
| Redis                        | Message broker for Celery; also Channel Layer (may already be present) |
| Django Channels consumer     | Receives task progress events, fans out to subscribed WS connections   |
| task_id → WS channel routing | Associates an upload to a specific browser session/tab                 |

**Why This Is the Correct Production Approach**

-    HTTP worker threads freed immediately after 202 — no blocking under load
-    Genuine per-stage progress meaningful for IFC (parsing ≠ hashing ≠ embedding)
-    All N files POSTed simultaneously; WS fan-out handles parallel status updates
-    Tasks are retryable and observable via Celery monitoring (Flower)
-    Decouples upload reliability from processing reliability

**Migration Notes (when the time comes)**

- FileUploadView.post() changes from run_pipeline() call to celery_task.delay(ifc_file.id)
- A new UploadConsumer (Django Channels) handles WS subscriptions by task_id
- Frontend adds a WS connection per batch; each file row subscribes to its channel
- HTTP response contract changes from 200 + result to 202 + task_id
- All existing service classes remain unchanged internally

## 7. Open Questions (Resolved).

| Question                                  | Decision                                                |
| ----------------------------------------- | ------------------------------------------------------- |
| Remove UploadIFCView?                     | Yes — confirm no external callers before deleting       |
| Parallel vs sequential uploads?           | Sequential — CPU-bound pipeline makes parallel harmful  |
| Redirect after upload or in-page summary? | In-page summary — preserves context, allows adding more |
| WebSockets for progress this iteration?   | No — requires Celery; deferred to Option B              |
