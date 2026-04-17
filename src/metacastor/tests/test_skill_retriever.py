# metacastor/tests/test_skill_retriever.py
"""
Unit tests for metacastor.services.skill_retriever.

Focuses on the truncation format — Deliverable 5 adds `generated_code` to
the returned dict so the Tier 3 planner can filter on it and render
reference patterns.

The DB-backed `retrieve()` is not re-tested here; it is exercised by
higher-level integration tests in writeback. These tests operate on
`_truncate()` with lightweight stand-ins for SkillExample instances, so
they run without `@pytest.mark.django_db`.
"""

from types import SimpleNamespace

from metacastor.services.skill_retriever import _truncate


def _fake_example(
    *,
    query_text: str = "Set fire rating on walls",
    intent_json=None,
    outcome_tier: int = 1,
    generated_code: str | None = None,
) -> SimpleNamespace:
    """Build a lightweight stand-in that _truncate can consume."""
    if intent_json is None:
        intent_json = {
            "operation": "SET_PROPERTY",
            "filter": {"ifc_type": "IfcWall"},
            "tier": outcome_tier,
            "confidence": 90,
        }
    return SimpleNamespace(
        query_text=query_text,
        intent_json=intent_json,
        outcome_tier=outcome_tier,
        generated_code=generated_code,
    )


class TestTruncate:
    """Tests for the _truncate() helper."""

    def test_tier1_example_has_generated_code_none(self):
        """Tier 1 examples never carry generated code — field must be None."""
        example = _fake_example(outcome_tier=1, generated_code=None)

        result = _truncate(example)

        assert "generated_code" in result
        assert result["generated_code"] is None
        assert result["tier"] == 1
        assert result["operation"] == "SET_PROPERTY"

    def test_tier2_example_has_generated_code_none(self):
        """Tier 2 examples also have generated_code=None."""
        example = _fake_example(outcome_tier=2, generated_code=None)

        result = _truncate(example)

        assert result["generated_code"] is None

    def test_tier3_example_surfaces_generated_code(self):
        """Tier 3 examples with committed code expose the full code string."""
        code = (
            "def modify_ifc(model):\n"
            "    import ifcopenshell\n"
            "    return {'summary': 'ok', 'changes': []}\n"
        )
        intent = {
            "operation": "CODE",
            "filter": {},
            "tier": 3,
            "confidence": 85,
            "code": code,
        }
        example = _fake_example(
            query_text="Create IfcSpace on Level 1",
            intent_json=intent,
            outcome_tier=3,
            generated_code=code,
        )

        result = _truncate(example)

        assert result["generated_code"] == code
        assert result["tier"] == 3
        assert result["query_text"] == "Create IfcSpace on Level 1"

    def test_chained_intent_uses_first_element(self):
        """When intent_json is a list (chain), the first element drives the header."""
        intent = [
            {"operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall"}, "tier": 1},
            {"operation": "SET_ATTRIBUTE", "filter": {"ifc_type": "IfcDoor"}, "tier": 1},
        ]
        example = _fake_example(intent_json=intent, outcome_tier=1)

        result = _truncate(example)

        assert result["operation"] == "SET_PROPERTY"
        assert "IfcWall" in result["filter"]

    def test_falls_back_to_outcome_tier_when_intent_missing_tier(self):
        """If intent_json omits `tier`, _truncate uses outcome_tier on the model."""
        intent = {"operation": "SET_PROPERTY", "filter": {}}  # no "tier"
        example = _fake_example(intent_json=intent, outcome_tier=2)

        result = _truncate(example)

        assert result["tier"] == 2

    def test_filter_is_json_serialised_string(self):
        """The `filter` field is returned as a JSON-encoded string, not a dict."""
        example = _fake_example()

        result = _truncate(example)

        assert isinstance(result["filter"], str)
        assert "IfcWall" in result["filter"]
