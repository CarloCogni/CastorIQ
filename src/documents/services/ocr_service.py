# documents/services/ocr_service.py
"""
GLM-OCR integration service.

Wraps the GLM-OCR model (via Ollama) as a system-level preprocessing layer.
This is NOT a user-facing LLM — it is configured at the environment level and
is invisible to UserLLMConfig / the Settings model picker.

Usage:
    svc = GLMOCRService()
    if svc.is_available():
        result = svc.analyze_document(document, force_all_pages=False)
"""

import base64
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import httpx
from django.conf import settings
from django.utils import timezone
from langchain_text_splitters import RecursiveCharacterTextSplitter

from documents.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

# ── Result dataclass ───────────────────────────────────────────────────────────


@dataclass
class OCRResult:
    """Summary of a completed OCR run."""

    pages_processed: int = 0
    chunks_created: int = 0
    errors: list[dict] = field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────────────


class GLMOCRService:
    """
    System-level OCR service using GLM-OCR via Ollama.

    The Ollama HTTP client is injected for testability — unit tests pass a
    mock client without needing a running Ollama instance.

    Architecture note: analyze_document() is the single dispatch point.
    To move to Celery in production, replace the direct call with .delay()
    at the call site without touching this class.
    """

    def __init__(self, ollama_client: httpx.Client | None = None) -> None:
        """
        Args:
            ollama_client: Pre-configured httpx.Client. Defaults to one built
                           from settings.GLM_OCR_OLLAMA_URL.
        """
        self._client = ollama_client or httpx.Client(
            base_url=getattr(settings, "GLM_OCR_OLLAMA_URL", "http://localhost:11434"),
            timeout=120.0,
        )
        self._model = getattr(settings, "GLM_OCR_MODEL", "glm-ocr:latest")
        self._dpi = getattr(settings, "GLM_OCR_PAGE_DPI", 150)
        self._threshold = getattr(settings, "GLM_OCR_TEXT_DENSITY_THRESHOLD", 50)
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """
        Return True if the GLM-OCR model is pulled and reachable on Ollama.

        Pings /api/tags and checks that self._model appears in the response.
        """
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
            available = any(self._model in m for m in models)
            if not available:
                logger.warning(
                    "GLM-OCR model '%s' not found in Ollama. Run: ollama pull %s",
                    self._model,
                    self._model,
                )
            return available
        except httpx.HTTPError as exc:
            logger.error("Ollama not reachable for GLM-OCR availability check: %s", exc)
            return False

    def process_image(self, image_bytes: bytes) -> str:
        """
        Send a single image to GLM-OCR and return the extracted Markdown string.

        This is the atomic unit — standalone and reusable (e.g. future Use Case 3E).

        Args:
            image_bytes: Raw PNG/JPEG bytes of the image to OCR.

        Returns:
            Extracted text as Markdown. Empty string on model error.

        Raises:
            httpx.HTTPError: If the Ollama API call fails at the transport level.
        """
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": "Extract all text from this image. Output structured Markdown.",
                    "images": [b64_image],
                }
            ],
            "stream": False,
        }
        response = self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    def render_page_to_image(self, page: fitz.Page) -> bytes:
        """
        Render a PDF page to PNG bytes at the configured DPI.

        If the resulting image exceeds GLM_OCR_MAX_IMAGE_BYTES (default 1.5 MB),
        it is scaled down proportionally and re-rendered once to stay within the
        model's context window.

        Args:
            page: A fitz.Page object from an open fitz.Document.

        Returns:
            PNG image bytes.
        """
        mat = fitz.Matrix(self._dpi / 72, self._dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        max_bytes = getattr(settings, "GLM_OCR_MAX_IMAGE_BYTES", 1_500_000)
        if len(img_bytes) > max_bytes:
            ratio = math.sqrt(max_bytes / len(img_bytes))
            reduced_dpi = max(50, int(self._dpi * ratio))
            mat2 = fitz.Matrix(reduced_dpi / 72, reduced_dpi / 72)
            img_bytes = page.get_pixmap(matrix=mat2).tobytes("png")
            logger.debug(
                "Page %d: image %d bytes exceeds limit, re-rendered at %d DPI",
                page.number + 1,
                len(pix.tobytes("png")),
                reduced_dpi,
            )

        return img_bytes

    def should_ocr_page(self, page: fitz.Page) -> bool:
        """
        Return True when a page has fewer extractable characters than the threshold.

        Uses fitz text extraction — independent of the LangChain pipeline.

        Args:
            page: A fitz.Page object.
        """
        text = page.get_text("text")
        return len(text.strip()) < self._threshold

    def analyze_document(
        self,
        document: Document,
        force_all_pages: bool = False,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OCRResult:
        """
        Run OCR on a document and persist the resulting chunks.

        This is the single dispatch point for both auto (3A) and manual (3B) flows.
        Replace the call with .delay() at the call site to migrate to Celery.

        Args:
            document:          The Document model instance to process.
            force_all_pages:   True → process every page (manual trigger 3B).
                               False → only process pages below the density threshold (auto 3A).
            progress_callback: Optional callable(page_num, total, status) for streaming.
                               Called with status="processing" before and "done" after each page.

        Returns:
            OCRResult with counts and any per-page errors.
        """
        result = OCRResult()

        document.ocr_status = Document.OcrStatus.PROCESSING
        document.save(update_fields=["ocr_status"])

        try:
            file_path = document.file.path
            pdf = fitz.open(file_path)
            total_pages = len(pdf)

            logger.info(
                "OCR starting: document=%s pages=%d force_all=%s",
                document.name,
                total_pages,
                force_all_pages,
            )

            new_chunks: list[DocumentChunk] = []
            # Remove existing OCR chunks so re-runs replace rather than duplicate.
            document.chunks.filter(chunk_source=DocumentChunk.ChunkSource.GLM_OCR).delete()
            # Chunk index offset: append after existing text-parser chunks.
            existing_count = document.chunks.count()
            chunk_index_offset = existing_count

            for page_num, page in enumerate(pdf, start=1):
                if not force_all_pages and not self.should_ocr_page(page):
                    continue

                if progress_callback:
                    progress_callback(page_num, total_pages, "processing")

                try:
                    image_bytes = self.render_page_to_image(page)
                    markdown_text = self.process_image(image_bytes)
                except httpx.HTTPError as exc:
                    error_msg = str(exc)
                    logger.error(
                        "OCR failed on page %d of %s: %s", page_num, document.name, error_msg
                    )
                    result.errors.append({"page": page_num, "error": error_msg})
                    if progress_callback:
                        progress_callback(page_num, total_pages, "error")
                    continue

                if markdown_text.strip():
                    for split_text in self._text_splitter.split_text(markdown_text):
                        new_chunks.append(
                            DocumentChunk(
                                document=document,
                                content=split_text,
                                chunk_index=chunk_index_offset + len(new_chunks),
                                page_number=page_num,
                                chunk_source=DocumentChunk.ChunkSource.GLM_OCR,
                                extracted_from_image=True,
                            )
                        )
                    result.pages_processed += 1

                if progress_callback:
                    progress_callback(page_num, total_pages, "done")

            pdf.close()

            if new_chunks:
                self._embed_and_save_chunks(new_chunks)

            result.chunks_created = len(new_chunks)

            document.ocr_status = Document.OcrStatus.DONE
            document.ocr_processed_at = timezone.now()
            document.ocr_page_count = result.pages_processed
            document.ocr_engine = self._model
            document.save(
                update_fields=["ocr_status", "ocr_processed_at", "ocr_page_count", "ocr_engine"]
            )

            logger.info(
                "OCR complete: document=%s pages_processed=%d chunks_created=%d errors=%d",
                document.name,
                result.pages_processed,
                result.chunks_created,
                len(result.errors),
            )

        except Exception as exc:
            logger.exception("OCR failed for document %s: %s", document.name, exc)
            document.ocr_status = Document.OcrStatus.FAILED
            document.save(update_fields=["ocr_status"])
            raise

        return result

    # ── Private helpers ────────────────────────────────────────────────────────

    def _embed_and_save_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Embed and bulk-create a list of DocumentChunk instances."""
        from embeddings.services.embedding_service import EmbeddingService

        texts = [c.content for c in chunks]
        embedding_svc = EmbeddingService()
        vectors = embedding_svc.embed_documents(texts)

        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector

        DocumentChunk.objects.bulk_create(chunks)
        logger.debug("Saved %d OCR chunks to DB.", len(chunks))
