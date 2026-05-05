# embeddings/services/embedding_service.py
"""
Embedding service — model-agnostic vector generation via Ollama.

Wraps langchain_ollama to provide a clean interface for embedding
text into vectors. Knows nothing about Django models; it just
converts strings to float lists.
"""

import logging

from django.conf import settings
from langchain_ollama import OllamaEmbeddings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Generates vector embeddings using a local Ollama instance.

    Usage:
        service = EmbeddingService()
        vector = service.embed_query("What is this wall made of?")
        vectors = service.embed_documents(["Wall A description", "Door B description"])
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._model = model or settings.OLLAMA_EMBED_MODEL
        self._base_url = base_url or settings.OLLAMA_HOST

        self._client = OllamaEmbeddings(
            model=self._model,
            base_url=self._base_url,
        )

        logger.debug(f"EmbeddingService initialized: model={self._model}, url={self._base_url}")

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. Used at search time."""
        if not text:
            return []
        return self._client.embed_query(text)

    def embed_documents(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        """Embed a batch of documents. Used at indexing time.

        Splits the input into sub-batches before delegating to the Ollama client.
        A single oversized HTTP request to Ollama's /api/embeddings endpoint
        returns 400 once the body exceeds its internal limits, which surfaces
        as a generic ingestion failure for long PDFs.
        """
        if not texts:
            return []
        size = batch_size if batch_size is not None else settings.OLLAMA_EMBED_BATCH_SIZE
        if size <= 0:
            size = len(texts)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), size):
            vectors.extend(self._client.embed_documents(texts[i : i + size]))
        return vectors

    @property
    def model_name(self) -> str:
        """The Ollama embedding model name."""
        return self._model

    @property
    def dimensions(self) -> int:
        """Vector dimensionality from settings (e.g. 1024)."""
        return settings.PGVECTOR_DIMENSIONS
