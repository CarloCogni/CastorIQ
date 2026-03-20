# embeddings/tests/test_generate_embeddings_command.py
"""Tests for the generate_embeddings management command — Ollama always mocked."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from ifc_processor.models import IFCEntity
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_embed_service():
    """Patches EmbeddingService to return predictable fake vectors."""
    fake_vectors = [[0.1] * 1024]

    with patch("embeddings.management.commands.generate_embeddings.EmbeddingService") as mock_cls:
        instance = mock_cls.return_value
        instance.embed_documents.return_value = fake_vectors
        yield instance


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGenerateEmbeddingsCommand:
    """Tests for the generate_embeddings management command."""

    def test_command_with_no_entities_exits_early(self, mock_embed_service):
        """When no entities need embeddings, command outputs a warning and returns."""
        out = StringIO()
        call_command("generate_embeddings", stdout=out)

        output = out.getvalue()
        assert "No entities" in output or output.strip() == "" or "warning" in output.lower()
        # Embedding service should NOT be called with no entities
        mock_embed_service.embed_documents.assert_not_called()

    def test_command_embeds_entities_without_embeddings(self, mock_embed_service):
        """Entities with embedding=None are processed and embeddings are assigned."""
        ifc_file = IFCFileFactory(status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            description="An external load-bearing IfcWall with EI60 fire rating.",
            embedding=None,
        )

        mock_embed_service.embed_documents.return_value = [[0.1] * 1024]

        call_command("generate_embeddings")

        entity.refresh_from_db()
        assert entity.embedding is not None

    def test_command_skips_already_embedded_entities(self, mock_embed_service):
        """Entities that already have embeddings are NOT re-processed by default."""
        ifc_file = IFCFileFactory(status="completed")
        IFCEntityFactory(
            ifc_file=ifc_file,
            description="Wall with existing embedding.",
            embedding=[0.5] * 1024,
        )

        out = StringIO()
        call_command("generate_embeddings", stdout=out)

        # No entities need embeddings → embed_documents never called
        mock_embed_service.embed_documents.assert_not_called()

    def test_command_force_flag_re_embeds_existing(self, mock_embed_service):
        """--force flag causes already-embedded entities to be re-processed."""
        ifc_file = IFCFileFactory(status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            description="Wall with stale embedding.",
            embedding=[0.5] * 1024,
        )

        mock_embed_service.embed_documents.return_value = [[0.9] * 1024]

        call_command("generate_embeddings", force=True)

        assert mock_embed_service.embed_documents.call_count >= 1

    def test_command_project_filter_limits_to_project(self, mock_embed_service):
        """--project flag only processes entities for that project."""
        ifc_file_1 = IFCFileFactory(status="completed")
        ifc_file_2 = IFCFileFactory(status="completed")

        entity_1 = IFCEntityFactory(
            ifc_file=ifc_file_1, description="Wall in project 1.", embedding=None
        )
        entity_2 = IFCEntityFactory(
            ifc_file=ifc_file_2, description="Wall in project 2.", embedding=None
        )

        mock_embed_service.embed_documents.return_value = [[0.1] * 1024]

        project_1_id = str(ifc_file_1.project.id)
        call_command("generate_embeddings", project=project_1_id)

        entity_1.refresh_from_db()
        entity_2.refresh_from_db()

        # Only entity_1 should have an embedding now
        assert entity_1.embedding is not None
        assert entity_2.embedding is None

    def test_command_skips_entities_with_empty_description(self, mock_embed_service):
        """Entities with empty/blank descriptions are skipped (not sent to Ollama)."""
        ifc_file = IFCFileFactory(status="completed")
        IFCEntityFactory(ifc_file=ifc_file, description="", embedding=None)
        IFCEntityFactory(ifc_file=ifc_file, description="  ", embedding=None)

        out = StringIO()
        call_command("generate_embeddings", stdout=out)

        # embed_documents should never be called for empty descriptions
        mock_embed_service.embed_documents.assert_not_called()

    def test_command_batch_failure_does_not_crash_command(self, mock_embed_service):
        """If EmbeddingService raises, command logs error and continues."""
        ifc_file = IFCFileFactory(status="completed")
        IFCEntityFactory(ifc_file=ifc_file, description="Wall A.", embedding=None)

        mock_embed_service.embed_documents.side_effect = RuntimeError("Ollama unreachable")

        out = StringIO()
        # Should NOT raise — command handles batch errors gracefully
        call_command("generate_embeddings", stdout=out)
