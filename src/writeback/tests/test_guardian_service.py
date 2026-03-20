# writeback/tests/test_guardian_service.py
"""Tests for GuardianService — LLM and embedding service always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.models import ModificationProposal
from writeback.services.guardian_service import GuardianService
from writeback.tests.factories import ModificationProposalFactory

FAKE_VECTOR = [0.1] * 1024


def _make_guardian(mock_llm: MagicMock, mock_embed: MagicMock) -> GuardianService:
    """Construct a GuardianService with both LLM and embedding service mocked."""
    with patch("writeback.services.guardian_service.get_llm", return_value=mock_llm):
        with patch("writeback.services.guardian_service.EmbeddingService") as mock_embed_cls:
            mock_embed_cls.return_value = mock_embed
            service = GuardianService(user=None)
    service.llm = mock_llm
    service.embedding_service = mock_embed
    return service


@pytest.fixture
def mock_llm():
    """A mock LLM object with a default CONFIRMED verdict."""
    llm = MagicMock()
    response = MagicMock()
    response.content = json.dumps(
        {
            "verdict": "CONFIRMED",
            "explanation": "Document confirms EI120 fire rating for external walls.",
            "source_detail": "Fire Strategy.pdf, p.14",
        }
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture
def mock_embed():
    """A mock EmbeddingService that returns a fixed vector."""
    embed = MagicMock()
    embed.embed_query.return_value = FAKE_VECTOR
    return embed


@pytest.mark.django_db
class TestGuardianCheck:
    """Tests for GuardianService.check()."""

    def test_check_no_documents_returns_unknown_status(
        self, project, ifc_file, wall_entities, mock_llm, mock_embed
    ):
        """With no document chunks in the project, guardian sets UNKNOWN status."""
        guardian = _make_guardian(mock_llm, mock_embed)
        # embed_query returns vector but DB has no DocumentChunks → no chunks found
        mock_embed.embed_query.return_value = FAKE_VECTOR

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=ifc_file.project.owner,
            intent_json={
                "tier": 1,
                "operation": "SET_PROPERTY",
                "filter": {"ifc_type": "IfcWall"},
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "confidence": 90,
                "explanation": "Set fire rating to EI120",
            },
        )

        result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.UNKNOWN
        assert "No relevant information" in result.verification_result

    def test_check_confirmed_verdict_sets_verified_status(
        self, project, ifc_file, mock_llm, mock_embed
    ):
        """When LLM returns CONFIRMED, proposal gets VERIFIED verification_status."""
        from documents.tests.factories import DocumentChunkFactory, DocumentFactory

        guardian = _make_guardian(mock_llm, mock_embed)
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "CONFIRMED",
                "explanation": "Document confirms EI120.",
                "source_detail": "Fire Strategy.pdf, p.14",
            }
        )

        # Need a document chunk with an embedding so the guardian can find it
        doc = DocumentFactory(project=project, status="completed")
        DocumentChunkFactory(document=doc, embedding=FAKE_VECTOR, page_number=14)

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=ifc_file.project.owner,
            intent_json={
                "tier": 1,
                "operation": "SET_PROPERTY",
                "filter": {"ifc_type": "IfcWall"},
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "confidence": 90,
                "explanation": "Set fire rating to EI120",
            },
        )

        # Patch the DB search so it returns our chunk with low distance
        with patch.object(guardian, "_search_documents") as mock_search:
            mock_chunk = MagicMock()
            mock_chunk.document.name = "Fire Strategy.pdf"
            mock_chunk.page_number = 14
            mock_chunk.content = "External walls shall have minimum EI120 fire rating."
            mock_search.return_value = [mock_chunk]

            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.VERIFIED
        assert "EI120" in result.verification_result

    def test_check_conflict_verdict_sets_conflict_status(
        self, project, ifc_file, mock_llm, mock_embed
    ):
        """When LLM returns CONFLICT, proposal gets CONFLICT verification_status."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "CONFLICT",
                "explanation": "Document requires EI90 not EI120.",
                "source_detail": "Fire Strategy.pdf, p.5",
            }
        )

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=ifc_file.project.owner,
            intent_json={
                "tier": 1,
                "operation": "SET_PROPERTY",
                "filter": {"ifc_type": "IfcWall"},
                "property": "FireRating",
                "new_value": "EI120",
                "explanation": "Set fire rating to EI120",
            },
        )

        with patch.object(guardian, "_search_documents") as mock_search:
            mock_chunk = MagicMock()
            mock_chunk.document.name = "Fire Strategy.pdf"
            mock_chunk.page_number = 5
            mock_chunk.content = "External walls shall be rated EI90."
            mock_search.return_value = [mock_chunk]

            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.CONFLICT

    def test_check_llm_failure_does_not_raise(self, project, ifc_file, mock_llm, mock_embed):
        """LLM exception during guardian check is swallowed — FAILED status returned."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_llm.invoke.side_effect = Exception("Ollama unreachable")

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=ifc_file.project.owner,
            intent_json={"operation": "SET_PROPERTY", "explanation": "test"},
        )

        with patch.object(guardian, "_search_documents") as mock_search:
            mock_chunk = MagicMock()
            mock_search.return_value = [mock_chunk]

            # Must NOT raise
            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.FAILED

    def test_check_embed_failure_returns_unknown(self, project, ifc_file, mock_llm, mock_embed):
        """Embed returning empty vector → no chunks → UNKNOWN status (non-blocking)."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_embed.embed_query.return_value = []  # Empty vector → no search

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=ifc_file.project.owner,
            intent_json={"operation": "SET_PROPERTY", "explanation": "test"},
        )

        result = guardian.check(proposal)

        assert result.verification_status in (
            ModificationProposal.VerificationStatus.UNKNOWN,
            ModificationProposal.VerificationStatus.FAILED,
        )


class TestBuildSearchQuery:
    """Unit tests for _build_search_query — no DB needed."""

    def test_camel_to_words_converts_fire_rating(self):
        """'FireRating' → 'fire rating'."""
        result = GuardianService._camel_to_words("FireRating")
        assert result == "fire rating"

    def test_camel_to_words_converts_thermal_transmittance(self):
        """'ThermalTransmittance' → 'thermal transmittance'."""
        result = GuardianService._camel_to_words("ThermalTransmittance")
        assert result == "thermal transmittance"

    def test_camel_to_words_single_word_unchanged(self):
        """Single lowercase word passes through unchanged."""
        result = GuardianService._camel_to_words("status")
        assert result == "status"
