# embeddings/tests/test_embedding_service.py
"""Tests for EmbeddingService — Ollama calls always mocked."""

from unittest.mock import patch

import pytest


@pytest.fixture
def mock_ollama_embeddings():
    """Patch OllamaEmbeddings to avoid real Ollama calls."""
    with patch("embeddings.services.embedding_service.OllamaEmbeddings") as MockCls:
        instance = MockCls.return_value
        instance.embed_query.return_value = [0.1] * 1024
        instance.embed_documents.return_value = [[0.1] * 1024, [0.2] * 1024]
        yield instance


class TestEmbeddingService:
    """Tests for EmbeddingService."""

    def test_init_uses_settings_defaults(self, mock_ollama_embeddings, settings):
        """EmbeddingService uses settings when no model/url provided."""
        from embeddings.services.embedding_service import EmbeddingService

        settings.OLLAMA_EMBED_MODEL = "mxbai-embed-large"
        settings.OLLAMA_HOST = "http://localhost:11434"

        svc = EmbeddingService()
        assert svc.model_name == "mxbai-embed-large"

    def test_embed_query_empty_string_returns_empty_list(self, mock_ollama_embeddings):
        """embed_query('') returns empty list without calling Ollama."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService()
        result = svc.embed_query("")
        assert result == []
        mock_ollama_embeddings.embed_query.assert_not_called()

    def test_embed_query_returns_vector(self, mock_ollama_embeddings):
        """embed_query returns list of floats from Ollama."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService()
        result = svc.embed_query("What is the fire rating of this wall?")
        assert len(result) == 1024
        mock_ollama_embeddings.embed_query.assert_called_once()

    def test_embed_documents_empty_list_returns_empty_list(self, mock_ollama_embeddings):
        """embed_documents([]) returns empty list without calling Ollama."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService()
        result = svc.embed_documents([])
        assert result == []
        mock_ollama_embeddings.embed_documents.assert_not_called()

    def test_embed_documents_returns_list_of_vectors(self, mock_ollama_embeddings):
        """embed_documents returns list of float vectors."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService()
        result = svc.embed_documents(["text 1", "text 2"])
        assert len(result) == 2
        mock_ollama_embeddings.embed_documents.assert_called_once()

    def test_embed_documents_batches_large_inputs(self, mock_ollama_embeddings, settings):
        """Long input lists are split into sub-batches to avoid Ollama 400s."""
        from embeddings.services.embedding_service import EmbeddingService

        settings.OLLAMA_EMBED_BATCH_SIZE = 16
        # Underlying client returns one vector per text it receives.
        mock_ollama_embeddings.embed_documents.side_effect = lambda batch: [
            [0.1] * 1024 for _ in batch
        ]

        svc = EmbeddingService()
        texts = [f"chunk {i}" for i in range(50)]
        result = svc.embed_documents(texts)

        assert len(result) == 50
        # ceil(50 / 16) = 4
        assert mock_ollama_embeddings.embed_documents.call_count == 4

    def test_model_name_property(self, mock_ollama_embeddings):
        """model_name property returns the configured model name."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService(model="custom-embed-model")
        assert svc.model_name == "custom-embed-model"

    def test_dimensions_property(self, mock_ollama_embeddings, settings):
        """dimensions property returns PGVECTOR_DIMENSIONS from settings."""
        from embeddings.services.embedding_service import EmbeddingService

        settings.PGVECTOR_DIMENSIONS = 1024
        svc = EmbeddingService()
        assert svc.dimensions == 1024

    def test_custom_base_url(self, mock_ollama_embeddings):
        """Custom base_url overrides the settings default."""
        from embeddings.services.embedding_service import EmbeddingService

        svc = EmbeddingService(base_url="http://custom-ollama:11434")
        assert svc._base_url == "http://custom-ollama:11434"
