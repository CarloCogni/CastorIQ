# writeback/tests/test_slot_extractor.py
"""Tests for the slot extractor (Stage 2 of the V2 pipeline) — LLM mocked."""

import json
from unittest.mock import MagicMock

import pytest

from writeback.services.slot_extractor import (
    SAFE_ATTRIBUTES,
    SlotExtractionError,
    SlotExtractor,
)


def _response(payload):
    response = MagicMock()
    response.content = json.dumps(payload) if not isinstance(payload, str) else payload
    return response


def _segment(kind, target="X", value="Y"):
    return {"kind": kind, "target_phrase": target, "value_phrase": value}


class TestPropertyExtraction:
    def test_basic_property(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("PROPERTY"), "set FireRating to EI120 on all walls")
        assert result.slots["pset"] == "Pset_WallCommon"
        assert result.slots["property"] == "FireRating"
        assert result.slots["value"] == "EI120"
        assert result.warnings == []

    def test_property_without_pset(self):
        """User didn't name a pset — slot is empty string, not raised."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": None, "property": "FireRating", "value": "EI120"}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("PROPERTY"), "set FireRating to EI120 on all walls")
        assert result.slots["pset"] == ""
        assert result.slots["property"] == "FireRating"

    def test_value_not_in_message_emits_groundedness_warning(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI240"}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("PROPERTY"), "set fire rating to EI120 on Wall-01")
        assert result.warnings  # one entry warning about EI240 not in message
        assert "EI240" in result.warnings[0]

    def test_missing_property_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": "Pset_X", "property": "", "value": "EI120"}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="missing 'property'"):
            ext.extract(_segment("PROPERTY"), "set FireRating to EI120")

    def test_missing_value_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": "Pset_X", "property": "FireRating", "value": None}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="missing 'value'"):
            ext.extract(_segment("PROPERTY"), "set FireRating")

    def test_boolean_value_skips_groundedness_check(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"pset": "Pset_WallCommon", "property": "IsExternal", "value": True}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("PROPERTY"), "set IsExternal on Wall-01")
        assert result.slots["value"] is True
        assert result.warnings == []


class TestAttributeExtraction:
    @pytest.mark.parametrize("attr", sorted(SAFE_ATTRIBUTES))
    def test_safe_attributes_accepted(self, attr):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response({"attribute": attr, "value": "NewValue"})
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(
            _segment("ATTRIBUTE"), f"set {attr} to NewValue on D-01"
        )
        assert result.slots["attribute"] == attr

    def test_unsafe_attribute_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response({"attribute": "GeometryRef", "value": "X"})
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="must be one of"):
            ext.extract(_segment("ATTRIBUTE"), "anything")

    def test_attribute_value_groundedness(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response({"attribute": "Name", "value": "Made-Up"})
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(
            _segment("ATTRIBUTE"), "rename D-01 to D-01-Updated"
        )
        # 'Made-Up' isn't in the message
        assert result.warnings


class TestPsetExtraction:
    def test_add_pset_with_properties(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {
                "operation": "ADD_PSET",
                "pset_name": "Pset_Maintenance",
                "properties": {"Inspector": "TBD", "LastInspection": "2026-01-01"},
            }
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(
            _segment("PSET"),
            "add Pset_Maintenance to all walls with Inspector TBD and LastInspection 2026-01-01",
        )
        assert result.slots["operation"] == "ADD_PSET"
        assert result.slots["pset_name"] == "Pset_Maintenance"
        assert result.slots["properties"]["Inspector"] == "TBD"

    def test_remove_pset(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"operation": "REMOVE_PSET", "pset_name": "Pset_Custom"}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("PSET"), "remove Pset_Custom from Wall-01")
        assert result.slots["operation"] == "REMOVE_PSET"
        assert "properties" not in result.slots

    def test_unknown_operation_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"operation": "UPDATE_PSET", "pset_name": "Pset_X"}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="must be ADD_PSET"):
            ext.extract(_segment("PSET"), "anything")

    def test_missing_pset_name_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response({"operation": "ADD_PSET", "pset_name": ""})
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="missing 'pset_name'"):
            ext.extract(_segment("PSET"), "anything")

    def test_properties_must_be_object(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"operation": "ADD_PSET", "pset_name": "Pset_X", "properties": ["wrong shape"]}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="must be a JSON object"):
            ext.extract(_segment("PSET"), "anything")


class TestCreateExtraction:
    def test_create_zone_with_three_names(self):
        """Failure A's CREATE request should extract cleanly."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {
                "entity_class": "IfcZone",
                "names": ["Fire Zone A", "Fire Zone B", "Fire Zone C"],
                "parent_phrase": "",
            }
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(
            _segment("CREATE"),
            "create three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C",
        )
        assert result.slots["entity_class"] == "IfcZone"
        assert result.slots["names"] == ["Fire Zone A", "Fire Zone B", "Fire Zone C"]
        assert result.slots["parent_phrase"] == ""

    def test_create_with_parent_phrase(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"entity_class": "IfcSpace", "names": ["Server Room"], "parent_phrase": "Level 2"}
        )
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(
            _segment("CREATE"), "create an IfcSpace called Server Room on Level 2"
        )
        assert result.slots["parent_phrase"] == "Level 2"

    def test_invalid_entity_class_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"entity_class": "Zone", "names": ["A"], "parent_phrase": ""}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="must be a valid IFC class"):
            ext.extract(_segment("CREATE"), "anything")

    def test_empty_names_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response(
            {"entity_class": "IfcZone", "names": [], "parent_phrase": ""}
        )
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="at least one name"):
            ext.extract(_segment("CREATE"), "anything")


class TestNoSlotKinds:
    def test_delete_kind_returns_empty_slots_without_llm(self):
        mock_llm = MagicMock()
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("DELETE"), "delete all chairs")
        assert result.slots == {}
        mock_llm.invoke.assert_not_called()

    def test_relationship_kind_returns_empty_slots_without_llm(self):
        mock_llm = MagicMock()
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("RELATIONSHIP"), "move Wall-01 to Level 2")
        assert result.slots == {}
        mock_llm.invoke.assert_not_called()

    def test_unclear_kind_returns_empty_slots_without_llm(self):
        mock_llm = MagicMock()
        ext = SlotExtractor(llm=mock_llm)
        result = ext.extract(_segment("UNCLEAR"), "anything")
        assert result.slots == {}
        mock_llm.invoke.assert_not_called()


class TestLLMErrorPaths:
    def test_non_json_response_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _response("not json")
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError, match="valid JSON"):
            ext.extract(_segment("PROPERTY"), "anything")

    def test_llm_exception_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("Ollama down")
        ext = SlotExtractor(llm=mock_llm)
        with pytest.raises(SlotExtractionError):
            ext.extract(_segment("PROPERTY"), "anything")
