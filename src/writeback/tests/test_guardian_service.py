# writeback/tests/test_guardian_service.py
"""Tests for GuardianService — LLM and embedding service always mocked.

V3: the query and the prompt are built from the proposal's measured diff.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.tests.factories import IFCEntityFactory
from writeback.models import ModificationProposal
from writeback.services.guardian_service import (
    GuardianService,
    build_guardian_query,
    camel_to_words,
)
from writeback.tests.factories import WALL_IDS, ModificationProposalFactory, sample_diff

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


def _verdict(verdict: str, explanation: str, source: str) -> MagicMock:
    response = MagicMock()
    response.content = json.dumps(
        {"verdict": verdict, "explanation": explanation, "source_detail": source}
    )
    return response


@pytest.fixture
def mock_llm():
    """A mock LLM object with a default CONFIRMED verdict."""
    llm = MagicMock()
    llm.invoke.return_value = _verdict(
        "CONFIRMED", "Document confirms EI120.", "Fire Strategy.pdf, p.14"
    )
    return llm


@pytest.fixture
def mock_embed():
    """A mock EmbeddingService that returns a fixed vector."""
    embed = MagicMock()
    embed.embed_query.return_value = FAKE_VECTOR
    return embed


@pytest.fixture
def walls(ifc_file):
    """Three indexed walls whose GlobalIds match the factory diff."""
    return [
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", global_id=gid) for gid in WALL_IDS
    ]


def _chunk(name: str, page: int, content: str) -> MagicMock:
    chunk = MagicMock()
    chunk.document.name = name
    chunk.page_number = page
    chunk.content = content
    return chunk


@pytest.mark.django_db
class TestGuardianCheck:
    """Tests for GuardianService.check()."""

    def test_check_no_documents_returns_unknown_status(self, ifc_file, walls, mock_llm, mock_embed):
        """With no document chunks in the project, guardian sets UNKNOWN status."""
        guardian = _make_guardian(mock_llm, mock_embed)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.UNKNOWN
        assert "No relevant information" in result.verification_result

    def test_check_confirmed_verdict_sets_verified_status(
        self, ifc_file, walls, mock_llm, mock_embed
    ):
        """When LLM returns CONFIRMED, proposal gets VERIFIED verification_status."""
        guardian = _make_guardian(mock_llm, mock_embed)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        with patch.object(guardian, "_search_documents") as mock_search:
            mock_search.return_value = [
                _chunk(
                    "Fire Strategy.pdf", 14, "External walls shall have minimum EI120 fire rating."
                )
            ]
            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.VERIFIED
        assert "EI120" in result.verification_result
        human = mock_llm.invoke.call_args.args[0][1].content
        assert "Entity type: wall" in human
        assert "Pset_WallCommon.FireRating: 'EI60' → 'EI120' on 3 entities" in human

    def test_check_conflict_verdict_sets_conflict_status(
        self, ifc_file, walls, mock_llm, mock_embed
    ):
        """When LLM returns CONFLICT, proposal gets CONFLICT verification_status."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_llm.invoke.return_value = _verdict(
            "CONFLICT", "Document requires EI90 not EI120.", "Fire Strategy.pdf, p.5"
        )
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        with patch.object(guardian, "_search_documents") as mock_search:
            mock_search.return_value = [
                _chunk("Fire Strategy.pdf", 5, "External walls shall be rated EI90.")
            ]
            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.CONFLICT

    def test_check_llm_failure_does_not_raise(self, ifc_file, walls, mock_llm, mock_embed):
        """LLM exception during guardian check is swallowed — FAILED status returned."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_llm.invoke.side_effect = Exception("Ollama unreachable")
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        with patch.object(guardian, "_search_documents") as mock_search:
            mock_search.return_value = [_chunk("x.pdf", 1, "…")]
            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.FAILED

    def test_check_model_timeout_is_a_failed_verdict(self, ifc_file, walls, mock_llm, mock_embed):
        """The verdict call has a wall-clock cap; a timeout is FAILED, never a hang or a raise."""
        guardian = _make_guardian(mock_llm, mock_embed)
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        with (
            patch.object(guardian, "_search_documents", return_value=[_chunk("x.pdf", 1, "…")]),
            patch(
                "writeback.services.guardian_service.safe_invoke", side_effect=TimeoutError()
            ) as invoke,
        ):
            result = guardian.check(proposal)

        assert result.verification_status == ModificationProposal.VerificationStatus.FAILED
        assert invoke.call_args.kwargs["timeout"] == 90

    def test_check_embed_failure_returns_unknown(self, ifc_file, walls, mock_llm, mock_embed):
        """Embed returning empty vector → no chunks → UNKNOWN status (non-blocking)."""
        guardian = _make_guardian(mock_llm, mock_embed)
        mock_embed.embed_query.return_value = []
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=ifc_file.project.owner)

        result = guardian.check(proposal)

        assert result.verification_status in (
            ModificationProposal.VerificationStatus.UNKNOWN,
            ModificationProposal.VerificationStatus.FAILED,
        )


@pytest.mark.django_db
class TestBuildGuardianQuery:
    """The query is type + humanised property + value from the dominant diff row."""

    def test_fire_rating_case_reads_wall_fire_rating_value(self, ifc_file, walls):
        """The spec's acceptance string for the fire-rating case."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, diff=sample_diff(after="EI60"))
        assert build_guardian_query(proposal) == "wall fire rating EI60"

    def test_dominant_row_wins_over_a_smaller_one(self, ifc_file, walls):
        """Two rows: the one touching more entities drives the query."""
        diff = sample_diff()
        diff["property_changes"].append(
            {
                "global_id": WALL_IDS[0],
                "pset": "Pset_WallCommon",
                "prop": "IsExternal",
                "before": True,
                "after": False,
            }
        )
        proposal = ModificationProposalFactory(ifc_file=ifc_file, diff=diff)
        assert build_guardian_query(proposal) == "wall fire rating EI120"

    def test_no_property_row_falls_back_to_the_explanation(self, ifc_file):
        """A creation-only diff has no property row; the explanation is the query."""
        diff = sample_diff(global_ids=[])
        diff["added_global_ids"] = ["NEW1"]
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file, diff=diff, explanation="Adds one zone."
        )
        assert build_guardian_query(proposal) == "Adds one zone."


def test_camel_to_words():
    """'FireRating' → 'fire rating'; a lowercase word is unchanged."""
    assert camel_to_words("FireRating") == "fire rating"
    assert camel_to_words("ThermalTransmittance") == "thermal transmittance"
    assert camel_to_words("status") == "status"


def test_guardian_uses_the_modify_model_with_the_modify_context():
    """Code, explanation and verdict share one loaded runner: same purpose, same num_ctx."""
    from writeback.services.generator import NUM_CTX

    with (
        patch("writeback.services.guardian_service.get_llm") as get_llm,
        patch("writeback.services.guardian_service.EmbeddingService"),
    ):
        GuardianService(user=None)

    assert get_llm.call_args.kwargs["purpose"] == "modify"
    assert get_llm.call_args.kwargs["num_ctx"] == NUM_CTX
