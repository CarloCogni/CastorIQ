# OCR Pipeline

GLM-OCR is a transparent preprocessing layer in Castor's document ingestion pipeline. It extracts structured text from image-dominant and scanned PDF pages that conventional text parsers cannot handle, feeding the result into the existing chunking and embedding pipeline as if it were native text.

---

## Problem

Castor's primary text extractor (LangChain `PyPDFLoader` / pypdf) silently fails on two common document types in AEC:

1. **Scanned PDFs** — image-only pages with no extractable text layer. Previously, these caused a hard pipeline failure and `status=FAILED`.
2. **Image-dominant pages in vectorial PDFs** — pages where meaningful content lives in embedded raster images: stamped drawings, data tables rendered as images, annotated figures.

When this happens, the vector store contains no useful embeddings for those pages. RAG answers degrade silently, with no indication to the user that content was missed. In AEC, scanned specs, stamped drawings, and image-heavy submittals are not edge cases — they are the norm.

---

## Solution

GLM-OCR (`glm-ocr:latest`, Ollama) runs as a **second-pass preprocessing layer**. It renders PDF pages to images and sends them to the model, which returns structured Markdown. That Markdown enters the existing chunking and embedding pipeline unchanged.

The existing pipeline's happy path — vectorial PDFs with good text — is untouched.

### Model Details

| Property | Value |
|---|---|
| Model tag | `glm-ocr:latest` |
| Architecture | 0.4B CogViT visual encoder + 0.5B GLM language decoder |
| Parameters | ~0.9B total |
| Output | Structured Markdown (preserves tables, formulas, code blocks, layout) |
| Benchmark | 94.62 OmniDocBench V1.5 (rank #1 at release) |
| Throughput | ~1.86 PDF pages/sec at single concurrency |
| API | Ollama `/api/chat` with base64 image payload |

---

## Architecture

```
                    STANDARD PATH (vectorial PDFs)
                    ───────────────────────────────

  PDF upload → PyPDFLoader → Chunk → Embed → pgvector
                                ↑
                      chunk_source = "text_parser"


                    OCR PATH (scanned / image-heavy pages)
                    ──────────────────────────────────────

  PDF upload → PyPDFLoader → zero/sparse text detected
                    │
                    ▼
            has_visual_content = True
            status = COMPLETED, chunk_count = 0
                    │
                    ▼ (triggered by frontend after upload response)
            GLMOCRService.analyze_document()
              ├── fitz.open(pdf)
              ├── Per page: should_ocr_page()? (chars < threshold)
              │     ├── render_page_to_image() → PNG bytes
              │     └── process_image() → POST /api/chat → Markdown
              └── Embed Markdown → DocumentChunk(chunk_source="glm_ocr")
                    │
                    ▼
              ocr_status = DONE
              ocr_engine, ocr_processed_at, ocr_page_count saved
```

---

## Two Interaction Modes

Both modes call the same `GLMOCRService.analyze_document()`. The only difference is the invocation path and the `force_all_pages` flag.

### 3A — Auto Fallback

Triggered automatically when a PDF upload produces zero or sparse text. Requires both `GLM_OCR_ENABLED=True` and `GLM_OCR_AUTO_TRIGGER=True`.

```
User uploads PDF
   └── DocumentProcessor.process() runs (synchronous, existing pipeline)
         ├── Good text extracted → chunks created normally → done
         │
         └── Zero / sparse text → has_visual_content = True, status = COMPLETED
               └── Upload response includes: { "needs_ocr": true, "ocr_ws_url": "..." }
                     └── Frontend opens OCRConsumer WebSocket automatically
                           └── analyze_document(force_all_pages=False)
                                 Only processes pages below the density threshold
```

The user sees a toast notification and live page-by-page progress. No explicit action required.

### 3B — Manual Trigger

Available at any time on the document detail page, regardless of auto-trigger setting. The user explicitly requests OCR (e.g., to improve coverage on a document that was partially extracted).

```
User clicks "Run OCR"
   └── Frontend opens OCRConsumer WebSocket
         └── analyze_document(force_all_pages=True)
               Processes ALL pages
               GLM_OCR chunks appended alongside existing TEXT_PARSER chunks
```

---

## Configuration

All settings in `config/settings/base.py`, overridable via environment variables.

| Setting | Default | Description |
|---|---|---|
| `GLM_OCR_ENABLED` | `False` | Master switch. `False` = entire subsystem is a no-op. |
| `GLM_OCR_MODEL` | `glm-ocr:latest` | Ollama model tag. |
| `GLM_OCR_OLLAMA_URL` | `OLLAMA_HOST` | Ollama base URL. Defaults to the shared Ollama instance. |
| `GLM_OCR_TEXT_DENSITY_THRESHOLD` | `50` | Chars per page below which auto-OCR fires on a page. |
| `GLM_OCR_AUTO_TRIGGER` | `True` | If `False`, only manual trigger (3B) is available. |
| `GLM_OCR_MAX_PAGES_AUTO` | `100` | Safety cap on pages processed in auto mode. |
| `GLM_OCR_PAGE_DPI` | `150` | DPI for page-to-image rendering. Higher = better quality, slower. |

The text density threshold (50 chars/page) is a starting point. AEC projects vary widely — tune it against a local sample set if auto-trigger fires too aggressively or not enough.

---

## Data Model

### Document (new fields)

| Field | Type | Purpose |
|---|---|---|
| `has_visual_content` | BooleanField | Set during ingestion when zero/sparse text is detected |
| `ocr_status` | CharField | `none / pending / processing / done / failed` |
| `ocr_processed_at` | DateTimeField | Timestamp of last completed OCR run |
| `ocr_page_count` | PositiveIntegerField | Number of pages processed by OCR |
| `ocr_engine` | CharField | Engine used, e.g. `glm-ocr:latest` |

`DocumentType` enum gains `SCANNED_PDF` — set automatically when initial extraction produces zero text.

### DocumentChunk (new fields)

| Field | Type | Purpose |
|---|---|---|
| `chunk_source` | CharField | `text_parser / glm_ocr / hybrid` — provenance of this chunk |
| `extracted_from_image` | BooleanField | True when the chunk was produced by OCR from a raster image |

`page_number` already existed and is populated by both the text parser and the OCR service.

---

## WebSocket Protocol

The `OCRConsumer` streams page-level progress at `ws/projects/<pid>/documents/<did>/ocr/`.

```json
// Sent before each page is processed
{ "type": "ocr_progress", "page": 3, "total": 12, "status": "processing" }

// Sent after each page completes
{ "type": "ocr_progress", "page": 3, "total": 12, "status": "done" }

// Non-fatal per-page error (processing continues)
{ "type": "ocr_error", "page": 5, "error": "model timeout" }

// All pages done
{ "type": "ocr_complete", "chunks_created": 47, "pages_ocrd": 8 }

// Fatal error (model not available, document not found, etc.)
{ "type": "ocr_failed", "error": "GLM-OCR model not available" }

// Always sent last
{ "type": "done" }
```

---

## Operational Setup

```bash
# 1. Enable the feature flag in .env
GLM_OCR_ENABLED=True
GLM_OCR_AUTO_TRIGGER=True

# 2. Pull the model (one-time)
ollama pull glm-ocr

# 3. Verify it is available
ollama list   # should show glm-ocr:latest

# 4. Verify from Django
# GLMOCRService().is_available() → True
```

After uploading a scanned PDF, verify in the Django shell:

```python
from documents.models import Document, DocumentChunk

doc = Document.objects.get(name="your_scanned_doc.pdf")
print(doc.has_visual_content)  # True
print(doc.ocr_status)          # "done"
print(doc.ocr_page_count)      # number of pages OCR'd

chunks = doc.chunks.filter(chunk_source="glm_ocr")
print(chunks.count())          # > 0
print(chunks.first().content)  # Markdown extracted from page image
```

---

## RAG Integration

OCR-extracted chunks flow into `DocumentChunk` with `chunk_source="glm_ocr"`. From there, they enter the same embedding and vector store pipeline as any other chunk. **No changes to RAG retrieval or prompt assembly.**

The `chunk_source` field is available to RAV (Retrieval-Augmented Verification) for optional provenance surfacing: *"This requirement was found in an OCR-extracted section — confirm against the original document."*

---

## Out of Scope

| Use Case | Status | Reason |
|---|---|---|
| 3C — Scanned PDF → searchable PDF export | Excluded | No RAG value |
| 3D — Photo/image ingestion as new media type | Phase 3 | Requires new upload handling |
| 3E — Embedded image extraction from vectorial PDFs | Phase 2 | Reuses `process_image()` |
| Handwriting recognition | Excluded | GLM-OCR accuracy unreliable on handwriting |
| Cloud OCR fallback | Excluded | Violates local-first constraint |
| Celery task queue | Future | `analyze_document()` is the single swap point — replace call with `.delay()` |
