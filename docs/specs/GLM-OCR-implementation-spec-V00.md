================================================================================
CASTOR — GLM-OCR INTEGRATION SPEC
Subsystem: Document Intelligence / Visual Content Extraction
Status: Investigation / Pre-Planning
Last updated: 2026-03-20
================================================================================

## 1. CONTEXT

Castor's RAG pipeline currently processes documents (PDF, DOCX) by extracting
plain text via conventional parsers (e.g. PyMuPDF, python-docx). This works
well for native digital documents but silently fails or degrades for:

  - Scanned PDFs (image-only pages with zero extractable text)
  - Pages in vectorial PDFs that contain embedded raster images carrying
    meaningful information (diagrams, stamped drawings, photos, tables as images)
  - Standalone photos or site images uploaded by users
  - Hand-annotated documents or inspection sheets

GLM-OCR is a 0.9B multimodal OCR model by Zhipu AI / Tsinghua University,
available on Ollama. It combines a 0.4B CogViT visual encoder with a 0.5B GLM
language decoder. It outputs structured Markdown or JSON preserving layout,
tables, formulas, and code blocks — not just raw character strings.

Benchmark: 94.62 on OmniDocBench V1.5 (rank #1 overall).
Throughput: ~1.86 PDF pages/sec, ~0.67 images/sec (single concurrency).

This model is a strong candidate to augment Castor's document ingestion
pipeline and open new media types to the RAG corpus.


## 2. DESIGN PHILOSOPHY FOR THIS INTEGRATION

  - GLM-OCR is a SYSTEM-LEVEL tool, not a per-user LLM selection. It must NOT
    appear in the user Settings model picker (UserLLMConfig). It is configured
    once at the system/environment level (e.g. GLMOCR_MODEL env var).

  - It does NOT replace the main chat/reasoning LLM. It is a pre-processing
    worker whose output feeds the existing embedding pipeline unchanged.

  - Invocation must NEVER block the Django request cycle. All GLM-OCR calls
    must run in async background workers (Celery task or async service).

  - User transparency: any Document processed with GLM-OCR should surface that
    fact in the UI (e.g. "Processed with OCR" badge, page count, confidence).

  - The feature should be modular — a dedicated `ocr/` app or service layer
    inside `documents/` — so it can be extended or swapped out independently.


## 3. CANDIDATE USE CASES

### 3A. Automatic OCR Fallback During Document Ingestion (Primary)
  - Trigger: document upload (PDF or image file)
  - Logic: after conventional text extraction, measure text density per page
    (chars extracted / page area). If a page falls below a threshold (e.g.
    < 50 chars per page), flag it as "image-dominant" and queue it for GLM-OCR.
  - Output: extracted Markdown/text merged back into the document's chunk
    pipeline as if it were native text.
  - Benefit: transparent to the user — scanned pages simply work.
  - Risk: latency. Mitigate by running asynchronously with a status field on
    the Document model (PENDING → PROCESSING_OCR → READY / FAILED).

### 3B. Manual OCR Trigger (Explicit User Action)
  - Trigger: user clicks "Run OCR" on an already-uploaded document.
  - Use case: user knows a document is scanned and wants to (re)process it
    explicitly; or automatic heuristic missed it.
  - UI: button on the Document detail view, visible only when document status
    is READY or OCR_FAILED. Streams progress via WebSocket (reuse existing
    pattern from writeback consumers).
  - Output: re-chunks the document and re-embeds, replacing old chunks.

### 3C. Scanned PDF → Searchable PDF Export (Standalone Feature)
  - A dedicated workflow where a user uploads a scanned PDF and Castor produces
    a text-layer-enriched or Markdown export as a downloadable artifact.
  - Does NOT necessarily enter the RAG pipeline — it's a utility feature.
  - Potential output formats: .md, .txt, or a text-overlaid PDF.
  - Benefit: useful even outside the RAG context; positions Castor as a
    document utility tool for AEC teams.
  - Complexity: medium. Requires page-level rendering (e.g. pdf2image / fitz)
    and reassembly.

### 3D. Photo / Image Ingestion (New Media Type)
  - Currently Castor does not manage photos or standalone images.
  - GLM-OCR could process photos of site conditions, whiteboard sketches,
    annotated printouts, material labels, signage, etc.
  - These would become retrievable context in the RAG pipeline, linked to the
    project like any other Document.
  - Supported formats: JPEG, PNG, TIFF, WEBP.
  - The Document model (or a new Image model) would need a source_type field
    to distinguish text docs from visual media.
  - This is the highest-novelty use case and the most exploratory — treat as
    a stretch goal until 3A/3B are stable.

### 3E. Embedded Image Extraction from Vectorial PDFs
  - Even native-text PDFs often contain raster figures: floor plan excerpts,
    material data sheets, equipment photos, QR codes, stamps.
  - Extract embedded images per page (via PyMuPDF's get_images()), pass each
    through GLM-OCR, and append extracted text to that page's chunk.
  - This is the most impactful for typical AEC documents (specs with embedded
    detail drawings).
  - Can be combined with 3A as part of a unified page analysis pass.


## 4. PROPOSED DOCUMENT MODEL CHANGES

Current Document model (inferred from architecture):
  - id (UUID)
  - project (FK)
  - uploaded_by (FK)
  - file (FileField)
  - created_at, updated_at

Proposed additions:
  - ocr_status: CharField, choices=NONE/PENDING/PROCESSING/DONE/FAILED
  - ocr_processed_at: DateTimeField (nullable)
  - ocr_page_count: IntegerField (nullable)
  - ocr_engine: CharField (nullable, e.g. "glm-ocr:latest")
  - has_visual_content: BooleanField (auto-set during ingestion scan)
  - source_type: CharField, choices=PDF/DOCX/IMAGE/SCANNED_PDF
    (auto-detected or user-specified)

DocumentChunk additions:
  - chunk_source: CharField, choices=TEXT_PARSER/GLM_OCR/HYBRID
  - page_number: IntegerField (nullable)
  - extracted_from_image: BooleanField


## 5. SERVICE LAYER DESIGN (ocr/services.py or documents/services/ocr.py)

class GLMOCRService:
    - model_name: str  (from settings, e.g. "glm-ocr:latest")
    - ollama_base_url: str (from settings)

    def is_available(self) -> bool
        # Ping Ollama to confirm model is pulled and responding

    def process_image(self, image_bytes: bytes, prompt: str = None) -> str
        # Send image to GLM-OCR via Ollama API, return Markdown string

    def process_pdf_page(self, page: fitz.Page) -> str
        # Render page to image, call process_image

    def analyze_document(self, document: Document) -> OCRResult
        # Full pipeline: detect pages needing OCR, process, return structured
        # result with per-page text and metadata

    def should_ocr_page(self, page: fitz.Page) -> bool
        # Heuristic: text density + embedded image detection

Key dependencies: PyMuPDF (fitz), httpx (async Ollama calls), Pillow


## 6. ASYNC EXECUTION PATTERN

Option A — Celery Task (recommended if Celery is added to the stack):
  @shared_task
  def run_ocr_for_document(document_id: UUID):
      ...

Option B — Django Channels async consumer (reuses existing infrastructure):
  OCRConsumer(WebsocketConsumer):
      # Streams page-by-page progress to the UI
      # Consistent with ProposalConsumer / ScanConsumer patterns

Option B is preferred to stay consistent with Castor's existing async pattern
and avoid introducing Celery as a new dependency.

WebSocket message schema (page-level streaming):
  { "type": "ocr_progress", "page": 3, "total": 12, "status": "done" }
  { "type": "ocr_complete", "chunks_created": 47, "pages_ocr'd": 8 }
  { "type": "ocr_error", "page": 5, "error": "model timeout" }


## 7. SETTINGS / CONFIGURATION

In Django settings (or .env):

  GLM_OCR_ENABLED = True
  GLM_OCR_MODEL = "glm-ocr:latest"
  GLM_OCR_OLLAMA_URL = "http://localhost:11434"
  GLM_OCR_TEXT_DENSITY_THRESHOLD = 50  # chars per page below which OCR fires
  GLM_OCR_AUTO_TRIGGER = True          # False = manual-only mode
  GLM_OCR_MAX_PAGES_AUTO = 100         # Safety cap for auto mode


## 8. UI TOUCHPOINTS

  Documents list view:
    - Status badge: "OCR Processed", "Awaiting OCR", "OCR Failed"
    - Filter: "Show documents with visual content"

  Document detail view:
    - "Run OCR" button (if applicable)
    - OCR metadata panel: engine used, pages processed, date
    - Per-chunk source indicator (TEXT_PARSER vs GLM_OCR)

  Upload flow:
    - If auto-trigger is ON and file is image-heavy: toast notification
      "Document contains visual content — OCR processing started in background"

  New standalone page (optional, Use Case 3C):
    - "OCR Converter" tool: upload scanned PDF → download Markdown or text


## 9. INTEGRATION WITH RAV (Retrieval-Augmented Verification)

GLM-OCR-extracted chunks flow into the same DocumentChunk table and vector
space as text-parser chunks. RAV requires no changes — it will automatically
benefit from richer chunk coverage.

One enhancement worth exploring: RAV could surface chunk_source metadata in
its advisory output ("This requirement was found in an OCR-extracted section —
review the original document to confirm accuracy").


## 10. OUT OF SCOPE (FOR NOW)

  - Handwriting recognition (GLM-OCR handles it but accuracy varies; defer)
  - Real-time camera input
  - IFC drawing / geometry interpretation via GLM-OCR
  - Multi-language OCR (Castor is English-only per architecture constraints)
  - Cloud-based OCR fallback (violates local-first principle)


## 11. OPEN QUESTIONS

  1. Is pdf2image / fitz page rendering fast enough at ~150 DPI for GLM-OCR
     input quality? (Test: compare 96 / 150 / 200 DPI on a sample spec sheet)
  2. Should OCRConsumer replace or wrap the existing document ingestion flow,
     or run as a second-pass consumer triggered post-ingestion?
  3. For Use Case 3D (photo ingestion), should images be a separate Django app
     (`images/`) or extend the existing `documents/` app with source_type?
  4. What prompt should be sent to GLM-OCR for AEC documents specifically?
     (Default generic vs. domain-tuned: "Extract all text, tables, and
     annotations from this architectural/engineering document.")
  5. Token/context limits: GLM-OCR processes one image at a time. Very large
     embedded images may need tiling. Investigate Ollama's image size limits.


================================================================================
CLAUDE CODE PLANNING PROMPT
================================================================================

Use the following prompt to initiate a planning session in Claude Code:

---

You are a senior Django engineer doing architecture planning before implementing
a new subsystem. Attached is the GLM-OCR integration spec for Castor. Read it,
but do NOT treat it as ground truth — it was written without full codebase
visibility and contains assumptions about model fields, ingestion flow, consumer
patterns, and service structure that may be wrong or outdated.

Do NOT write any code. This is a planning session only.

────────────────────────────────────────────────────────────────────────────────
STEP 1 — AUDIT THE CODEBASE FIRST
────────────────────────────────────────────────────────────────────────────────

Before evaluating the spec, find:

  Documents app:
    - The exact field names on Document and DocumentChunk models — especially
      any existing status, source_type, or processing-related fields
    - Where and how documents are currently ingested (which service, which view,
      which signals or post-save hooks trigger chunking and embedding)
    - Whether DocumentChunk already has page_number, chunk_source, or any
      provenance metadata
    - The exact method signature of the chunking/embedding pipeline entry point

  Async / consumer layer:
    - How ProposalConsumer and ScanConsumer are structured — what base class
      they inherit, how they stream phases, how they call sync services via
      sync_to_async
    - Whether there is already a Celery dependency anywhere in settings,
      requirements, or docker-compose (the spec assumes there is NOT — verify)
    - Whether Django Channels is already handling any document-related
      consumers, or only writeback consumers

  Ollama integration:
    - How get_llm(user) and the Ollama client are currently instantiated
      (core/llm.py) — specifically whether there is a shared Ollama base URL
      config or if it is hardcoded
    - Whether there is any existing service that calls Ollama directly via HTTP
      (not via LangChain) that GLMOCRService could follow as a pattern

  Dependencies:
    - Is PyMuPDF (fitz) already in requirements? If not, flag it as a new dep
    - Is Pillow already present?
    - Is pdf2image present or absent?
    - Are there any existing image-handling utilities in the codebase?

────────────────────────────────────────────────────────────────────────────────
STEP 2 — CHALLENGE THE SPEC
────────────────────────────────────────────────────────────────────────────────

After the audit, explicitly challenge these assumptions from the spec:

  1. The spec proposes adding ocr_status, ocr_engine, has_visual_content,
     source_type to the Document model. Do any of these already exist under
     different names? Does adding them conflict with any existing migration?

  2. The spec assumes DocumentChunk can receive a chunk_source and
     extracted_from_image field. Confirm the current chunk pipeline writes
     chunks in a way that these fields can be populated without refactoring
     the entire ingestion flow.

  3. The spec recommends an OCRConsumer following the ProposalConsumer pattern.
     Verify whether this is actually the right pattern — OCR is document-scoped
     (one document, many pages), not proposal-scoped. Challenge whether a
     consumer is better than a background task triggered post-upload, given
     Castor's current async infrastructure.

  4. The spec assumes GLM-OCR can be called via Ollama's standard API with an
     image payload. Verify this is true for the glm-ocr:latest tag — confirm
     the exact Ollama API contract for vision/multimodal models (endpoint,
     payload shape, response format).

  5. The spec sets a text density threshold of 50 chars/page. Flag this as an
     unvalidated magic number. Propose how to make it empirically calibrated
     rather than hardcoded.

  6. The spec treats Use Cases 3A (auto fallback) and 3E (embedded image
     extraction) as separable. Challenge whether they should be a single unified
     page-analysis pass to avoid scanning the same PDF twice.

  Flag any other contradictions or unverifiable claims you find. Be direct.

────────────────────────────────────────────────────────────────────────────────
STEP 3 — ENGINEERING STANDARDS (non-negotiable)
────────────────────────────────────────────────────────────────────────────────

Apply these to everything you plan:

  - Clean Code + Zen of Python + DRY
  - Negative Space Programming: guard clauses over nesting, design by omission
  - Views are dumb — all OCR logic lives in services/, never in consumers
    or views directly
  - Consumers only coordinate and stream — they call services via sync_to_async
  - Type hints on all signatures and return types
  - Docstrings on every module, class, and public method
  - File header comment: # app/path/to/file.py
  - Logging only (never print), all log messages in English
  - select_related / prefetch_related on every queryset touching Document or
    DocumentChunk
  - GLMOCRService must be independently testable with a mock Ollama fixture —
    design the Ollama HTTP client as an injected dependency, not a hardcoded call
  - GLM_OCR_ENABLED must act as a hard feature flag — if False, the entire
    subsystem is a no-op with zero side effects on the existing pipeline

────────────────────────────────────────────────────────────────────────────────
STEP 4 — PRESENT A REVISED PLAN
────────────────────────────────────────────────────────────────────────────────

After the audit and challenge:

  1. Correct every spec error you found — state what the spec says vs. what
     the codebase shows and what you recommend instead
  2. Define a phased rollout:
       Phase 1: Auto fallback + manual trigger (OCR on scanned pages)
       Phase 2: Embedded image extraction from vectorial PDFs
       Phase 3: Photo / image ingestion as a new media type
  3. List exactly which files you will create or modify per phase, and why
  4. For each new model field, write the migration name and confirm it is
     nullable/backward-compatible with existing data
  5. Identify every decision point that requires a human answer before
     implementation can proceed — present them as explicit YES/NO or
     OPTION A/B questions, not open-ended discussion

  Ask for confirmation before planning any work that touches more than 3
  existing files outside the documents/ app.

────────────────────────────────────────────────────────────────────────────────
STEP 5 — FUTURE-PROOFING (do not implement, keep in mind)
────────────────────────────────────────────────────────────────────────────────

  - The correct production path for heavy OCR workloads is:
    POST → 202 + task_id → Celery worker → Channels WebSocket fan-out
  - Do not design the Phase 1 solution in a way that makes this migration
    harder. Specifically: isolate the GLMOCRService.analyze_document() call
    to a single dispatch point so it can be swapped for a .delay() call later
    without touching consumers or views.
  - Keep the WebSocket message schema defined in the spec — do not invent a
    different schema, even if it seems simpler, to avoid frontend churn.

────────────────────────────────────────────────────────────────────────────────

Strip all filler from your responses. If my spec is wrong, say so directly and
say why. Show audit results → challenge findings → revised plan, in that order.
No code until the plan is confirmed.


---

================================================================================
END OF SPEC
================================================================================
