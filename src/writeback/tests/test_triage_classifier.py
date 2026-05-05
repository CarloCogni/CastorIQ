# writeback/tests/test_triage_classifier.py
"""Tests for the triage classifier (Stage 1 of the V2 pipeline) — LLM mocked."""

import json
from unittest.mock import MagicMock

import pytest

from writeback.services.triage_classifier import (
    TriageClassifier,
    TriageError,
    _normalise_segments,
)


def _make_response(content: str) -> MagicMock:
    response = MagicMock()
    response.content = content
    return response


class TestNormaliseSegments:
    """The shape repair logic — pure function, no LLM."""

    def test_canonical_segments_list_passes_through(self):
        data = {
            "segments": [
                {
                    "kind": "PROPERTY",
                    "target_phrase": "Wall-01",
                    "value_phrase": "FireRating to EI120",
                }
            ]
        }
        out = _normalise_segments(data)
        assert len(out) == 1
        assert out[0]["kind"] == "PROPERTY"
        assert out[0]["target_phrase"] == "Wall-01"

    def test_top_level_single_segment_dict_is_wrapped(self):
        data = {"kind": "ATTRIBUTE", "target_phrase": "D-01", "value_phrase": "Name to D-01-A"}
        out = _normalise_segments(data)
        assert len(out) == 1
        assert out[0]["kind"] == "ATTRIBUTE"

    def test_top_level_list_of_segments(self):
        data = [{"kind": "PROPERTY", "target_phrase": "X", "value_phrase": "Y"}]
        out = _normalise_segments(data)
        assert len(out) == 1

    def test_lowercase_kind_is_normalised(self):
        data = {"segments": [{"kind": "property", "target_phrase": "X", "value_phrase": "Y"}]}
        out = _normalise_segments(data)
        assert out[0]["kind"] == "PROPERTY"

    def test_unknown_kind_is_dropped(self):
        data = {"segments": [{"kind": "DESTROY", "target_phrase": "X", "value_phrase": "Y"}]}
        out = _normalise_segments(data)
        assert out == []

    def test_out_of_scope_carries_reason(self):
        data = {
            "segments": [
                {
                    "kind": "OUT_OF_SCOPE",
                    "target_phrase": "Wall-01",
                    "value_phrase": "move 1m",
                    "reason": "Geometric edits not supported",
                }
            ]
        }
        out = _normalise_segments(data)
        assert out[0]["reason"] == "Geometric edits not supported"

    def test_unclear_carries_missing_list(self):
        data = {
            "segments": [
                {
                    "kind": "UNCLEAR",
                    "target_phrase": "",
                    "value_phrase": "",
                    "missing": ["target", "value"],
                }
            ]
        }
        out = _normalise_segments(data)
        assert out[0]["missing"] == ["target", "value"]

    def test_unclear_with_no_missing_field_defaults_to_empty(self):
        data = {"segments": [{"kind": "UNCLEAR"}]}
        out = _normalise_segments(data)
        assert out[0]["missing"] == []


class TestTriageClassifier:
    def test_property_request_returns_single_segment(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "PROPERTY",
                            "target_phrase": "all walls",
                            "value_phrase": "FireRating to EI120",
                        }
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify("set fire rating to EI120 on all walls")
        assert len(result.segments) == 1
        assert result.segments[0]["kind"] == "PROPERTY"
        assert result.segments[0]["target_phrase"] == "all walls"
        assert not result.is_unclear
        assert not result.is_out_of_scope

    def test_create_request_segments_correctly(self):
        """Failure A's request should now segment cleanly as CREATE."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "CREATE",
                            "target_phrase": "three new IfcZone for Fire Zone A, B, C",
                            "value_phrase": "IfcZone named Fire Zone A, Fire Zone B, Fire Zone C",
                        }
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify(
            "create three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C"
        )
        assert result.segments[0]["kind"] == "CREATE"

    def test_pset_request_for_failure_b(self):
        """Failure B's request — separates 'all walls' from 'Pset_Maintenance' phrasing."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "PSET",
                            "target_phrase": "all walls",
                            "value_phrase": (
                                "Pset_Maintenance with Inspector=TBD and LastInspection=2026-01-01"
                            ),
                        }
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify(
            "Create a custom property set Pset_Maintenance on all walls "
            'with Inspector "TBD" and LastInspection "2026-01-01"'
        )
        assert result.segments[0]["kind"] == "PSET"
        assert result.segments[0]["target_phrase"] == "all walls"

    def test_chained_property_request(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "PROPERTY",
                            "target_phrase": "Wall-01",
                            "value_phrase": "FireRating to EI120",
                        },
                        {
                            "kind": "PROPERTY",
                            "target_phrase": "Wall-01",
                            "value_phrase": "IsExternal to true",
                        },
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify("set FireRating to EI120 and IsExternal to true on Wall-01")
        assert len(result.segments) == 2

    def test_unclear_request_returns_unclear_segment(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "UNCLEAR",
                            "target_phrase": "",
                            "value_phrase": "",
                            "missing": ["target", "action", "value"],
                        }
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify("do something useful")
        assert result.is_unclear

    def test_out_of_scope_request(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(
            json.dumps(
                {
                    "segments": [
                        {
                            "kind": "OUT_OF_SCOPE",
                            "target_phrase": "Wall-01",
                            "value_phrase": "move 1m east",
                            "reason": "Geometric edits not supported",
                        }
                    ]
                }
            )
        )
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify("move Wall-01 1m east")
        assert result.is_out_of_scope

    def test_empty_message_returns_unclear_without_llm(self):
        mock_llm = MagicMock()
        triage = TriageClassifier(llm=mock_llm)
        result = triage.classify("")
        assert result.is_unclear
        mock_llm.invoke.assert_not_called()

    def test_non_json_response_raises_triage_error(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response("not valid json")
        triage = TriageClassifier(llm=mock_llm)
        with pytest.raises(TriageError):
            triage.classify("anything")

    def test_llm_exception_raises_triage_error(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("Ollama down")
        triage = TriageClassifier(llm=mock_llm)
        with pytest.raises(TriageError):
            triage.classify("anything")

    def test_empty_segments_list_raises_triage_error(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _make_response(json.dumps({"segments": []}))
        triage = TriageClassifier(llm=mock_llm)
        with pytest.raises(TriageError):
            triage.classify("anything")
