# Castor — GLM-OCR Integration Spec v2

**Subsystem:** Document Intelligence / Visual Content Extraction
**Scope:** Use Cases 3A (Auto OCR Fallback) + 3B (Manual OCR Trigger)
**Status:** Ready for Claude Code planning session
**Date:** 2026-03-23
**URL: ** https://github.com/zai-org/GLM-OCR
---

## 1. Problem Statement

Castor's RAG pipeline extracts text from uploaded documents (PDF, DOCX) using conventional parsers (PyMuPDF, python-docx). This silently fails for:

- **Scanned PDFs** — image-only pages with zero extractable text
- **Image-heavy pages in vectorial PDFs** — pages where the meaningful content is in embedded raster images (stamped drawings, tables-as-images, annotated figures)

When this happens, the chunking pipeline produces empty or near-empty chunks, the vector store contains no useful embeddings for those pages, and the user gets bad RAG answers with no indication that content was missed. In AEC (architecture, engineering, construction), scanned specs and stamped drawings are extremely common — this is not an edge case.

---

## 2. Solution Overview

Integrate GLM-OCR (a 0.9B multimodal OCR model by Zhipu AI / Tsinghua, available on Ollama) as a **transparent preprocessing layer** in the document ingestion pipeline. The model extracts structured Markdown from page images, which then feeds into the existing chunking and embedding pipeline as if it were native text.

**Two interaction modes, one underlying service:**

| Mode | Trigger | User Action Required |
|---|---|---|
| **3A — Auto Fallback** | Document upload (when auto-trigger is ON) | None — happens transparently in background |
| **3B — Manual Trigger** | User clicks "Run OCR" on a document | Explicit button press |

Both modes call the same `GLMOCRService.analyze_document()` method. The only difference is invocation path.

---

## 3. Design Constraints

These are non-negotiable and derive from Castor's architecture:

1. **GLM-OCR is a system-level tool, not a user-selectable LLM.** It must NOT appear in the Settings model picker (`UserLLMConfig`). It is configured once at the environment level via settings/env vars.

2. **It does not replace the chat/reasoning LLM.** It is a preprocessing worker whose output feeds the existing embedding pipeline.

3. **Invocation must never block the Django request cycle.** All GLM-OCR calls run in async background processing.

4. **Local-first.** GLM-OCR runs via Ollama locally. No cloud OCR fallback.

5. **Feature-flagged.** `GLM_OCR_ENABLED = False` makes the entire subsystem a no-op with zero side effects on the existing pipeline.

6. **Service layer pattern.** All OCR logic lives in a service class. Consumers only coordinate and stream — they call services via `sync_to_async`. Views are dumb.

7. **User transparency.** Any document processed with OCR surfaces that fact in the UI.

---

## 4. GLM-OCR Model Details

| Property | Value |
|---|---|
| Model | `glm-ocr:latest` (Ollama tag) |
| Architecture | 0.4B CogViT visual encoder + 0.5B GLM language decoder |
| Parameters | ~0.9B total |
| Output format | Structured Markdown (preserves tables, formulas, code blocks, layout) |
| Benchmark | 94.62 on OmniDocBench V1.5 (rank #1) |
| Throughput | ~1.86 PDF pages/sec, ~0.67 images/sec (single concurrency) |
| API | Ollama standard vision/multimodal endpoint (`/api/chat` with image payload) |
| Input | Base64-encoded image (one image per call) |

---

## 5. Proposed Pipeline

### 5A. Auto Fallback (Use Case 3A)

```
User uploads PDF
       │
       ▼
Conventional text extraction (existing pipeline)
       │
       ▼
Per-page text density check
  chars_extracted / page → compare to threshold
       │
       ├── Above threshold → page has text → proceed normally
       │
       └── Below threshold → page is image-dominant
              │
              ▼
       Flag document for OCR processing
       Update document status → PENDING_OCR
              │
              ▼
       Async OCR processing (page by page):
         1. Render page to image (PyMuPDF)
         2. Send image to GLM-OCR via Ollama
         3. Receive Markdown output
         4. Store as OCR-sourced chunks
              │
              ▼
       Embed new chunks into vector store
       Update document status → READY
       Notify UI via WebSocket (if connected)
```

**Critical detail:** The existing ingestion pipeline runs first and completes normally. OCR is a **second pass** on flagged pages only — it does not block or replace the primary ingestion. Text-rich pages keep their original parser output.

### 5B. Manual Trigger (Use Case 3B)

```
User clicks "Run OCR" on document detail page
       │
       ▼
WebSocket connection opened (OCRConsumer)
       │
       ▼
GLMOCRService.analyze_document() called via sync_to_async
  - Processes ALL pages (not just low-density ones)
  - Replaces existing chunks for OCR'd pages
  - Streams page-by-page progress via WebSocket
       │
       ▼
Re-embed affected chunks
Update document status → READY
```

**Key difference from 3A:** Manual mode processes all pages (the user is explicitly saying "this document needs OCR"), while auto mode only processes pages below the text density threshold.

---

## 6. Proposed Model Changes

### Document Model — New Fields

All fields must be nullable and backward-compatible with existing data (no existing rows break).

| Field | Type | Default | Purpose |
|---|---|---|---|
| `ocr_status` | CharField(choices) | `"NONE"` | NONE / PENDING / PROCESSING / DONE / FAILED |
| `ocr_processed_at` | DateTimeField | null | When OCR last completed |
| `ocr_page_count` | PositiveIntegerField | null | Number of pages processed by OCR |
| `ocr_engine` | CharField | null | Engine identifier, e.g. `"glm-ocr:latest"` |
| `has_visual_content` | BooleanField | `False` | Auto-set during ingestion scan |
| `source_type` | CharField(choices) | `"PDF"` | PDF / DOCX / SCANNED_PDF (auto-detected) |

### DocumentChunk Model — New Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `chunk_source` | CharField(choices) | `"TEXT_PARSER"` | TEXT_PARSER / GLM_OCR / HYBRID |
| `page_number` | PositiveIntegerField | null | Source page in document |
| `extracted_from_image` | BooleanField | `False` | Whether this chunk came from an image |

**IMPORTANT:** These are proposed based on reading the architecture doc, not the actual codebase. The Claude Code planning session MUST audit the existing models first — some of these fields may already exist under different names, or the chunk pipeline may not support adding metadata at the points assumed here.

---

## 7. Service Layer Design

```
documents/services/ocr.py  (or ocr/ app — see Open Question #1)

class GLMOCRService:
    """
    System-level OCR service using GLM-OCR via Ollama.
    Not a user-facing LLM — configured at environment level.
    """

    def __init__(self, ollama_client=None):
        # Ollama HTTP client is an injected dependency for testability
        # Falls back to default client built from settings

    def is_available(self) -> bool
        # Ping Ollama, confirm glm-ocr model is pulled

    def process_image(self, image_bytes: bytes, prompt: str | None = None) -> str
        # Send single image to GLM-OCR, return Markdown string
        # This is the atomic unit — everything else calls this

    def render_page_to_image(self, page: fitz.Page, dpi: int = 150) -> bytes
        # Render a PDF page to PNG bytes at specified DPI

    def should_ocr_page(self, page: fitz.Page, threshold: int = None) -> bool
        # Heuristic: text char count vs threshold
        # threshold defaults to settings.GLM_OCR_TEXT_DENSITY_THRESHOLD

    def analyze_document(self, document: Document, force_all_pages: bool = False,
                         progress_callback: Callable | None = None) -> OCRResult
        # Main entry point — called by both auto and manual flows
        # force_all_pages=True for manual trigger (3B)
        # force_all_pages=False for auto fallback (3A, only low-density pages)
        # progress_callback receives (page_num, total, status) for streaming
        #
        # IMPORTANT: This is the single dispatch point.
        # In the future, this call gets replaced with .delay() for Celery
        # without touching consumers or views.
```

**Testability:** The Ollama HTTP client is injected, not hardcoded. Tests mock the client and assert on service behavior without needing a running Ollama instance.

---

## 8. Async Execution — WebSocket Consumer

Follow the existing `ProposalConsumer` / `ScanConsumer` pattern. The consumer is a thin coordination layer that calls the service and streams results.

```
documents/consumers.py  (or writeback/consumers.py — depends on codebase audit)

class OCRConsumer(WebsocketConsumer):
    # Receives: { "action": "run_ocr", "document_id": "..." }
    # Calls GLMOCRService.analyze_document() via sync_to_async
    # Streams progress via WebSocketEmitter (reuse existing utility)
```

### WebSocket Message Schema

Preserve this schema exactly — do not invent alternatives, even if simpler. This avoids frontend churn if the protocol is later reused.

```json
// Page-level progress
{ "type": "ocr_progress", "page": 3, "total": 12, "status": "processing" }
{ "type": "ocr_progress", "page": 3, "total": 12, "status": "done" }

// Completion
{ "type": "ocr_complete", "chunks_created": 47, "pages_ocrd": 8 }

// Error (non-fatal, page-level)
{ "type": "ocr_error", "page": 5, "error": "model timeout" }

// Fatal error (document-level)
{ "type": "ocr_failed", "error": "GLM-OCR model not available" }
```

---

## 9. Settings / Configuration

```python
# Django settings or .env

GLM_OCR_ENABLED = True                    # Hard feature flag — False = entire subsystem is no-op
GLM_OCR_MODEL = "glm-ocr:latest"          # Ollama model tag
GLM_OCR_OLLAMA_URL = "http://localhost:11434"  # Ollama base URL
GLM_OCR_TEXT_DENSITY_THRESHOLD = 50        # Chars per page below which auto-OCR fires
GLM_OCR_AUTO_TRIGGER = True                # False = manual-only mode (3B only)
GLM_OCR_MAX_PAGES_AUTO = 100              # Safety cap for auto mode
GLM_OCR_PAGE_DPI = 150                    # Render DPI for page-to-image conversion
```

**Note on threshold:** The 50 chars/page value is an unvalidated starting point. The planning session should propose a way to make this empirically calibrated (e.g., test against a sample set of known scanned vs. digital PDFs from AEC projects).

---

## 10. UI Touchpoints

### Documents List View
- Status badge on each document: **"OCR Processed"** (green), **"Awaiting OCR"** (amber), **"OCR Failed"** (red)
- Optional filter: "Show documents with visual content"

### Document Detail View
- **"Run OCR" button** — visible when document status is READY, NONE, or FAILED
- OCR metadata panel: engine used, pages processed, timestamp
- Per-chunk source indicator: TEXT_PARSER vs GLM_OCR badge on chunk list (if chunks are displayed)

### Upload Flow (auto-trigger)
- If `GLM_OCR_AUTO_TRIGGER` is ON and the document is flagged as image-heavy after initial ingestion:
  - Toast notification: *"Document contains visual content — OCR processing started in background"*
  - Document status updates live via WebSocket as processing completes

---

## 11. Integration with Existing Systems

### RAG Pipeline
OCR-extracted text flows into `DocumentChunk` with `chunk_source = "GLM_OCR"`. From there, it enters the same embedding and vector store pipeline as any other chunk. **No changes to the RAG retrieval or prompt assembly logic.**

### RAV (Retrieval-Augmented Verification)
No changes required. RAV queries the same `DocumentChunk` table. One optional enhancement: RAV could surface `chunk_source` metadata in its advisory output — *"This requirement was found in an OCR-extracted section — review the original document to confirm accuracy."*

### Per-user LLM Selection
GLM-OCR is completely independent of `UserLLMConfig` and `get_llm(user)`. It does not appear in the Settings model picker. It has its own config path via Django settings.

---

## 12. Dependencies

| Dependency | Status (verify in codebase) | Purpose |
|---|---|---|
| PyMuPDF (fitz) | Likely present (used for PDF text extraction) | Page rendering, text density check, image extraction |
| Pillow | Likely present | Image format handling |
| httpx | May need to be added | Async HTTP client for Ollama API calls |
| pdf2image | NOT needed if using PyMuPDF for rendering | — |

---

## 13. Out of Scope

These are explicitly excluded from this implementation:

- **Use Case 3C** — Scanned PDF → searchable PDF export (standalone utility, no RAG value)
- **Use Case 3D** — Photo / image ingestion as new media type (requires new models, upload handling)
- **Use Case 3E** — Embedded image extraction from vectorial PDFs (rabbit hole, Phase 2 candidate)
- Handwriting recognition (GLM-OCR supports it but accuracy is unreliable)
- Multi-language OCR (Castor is English-only)
- Cloud OCR fallback (violates local-first)
- Celery integration (future-proof for it, don't implement it)

---

## 14. Open Questions for Planning Session

These must be resolved during the Claude Code planning session before implementation begins. Present answers as explicit YES/NO or OPTION A/B decisions.

| # | Question | Options |
|---|---|---|
| 1 | Should OCR logic live in a new `ocr/` Django app or inside `documents/services/ocr.py`? | **A:** New app (cleaner separation, own models if needed) / **B:** Service inside documents (less moving parts, no new app registration) |
| 2 | Should the OCRConsumer run as a second-pass process triggered after initial ingestion completes, or should it wrap/extend the existing ingestion flow? | **A:** Second pass (decoupled, safer) / **B:** Integrated (single pipeline, but higher risk of breaking existing flow) |
| 3 | Does the existing chunking pipeline accept metadata (page_number, source) at chunk creation time, or does it need to be refactored? | Depends on codebase audit |
| 4 | What DPI should be used for page rendering? 150 is the spec default but unvalidated. | Test 96 / 150 / 200 on a sample scanned spec sheet |
| 5 | Should auto-trigger (3A) run inline after ingestion (same request cycle, async) or be dispatched as a separate background task? | **A:** Dispatched separately (cleaner) / **B:** Chained to ingestion completion signal |
| 6 | The text density threshold (50 chars/page) is a magic number. How should it be calibrated? | Propose a test methodology during planning |

---

## 15. Future-Proofing (Do Not Implement, Keep in Mind)

- The correct production path for heavy OCR is: `POST → 202 + task_id → Celery worker → Channels WebSocket fan-out`. Do not design Phase 1 in a way that makes this migration harder.
- Isolate `GLMOCRService.analyze_document()` to a single dispatch point so it can be swapped for `.delay()` later without touching consumers or views.
- Keep the WebSocket message schema from Section 8 stable — frontend will be built against it.
- Use Case 3E (embedded image extraction) will likely reuse `GLMOCRService.process_image()` — design that method to be standalone and reusable, not coupled to full-document analysis.

---

## 16. Claude Code Planning Prompt

Use the following prompt to initiate the planning session. Paste it into Claude Code along with this spec.

---

You are a senior Django engineer doing architecture planning before implementing a new subsystem. Attached is the GLM-OCR integration spec v2 for Castor. Read it, but do NOT treat it as ground truth — it was written without full codebase visibility and contains assumptions about model fields, ingestion flow, consumer patterns, and service structure that may be wrong or outdated.

Do NOT write any code. This is a planning session only.

### STEP 1 — AUDIT THE CODEBASE FIRST

Before evaluating the spec, find:

**Documents app:**
- The exact field names on Document and DocumentChunk models — especially any existing status, source_type, or processing-related fields
- Where and how documents are currently ingested (which service, which view, which signals or post-save hooks trigger chunking and embedding)
- Whether DocumentChunk already has page_number, chunk_source, or any provenance metadata
- The exact method signature of the chunking/embedding pipeline entry point

**Async / consumer layer:**
- How ProposalConsumer and ScanConsumer are structured — base class, streaming pattern, sync_to_async usage
- Whether there is already a Celery dependency anywhere (settings, requirements, docker-compose) — the spec assumes there is NOT
- Whether Django Channels handles any document-related consumers, or only writeback consumers

**Ollama integration:**
- How `get_llm(user)` and the Ollama client are currently instantiated (`core/llm.py`)
- Whether there is a shared Ollama base URL config or if it is hardcoded
- Whether any existing service calls Ollama directly via HTTP (not via LangChain) that GLMOCRService could follow as a pattern

**Dependencies:**
- Is PyMuPDF (fitz) already in requirements?
- Is Pillow already present?
- Is httpx already present?
- Are there any existing image-handling utilities?

### STEP 2 — CHALLENGE THE SPEC

After the audit, explicitly challenge these assumptions:

1. The spec proposes adding `ocr_status`, `ocr_engine`, `has_visual_content`, `source_type` to Document. Do any already exist under different names? Does adding them conflict with existing migrations?

2. The spec assumes DocumentChunk can receive `chunk_source` and `extracted_from_image` fields. Confirm the current chunk pipeline writes chunks in a way that these fields can be populated without refactoring the entire ingestion flow.

3. The spec recommends an OCRConsumer following the ProposalConsumer pattern. Verify whether this is the right pattern — OCR is document-scoped (one document, many pages), not proposal-scoped. Challenge whether a consumer is better than a background task triggered post-upload.

4. The spec assumes GLM-OCR can be called via Ollama's standard API with an image payload. Verify the exact Ollama API contract for vision/multimodal models (endpoint, payload shape, response format).

5. The text density threshold of 50 chars/page is unvalidated. Propose how to calibrate it empirically.

6. Should Use Cases 3A (auto fallback) and the future 3E (embedded image extraction) share a single unified page-analysis pass to avoid scanning PDFs twice? Flag this for architecture even though 3E is out of scope.

Flag any other contradictions or unverifiable claims you find. Be direct.

### STEP 3 — ENGINEERING STANDARDS (Non-Negotiable)

Apply these to everything you plan:

- Clean Code + Zen of Python + DRY
- Negative Space Programming: guard clauses over nesting, design by omission
- Views are dumb — all OCR logic lives in `services/`, never in consumers or views
- Consumers only coordinate and stream — they call services via `sync_to_async`
- Type hints on all signatures and return types
- Docstrings on every module, class, and public method
- File header comment: `# app/path/to/file.py`
- Logging only (never print), all log messages in English
- `select_related` / `prefetch_related` on every queryset touching Document or DocumentChunk
- GLMOCRService must be independently testable with a mock Ollama fixture — design the Ollama HTTP client as an injected dependency
- `GLM_OCR_ENABLED` must act as a hard feature flag — if False, zero side effects on existing pipeline

### STEP 4 — PRESENT A REVISED PLAN

After audit and challenge:

1. **Correct every spec error** — state what the spec says vs. what the codebase shows vs. what you recommend
2. **Define a phased rollout:**
   - Phase 1: Auto fallback + manual trigger (3A + 3B)
   - Phase 2: Embedded image extraction from vectorial PDFs (3E, future)
   - Phase 3: Photo/image ingestion as new media type (3D, future)
3. **List exactly which files you will create or modify** in Phase 1, and why
4. **For each new model field**, confirm it is nullable/backward-compatible
5. **List every decision point** that requires a human answer before implementation — present as YES/NO or OPTION A/B, not open-ended

Ask for confirmation before planning any work that touches more than 3 existing files outside `documents/`.

### STEP 5 — FUTURE-PROOFING (Do Not Implement)

- Production OCR path = `POST → 202 → Celery → WebSocket fan-out`. Don't block this.
- Isolate `GLMOCRService.analyze_document()` as a single dispatch point swappable for `.delay()`.
- Keep the WebSocket message schema from the spec stable.

Strip all filler. If the spec is wrong, say so directly and say why. Show: audit results → challenge findings → revised plan.

---