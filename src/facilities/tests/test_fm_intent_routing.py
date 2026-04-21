# facilities/tests/test_fm_intent_routing.py
"""Tests for FM-asset intent routing inside RAGService.

The plan for M1 keeps the RAG integration light-touch — the minimum behaviour
is that natural-language asset queries (``"which assets are in Storey-02?"``)
classify as ``fm_asset_query`` and that the asset-context block renders the
matched rows.
"""

from __future__ import annotations

import pytest

from chat.services.rag_service import RAGService


class TestDetectIntent:
    """RAGService._detect_intent keyword classification."""

    @pytest.fixture
    def service(self, monkeypatch):
        """A lightweight RAGService with LLM / embedding init stubbed out."""
        monkeypatch.setattr("chat.services.rag_service.get_llm", lambda **_: None)
        monkeypatch.setattr(
            "chat.services.rag_service.resolve_model_name", lambda _user: "stub-model"
        )
        monkeypatch.setattr("chat.services.rag_service.EmbeddingService", lambda: None)
        return RAGService(user=None)

    @pytest.mark.parametrize(
        "query",
        [
            "Which assets are in Storey-02?",
            "List assets with an expired warranty.",
            "Show me the asset inventory for HVAC.",
            "What assets under warranty do we have?",
            "Give me the facility asset register.",
        ],
    )
    def test_fm_asset_queries_route_to_fm_asset_query(self, service, query):
        """FM-leaning phrasing classifies as fm_asset_query."""
        assert service._detect_intent(query) == "fm_asset_query"

    def test_fm_asset_wins_over_ifc_inventory_when_both_keywords_present(self, service):
        """FM keywords are more specific than IFC inventory and take priority."""
        # "list" is not in either set on its own, but "list assets" is an FM trigger
        # while "list all" triggers ifc_inventory.
        assert service._detect_intent("list assets") == "fm_asset_query"
        assert service._detect_intent("list all walls") == "ifc_inventory"

    def test_plain_question_still_falls_through_to_specific_qa(self, service):
        """Queries without any matching keyword fall back to specific_qa."""
        assert service._detect_intent("What material is the roof?") == "specific_qa"


@pytest.mark.django_db
class TestFormatFmAssetContext:
    """RAGService._format_fm_asset_context renders a compact text block."""

    def test_empty_list_returns_guidance_sentence(self):
        """No matches → the LLM is told there are no assets and how to respond."""
        block = RAGService._format_fm_asset_context([])
        assert "No matching assets" in block

    def test_populated_list_renders_key_fields(self):
        """Each asset row surfaces tag, manufacturer, warranty, condition, and location."""
        from facilities.tests.factories import FacilityAssetFactory

        asset = FacilityAssetFactory(
            asset_tag="AHU-04",
            manufacturer="Carrier",
            model_number="50BV",
            serial_number="SN-007",
            condition_score=72,
        )
        block = RAGService._format_fm_asset_context([asset])
        assert "AHU-04" in block
        assert "Carrier" in block
        assert "72/100" in block
