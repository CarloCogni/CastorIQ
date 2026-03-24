# documents/tests/test_ocr_service.py
"""
Unit tests for GLMOCRService.

All Ollama HTTP calls are intercepted via httpx.MockTransport — no running
Ollama instance is required. DB tests use @pytest.mark.django_db only where
model persistence is actually exercised.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from documents.models import Document, DocumentChunk
from documents.services.ocr_service import GLMOCRService
from documents.tests.factories import DocumentChunkFactory, DocumentFactory

# ── Helpers ────────────────────────────────────────────────────────────────────


_BASE_URL = "http://localhost:11434"


def _make_ollama_client(response_body: dict, status_code: int = 200) -> httpx.Client:
    """Return an httpx.Client backed by a mock transport with a canned response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_body)

    return httpx.Client(base_url=_BASE_URL, transport=httpx.MockTransport(handler))


def _make_tags_client(model_name: str = "glm-ocr:latest") -> httpx.Client:
    """Return a client whose /api/tags lists the given model."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": model_name}]})
        return httpx.Response(404, json={})

    return httpx.Client(base_url=_BASE_URL, transport=httpx.MockTransport(handler))


def _make_chat_client(markdown: str) -> httpx.Client:
    """Return a client that answers /api/chat with canned Markdown."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": markdown}})
        return httpx.Response(404, json={})

    return httpx.Client(base_url=_BASE_URL, transport=httpx.MockTransport(handler))


# ── is_available ───────────────────────────────────────────────────────────────


def test_is_available_returns_true_when_model_listed():
    """is_available() returns True when Ollama lists the configured model."""
    svc = GLMOCRService(ollama_client=_make_tags_client("glm-ocr:latest"))
    assert svc.is_available() is True


def test_is_available_returns_false_when_model_absent():
    """is_available() returns False when the model is not in Ollama's tag list."""
    svc = GLMOCRService(ollama_client=_make_tags_client("some-other-model"))
    assert svc.is_available() is False


def test_is_available_returns_false_on_connection_error():
    """is_available() returns False (does not raise) when Ollama is unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    svc = GLMOCRService(ollama_client=client)
    assert svc.is_available() is False


# ── process_image ──────────────────────────────────────────────────────────────


def test_process_image_returns_markdown_from_ollama():
    """process_image() extracts the content field from Ollama's chat response."""
    expected = "# Header\n\nSome extracted text."
    svc = GLMOCRService(ollama_client=_make_chat_client(expected))
    image_bytes = b"\x89PNG fake image bytes"
    result = svc.process_image(image_bytes)
    assert result == expected


def test_process_image_returns_empty_string_when_content_missing():
    """process_image() returns '' when Ollama response has no content field."""
    client = _make_ollama_client({"message": {}})
    svc = GLMOCRService(ollama_client=client)
    assert svc.process_image(b"img") == ""


def test_process_image_raises_on_http_error():
    """process_image() propagates httpx.HTTPStatusError on non-2xx responses."""
    client = _make_ollama_client({"error": "model not found"}, status_code=404)
    svc = GLMOCRService(ollama_client=client)
    with pytest.raises(httpx.HTTPStatusError):
        svc.process_image(b"img")


# ── should_ocr_page ────────────────────────────────────────────────────────────


def test_should_ocr_page_true_when_text_below_threshold():
    """should_ocr_page() returns True when the page has fewer chars than the threshold."""
    svc = GLMOCRService(ollama_client=MagicMock())
    svc._threshold = 50

    mock_page = MagicMock()
    mock_page.get_text.return_value = "short"  # 5 chars < 50
    assert svc.should_ocr_page(mock_page) is True


def test_should_ocr_page_false_when_text_above_threshold():
    """should_ocr_page() returns False when the page has sufficient text."""
    svc = GLMOCRService(ollama_client=MagicMock())
    svc._threshold = 50

    mock_page = MagicMock()
    mock_page.get_text.return_value = "x" * 100  # 100 chars > 50
    assert svc.should_ocr_page(mock_page) is False


# ── analyze_document ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_analyze_document_creates_glm_ocr_chunks():
    """analyze_document() persists DocumentChunk rows with chunk_source=GLM_OCR."""
    document = DocumentFactory(status="completed", has_visual_content=True)
    markdown = "## Extracted heading\n\nBody text from scanned page."

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 1
    mock_page = MagicMock()
    mock_page.get_text.return_value = ""  # zero text → should OCR
    mock_pdf.__iter__ = lambda self: iter([mock_page])

    svc = GLMOCRService(ollama_client=_make_chat_client(markdown))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            return_value=[[0.1] * 1024],
        ),
    ):
        result = svc.analyze_document(document, force_all_pages=True)

    assert result.chunks_created == 1
    assert result.pages_processed == 1
    assert len(result.errors) == 0

    chunk = DocumentChunk.objects.get(document=document)
    assert chunk.chunk_source == DocumentChunk.ChunkSource.GLM_OCR
    assert chunk.extracted_from_image is True
    assert chunk.content == markdown


@pytest.mark.django_db
def test_analyze_document_skips_text_rich_pages_in_auto_mode():
    """In auto mode (force_all_pages=False), text-rich pages are not OCR'd."""
    document = DocumentFactory(status="completed")

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 2

    rich_page = MagicMock()
    rich_page.get_text.return_value = "x" * 200  # above threshold

    sparse_page = MagicMock()
    sparse_page.get_text.return_value = "y" * 10  # below threshold

    mock_pdf.__iter__ = lambda self: iter([rich_page, sparse_page])

    svc = GLMOCRService(ollama_client=_make_chat_client("OCR text"))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            return_value=[[0.1] * 1024],
        ),
    ):
        result = svc.analyze_document(document, force_all_pages=False)

    # Only the sparse page should be processed
    assert result.pages_processed == 1


@pytest.mark.django_db
def test_analyze_document_records_page_errors_without_failing():
    """Per-page HTTP errors are recorded in OCRResult.errors without aborting the run."""
    document = DocumentFactory(status="completed")

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 1
    mock_page = MagicMock()
    mock_page.get_text.return_value = ""
    mock_pdf.__iter__ = lambda self: iter([mock_page])

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(500, json={"error": "model error"})
        return httpx.Response(404, json={})

    client = httpx.Client(base_url=_BASE_URL, transport=httpx.MockTransport(failing_handler))
    svc = GLMOCRService(ollama_client=client)
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
    ):
        result = svc.analyze_document(document, force_all_pages=True)

    assert result.pages_processed == 0
    assert len(result.errors) == 1
    assert result.errors[0]["page"] == 1


@pytest.mark.django_db
def test_analyze_document_sets_ocr_status_done_on_success():
    """Document.ocr_status is set to DONE after a successful run."""
    document = DocumentFactory(status="completed")

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 1
    mock_page = MagicMock()
    mock_page.get_text.return_value = ""
    mock_pdf.__iter__ = lambda self: iter([mock_page])

    svc = GLMOCRService(ollama_client=_make_chat_client("text"))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            return_value=[[0.1] * 1024],
        ),
    ):
        svc.analyze_document(document, force_all_pages=True)

    document.refresh_from_db()
    assert document.ocr_status == Document.OcrStatus.DONE
    assert document.ocr_engine == svc._model
    assert document.ocr_processed_at is not None


@pytest.mark.django_db
def test_analyze_document_progress_callback_called_per_page():
    """progress_callback receives (page_num, total, status) for each page."""
    document = DocumentFactory(status="completed")

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 2
    page1 = MagicMock()
    page1.get_text.return_value = ""
    page2 = MagicMock()
    page2.get_text.return_value = ""
    mock_pdf.__iter__ = lambda self: iter([page1, page2])

    calls: list[tuple] = []

    def callback(page_num: int, total: int, status: str) -> None:
        calls.append((page_num, total, status))

    svc = GLMOCRService(ollama_client=_make_chat_client("text"))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            return_value=[[0.1] * 1024] * 2,
        ),
    ):
        svc.analyze_document(document, force_all_pages=True, progress_callback=callback)

    # Each page triggers "processing" then "done"
    assert len(calls) == 4
    assert calls[0] == (1, 2, "processing")
    assert calls[1] == (1, 2, "done")
    assert calls[2] == (2, 2, "processing")
    assert calls[3] == (2, 2, "done")


@pytest.mark.django_db
def test_analyze_document_rerun_deletes_old_ocr_chunks():
    """Re-running analyze_document() replaces existing GLM_OCR chunks, not duplicates them."""
    document = DocumentFactory(status="completed")
    # Pre-create 2 stale OCR chunks from a previous run.
    DocumentChunkFactory(
        document=document,
        chunk_index=0,
        chunk_source=DocumentChunk.ChunkSource.GLM_OCR,
    )
    DocumentChunkFactory(
        document=document,
        chunk_index=1,
        chunk_source=DocumentChunk.ChunkSource.GLM_OCR,
    )
    assert document.chunks.filter(chunk_source=DocumentChunk.ChunkSource.GLM_OCR).count() == 2

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 1
    mock_page = MagicMock()
    mock_page.get_text.return_value = ""
    mock_pdf.__iter__ = lambda self: iter([mock_page])

    svc = GLMOCRService(ollama_client=_make_chat_client("Fresh OCR text"))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            return_value=[[0.1] * 1024],
        ),
    ):
        result = svc.analyze_document(document, force_all_pages=True)

    assert result.chunks_created == 1
    # Old chunks gone, exactly 1 new one.
    assert document.chunks.filter(chunk_source=DocumentChunk.ChunkSource.GLM_OCR).count() == 1


@pytest.mark.django_db
def test_analyze_document_long_content_creates_multiple_chunks():
    """OCR Markdown exceeding 1000 chars is split into multiple DocumentChunk rows."""
    document = DocumentFactory(status="completed")
    # 1200 chars — exceeds chunk_size=1000, forces at least 2 splits.
    long_markdown = "word " * 240

    mock_pdf = MagicMock()
    mock_pdf.__len__ = lambda self: 1
    mock_page = MagicMock()
    mock_page.get_text.return_value = ""
    mock_pdf.__iter__ = lambda self: iter([mock_page])

    svc = GLMOCRService(ollama_client=_make_chat_client(long_markdown))
    svc._threshold = 50

    with (
        patch("documents.services.ocr_service.fitz.open", return_value=mock_pdf),
        patch(
            "documents.services.ocr_service.GLMOCRService.render_page_to_image", return_value=b"img"
        ),
        patch(
            "embeddings.services.embedding_service.EmbeddingService.embed_documents",
            side_effect=lambda texts: [[0.1] * 1024 for _ in texts],
        ),
    ):
        result = svc.analyze_document(document, force_all_pages=True)

    chunks = list(document.chunks.filter(chunk_source=DocumentChunk.ChunkSource.GLM_OCR))
    assert len(chunks) > 1, "Long OCR output should produce more than one chunk"
    assert result.chunks_created == len(chunks)
    # All sub-chunks share the source page.
    assert all(c.page_number == 1 for c in chunks)
