# chat/tests/test_rag_service.py
"""Tests for RAGService — LLM and embedding service always mocked."""

from unittest.mock import MagicMock, patch

import pytest

from chat.services.rag_service import RAGService
from chat.tests.factories import ChatSessionFactory, MessageFactory
from environments.tests.factories import ProjectFactory, UserFactory

FAKE_VECTOR = [0.1] * 1024


@pytest.fixture
def mock_llm():
    """A mock LLM that returns a canned answer string."""
    llm = MagicMock()
    response = MagicMock()
    response.content = "The fire rating is EI60 according to the IFC model."
    # chain.invoke returns a string via StrOutputParser, but we patch at the chain level
    llm.invoke.return_value = response
    return llm


@pytest.fixture
def rag_service(mock_llm):
    """RAGService with LLM and EmbeddingService mocked at construction time."""
    with patch("chat.services.rag_service.get_llm", return_value=mock_llm):
        with patch("chat.services.rag_service.resolve_model_name", return_value="llama3.1:8b"):
            with patch("chat.services.rag_service.EmbeddingService") as mock_embed_cls:
                mock_embed_instance = mock_embed_cls.return_value
                mock_embed_instance.embed_query.return_value = FAKE_VECTOR
                service = RAGService(user=None)
                service._mock_embed = mock_embed_instance
                yield service


@pytest.mark.django_db
class TestGenerateAnswer:
    """Tests for RAGService.generate_answer() — the public entry point."""

    def test_generate_answer_empty_project_returns_graceful_response(self, rag_service):
        """Empty project with no entities/docs returns a valid (answer, [], float) tuple."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        with patch.object(
            rag_service,
            "_generate_response",
            return_value=("No relevant excerpts found.", 5.0),
        ):
            answer, context_items, utilization = rag_service.generate_answer(
                project, session, "What are the fire ratings?"
            )

        assert isinstance(answer, str)
        assert isinstance(context_items, list)
        assert isinstance(utilization, float)

    def test_generate_answer_returns_three_tuple(self, rag_service):
        """generate_answer() always returns (str, list, float)."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        with patch.object(
            rag_service,
            "_retrieve_vector_context",
            return_value=[],
        ):
            with patch.object(
                rag_service,
                "_generate_response",
                return_value=("Test answer.", 12.5),
            ):
                answer, context_items, utilization = rag_service.generate_answer(
                    project, session, "Tell me about the walls"
                )

        assert isinstance(answer, str)
        assert isinstance(context_items, list)
        assert isinstance(utilization, float)
        assert utilization == 12.5

    def test_generate_answer_summary_triggers_summary_retrieval(self, rag_service):
        """Summary keywords route to _retrieve_summary_context, not vector search."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        with patch.object(
            rag_service, "_retrieve_summary_context", return_value=[]
        ) as mock_summary:
            with patch.object(
                rag_service,
                "_generate_response",
                return_value=("Summary answer.", 10.0),
            ):
                rag_service.generate_answer(project, session, "summarize this document")

        mock_summary.assert_called_once_with(project)

    def test_generate_answer_ifc_scope_skips_summary_retrieval(self, rag_service):
        """scope='ifc' forces vector search even for summary keywords."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        with patch.object(rag_service, "_retrieve_vector_context", return_value=[]) as mock_vector:
            with patch.object(
                rag_service,
                "_generate_response",
                return_value=("IFC answer.", 8.0),
            ):
                rag_service.generate_answer(
                    project, session, "summarize this document", scope="ifc"
                )

        mock_vector.assert_called_once()

    def test_generate_answer_uses_vector_context_for_specific_query(self, rag_service):
        """Non-summary query uses vector context retrieval."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        with patch.object(rag_service, "_retrieve_vector_context", return_value=[]) as mock_vector:
            with patch.object(
                rag_service,
                "_generate_response",
                return_value=("Answer.", 15.0),
            ):
                rag_service.generate_answer(
                    project, session, "What is the fire rating of Wall-001?"
                )

        mock_vector.assert_called_once()


@pytest.mark.django_db
class TestFormatContext:
    """Tests for the context formatting helpers."""

    def test_format_context_empty_list_returns_no_excerpts(self, rag_service):
        """Empty context list returns sentinel string."""
        result = rag_service._format_context([])
        assert result == "No relevant excerpts found."

    def test_format_context_ifc_entity_includes_name_and_type(self, rag_service):
        """IFC entity items are formatted with name and ifc_type."""
        entity = MagicMock()
        entity.__class__.__name__ = "IFCEntity"
        entity.name = "Wall-001"
        entity.ifc_type = "IfcWall"
        entity.building_storey = "Level 1"
        entity.description = "External load-bearing wall with EI60 fire rating."

        # Patch isinstance check inside _format_context
        from ifc_processor.models import IFCEntity

        real_entity = MagicMock(spec=IFCEntity)
        real_entity.name = "Wall-001"
        real_entity.ifc_type = "IfcWall"
        real_entity.building_storey = "Level 1"
        real_entity.description = "External load-bearing wall."

        result = rag_service._format_context([real_entity])
        assert "Wall-001" in result
        assert "IfcWall" in result

    def test_format_context_multiple_items_joined_by_separator(self, rag_service):
        """Multiple context items are separated by '---'."""
        from ifc_processor.models import IFCEntity

        e1 = MagicMock(spec=IFCEntity)
        e1.name = "Wall-001"
        e1.ifc_type = "IfcWall"
        e1.building_storey = "L1"
        e1.description = "Wall one."

        e2 = MagicMock(spec=IFCEntity)
        e2.name = "Wall-002"
        e2.ifc_type = "IfcWall"
        e2.building_storey = "L1"
        e2.description = "Wall two."

        result = rag_service._format_context([e1, e2])
        assert "---" in result


@pytest.mark.django_db
class TestBuildHistoryWithinBudget:
    """Tests for _build_history_within_budget."""

    def test_empty_session_returns_empty_string(self, rag_service):
        """Session with no messages returns an empty string."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)

        result = rag_service._build_history_within_budget(session, max_history_tokens=2000)

        assert result == ""

    def test_session_with_messages_returns_formatted_history(self, rag_service):
        """Session with user/assistant messages returns formatted history lines."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)
        MessageFactory(session=session, role="user", content="What is the fire rating?")
        MessageFactory(session=session, role="assistant", content="The fire rating is EI60.")

        result = rag_service._build_history_within_budget(session, max_history_tokens=2000)

        assert "USER" in result
        assert "CASTOR" in result

    def test_zero_budget_returns_empty_string(self, rag_service):
        """Zero token budget prevents any history from being included."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)
        MessageFactory(session=session, role="user", content="A question.")

        result = rag_service._build_history_within_budget(session, max_history_tokens=0)

        assert result == ""

    def test_multiple_messages_are_included_in_order(self, rag_service):
        """Multiple messages within budget are included in chronological order."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)
        MessageFactory(session=session, role="user", content="First question.")
        MessageFactory(session=session, role="assistant", content="First answer.")
        MessageFactory(session=session, role="user", content="Second question.")

        result = rag_service._build_history_within_budget(session, max_history_tokens=5000)

        assert "First question." in result
        assert "First answer." in result
        assert "Second question." in result
        # USER comes before CASTOR
        user_pos = result.index("USER")
        castor_pos = result.index("CASTOR")
        assert user_pos < castor_pos

    def test_very_small_budget_truncates_history(self, rag_service):
        """A very small token budget limits which messages are included."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        session = ChatSessionFactory(project=project, user=user)
        # Add 10 messages
        for i in range(10):
            MessageFactory(session=session, role="user", content=f"Message number {i}.")

        # Budget of 10 tokens → only zero or one short messages fit
        result = rag_service._build_history_within_budget(session, max_history_tokens=10)

        # With a 10-token budget, at most a couple of short lines can fit
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) <= 2


@pytest.mark.django_db
class TestRetrieveVectorContext:
    """Tests for _retrieve_vector_context()."""

    def test_returns_empty_when_embed_query_fails(self, rag_service):
        """Empty query vector → returns empty list immediately."""
        user = UserFactory()
        project = ProjectFactory(owner=user)

        rag_service._mock_embed.embed_query.return_value = []

        result = rag_service._retrieve_vector_context(project, "What is the fire rating?", "auto")

        assert result == []

    def test_ifc_scope_only_queries_ifc_entities(self, rag_service):
        """scope='ifc' only queries IFCEntity table, not DocumentChunk."""
        user = UserFactory()
        project = ProjectFactory(owner=user)

        with patch("chat.services.rag_service.DocumentChunk.objects.filter") as mock_doc_filter:
            rag_service._retrieve_vector_context(project, "Wall height", "ifc")

        mock_doc_filter.assert_not_called()

    def test_docs_scope_only_queries_document_chunks(self, rag_service):
        """scope='docs' only queries DocumentChunk table, not IFCEntity."""
        user = UserFactory()
        project = ProjectFactory(owner=user)

        with patch("chat.services.rag_service.IFCEntity.objects.filter") as mock_ifc_filter:
            rag_service._retrieve_vector_context(project, "Contract clause", "docs")

        mock_ifc_filter.assert_not_called()

    def test_auto_scope_queries_both_tables(self, rag_service):
        """scope='auto' queries both IFCEntity and DocumentChunk tables."""
        user = UserFactory()
        project = ProjectFactory(owner=user)

        with (
            patch("chat.services.rag_service.IFCEntity.objects.filter") as mock_ifc_filter,
            patch("chat.services.rag_service.DocumentChunk.objects.filter") as mock_doc_filter,
        ):
            mock_ifc_filter.return_value.filter.return_value.annotate.return_value.order_by.return_value.__getitem__ = (
                lambda self, key: []
            )
            mock_doc_filter.return_value.select_related.return_value.filter.return_value.annotate.return_value.order_by.return_value.__getitem__ = (
                lambda self, key: []
            )
            # Just verify both code paths are reached — use list() to not crash
            try:
                rag_service._retrieve_vector_context(project, "Mixed query", "auto")
            except Exception:
                pass

        mock_ifc_filter.assert_called()
        mock_doc_filter.assert_called()


@pytest.mark.django_db
class TestGetProjectMetadata:
    """Tests for _get_project_metadata()."""

    def test_empty_project_returns_no_files_active(self, rag_service):
        """Project with no completed files returns 'No files active.'."""
        user = UserFactory()
        project = ProjectFactory(owner=user)

        result = rag_service._get_project_metadata(project)

        assert result == "No files active."

    def test_project_with_ifc_files_includes_models_label(self, rag_service):
        """Project with completed IFC files includes 'MODELS:' in metadata."""
        from ifc_processor.tests.factories import IFCFileFactory

        user = UserFactory()
        project = ProjectFactory(owner=user)
        IFCFileFactory(project=project, name="building.ifc", status="completed")

        result = rag_service._get_project_metadata(project)

        assert "MODELS" in result
        assert "building.ifc" in result


@pytest.mark.django_db
class TestSerializeContext:
    """Tests for _serialize_context()."""

    def test_serialize_empty_list_returns_zero_sources(self, rag_service):
        """Empty context list returns source_count=0."""
        result = rag_service._serialize_context([])
        assert result["source_count"] == 0
        assert result["sources"] == []

    def test_serialize_ifc_entity_produces_ifc_type_entry(self, rag_service):
        """IFCEntity in context produces a source entry with type='ifc'."""
        from unittest.mock import MagicMock

        from ifc_processor.models import IFCEntity

        entity = MagicMock(spec=IFCEntity)
        entity.id = "test-id"
        entity.name = "Wall-001"
        entity.ifc_type = "IfcWall"
        entity.building_storey = "Level 1"
        entity.global_id = "ABC123"
        entity.description = "An external wall."

        result = rag_service._serialize_context([entity])

        assert result["source_count"] == 1
        assert result["sources"][0]["type"] == "ifc"
        assert result["sources"][0]["name"] == "Wall-001"

    def test_serialize_document_chunk_produces_doc_type_entry(self, rag_service):
        """DocumentChunk in context produces a source entry with type='doc'."""
        from unittest.mock import MagicMock

        from documents.models import DocumentChunk

        chunk = MagicMock(spec=DocumentChunk)
        chunk.id = "chunk-id"
        chunk.document = MagicMock()
        chunk.document.name = "Fire Safety Report.pdf"
        chunk.page_number = 5
        chunk.content = "Walls shall have EI120."

        result = rag_service._serialize_context([chunk])

        assert result["source_count"] == 1
        assert result["sources"][0]["type"] == "doc"
        assert result["sources"][0]["source"] == "Fire Safety Report.pdf"
