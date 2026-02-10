"""
Embedding service — model-agnostic vector generation via Ollama.

Wraps langchain_ollama to provide a clean interface for embedding
text into vectors. Knows nothing about Django models; it just
converts strings to float lists.
"""

import logging
from typing import Optional

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
        model: Optional[str] = None,
        base_url: Optional[str] = None,
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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents. Used at indexing time."""
        if not texts:
            return []
        return self._client.embed_documents(texts)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return settings.PGVECTOR_DIMENSIONS