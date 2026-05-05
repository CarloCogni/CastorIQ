# writeback/tests/test_hint_generator.py
"""Unit tests for the V2 rejection HintGenerator (Strategies 1-3)."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from writeback.services.hint_generator import (
    SOURCE_LLM,
    SOURCE_REGISTRY,
    SOURCE_TEMPLATED,
    Hint,
    HintGenerator,
)
from writeback.services.tier_router import (
    REJECT_CATEGORY_OUT_OF_SCOPE,
    REJECT_CATEGORY_PSET_UNKNOWN,
    REJECT_CATEGORY_UNCLEAR,
)


@pytest.fixture(autouse=True)
def _clear_hint_cache():
    """The HintGenerator caches LLM-fallback results by (category, payload).
    Clear before each test so cached hints from one test don't leak into
    the next."""
    cache.clear()
    yield
    cache.clear()


# ── Strategy 1: Templated ─────────────────────────────────────────────


class TestTemplatedStrategy:
    """Static templates by rejection category. Zero LLM, zero DB."""

    def test_out_of_scope_includes_castor_scope_reminder(self):
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_OUT_OF_SCOPE,
            payload={"reason": "Geometric modifications are out of scope."},
        )
        assert hint.source == SOURCE_TEMPLATED
        assert "geometry" in hint.text.lower()

    @pytest.mark.parametrize(
        "missing,expected_substr",
        [
            (["target", "value"], "which entities"),
            (["value"], "new value"),
            (["target"], "entity reference"),
            (["request"], "set <property>"),
            ([], "set <property>"),
        ],
    )
    def test_unclear_picks_template_per_missing_set(self, missing, expected_substr):
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_UNCLEAR,
            payload={"missing": missing},
        )
        assert hint.source == SOURCE_TEMPLATED
        assert expected_substr.lower() in hint.text.lower()


# ── Strategy 2: Registry-grounded (the §18.4 use case) ────────────────


class TestRegistryStrategyForProperty:
    """Property typo / near-miss → suggests the closest STANDARD_PSETS prop."""

    def test_fireresistanceduration_suggests_firerating(self):
        """The §18.4 acceptance test: typo'd property → real one."""
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_PSET_UNKNOWN,
            payload={"property": "FireResistanceDuration", "ifc_type_hint": "IfcWall"},
        )
        assert hint.source == SOURCE_REGISTRY
        assert "FireRating" in hint.text
        assert "Pset_WallCommon" in hint.text

    def test_case_insensitive_match(self):
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_PSET_UNKNOWN,
            payload={"property": "firerating", "ifc_type_hint": "IfcWall"},
        )
        assert hint.source == SOURCE_REGISTRY
        assert "FireRating" in hint.text

    def test_unknown_gibberish_returns_empty(self):
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_PSET_UNKNOWN,
            payload={"property": "xyzqq42_unrelated", "ifc_type_hint": "IfcWall"},
        )
        # No similarity → falls through to Strategy 3 (gated off in tests).
        assert hint.is_empty

    def test_falls_back_to_global_scan_when_type_filter_empty(self):
        """An ifc_type that has no Pset_* entries should still get a hint
        from the global scan fallback."""
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=REJECT_CATEGORY_PSET_UNKNOWN,
            payload={"property": "FireRating", "ifc_type_hint": "IfcMadeUpThing"},
        )
        # Global scan should still find FireRating in some standard pset.
        assert hint.source == SOURCE_REGISTRY
        assert "FireRating" in hint.text


# ── Strategy 2 (entity match) requires DB — covered via Strategy 1 above ──


# ── Strategy 3: LLM-fallback, gated ───────────────────────────────────


class TestLlmFallbackGating:
    """Strategy 3 must NOT fire unless explicitly whitelisted.

    Uses a synthetic category that neither Strategy 1 nor Strategy 2
    handles, so the test exercises the LLM gating path in isolation
    rather than racing against the registry-grounded strategy.
    """

    SYNTHETIC_CATEGORY = "synthetic_test_category"
    PAYLOAD = {"context": "test"}

    def test_disabled_when_master_flag_off(self, settings):
        settings.WRITEBACK_HINT_LLM_FALLBACK = False
        settings.WRITEBACK_HINT_LLM_CATEGORIES = (self.SYNTHETIC_CATEGORY,)
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=self.SYNTHETIC_CATEGORY,
            payload=self.PAYLOAD,
        )
        assert hint.is_empty

    def test_disabled_when_category_not_whitelisted(self, settings):
        settings.WRITEBACK_HINT_LLM_FALLBACK = True
        settings.WRITEBACK_HINT_LLM_CATEGORIES = ()  # empty whitelist
        gen = HintGenerator(project=MagicMock())
        hint = gen.suggest(
            reason_category=self.SYNTHETIC_CATEGORY,
            payload=self.PAYLOAD,
        )
        assert hint.is_empty

    def test_fires_when_whitelisted_and_returns_validated_hint(self, settings):
        settings.WRITEBACK_HINT_LLM_FALLBACK = True
        settings.WRITEBACK_HINT_LLM_CATEGORIES = (self.SYNTHETIC_CATEGORY,)
        gen = HintGenerator(project=MagicMock())
        # Suggest a real Pset.Property pair so registry validation passes.
        with patch.object(
            gen,
            "_llm_invoke",
            return_value='{"suggestion": "Try Pset_WallCommon.FireRating instead."}',
        ):
            hint = gen.suggest(
                reason_category=self.SYNTHETIC_CATEGORY,
                payload=self.PAYLOAD,
            )
        assert hint.source == SOURCE_LLM
        assert "Pset_WallCommon.FireRating" in hint.text

    def test_drops_hint_with_unknown_pset_property(self, settings):
        """Registry validation rejects fabricated Pset.Property pairs."""
        settings.WRITEBACK_HINT_LLM_FALLBACK = True
        settings.WRITEBACK_HINT_LLM_CATEGORIES = (self.SYNTHETIC_CATEGORY,)
        gen = HintGenerator(project=MagicMock())
        with patch.object(
            gen,
            "_llm_invoke",
            return_value='{"suggestion": "Try Pset_FakePset.NotARealProperty instead."}',
        ):
            hint = gen.suggest(
                reason_category=self.SYNTHETIC_CATEGORY,
                payload=self.PAYLOAD,
            )
        assert hint.is_empty

    def test_returns_empty_when_llm_returns_garbage(self, settings):
        settings.WRITEBACK_HINT_LLM_FALLBACK = True
        settings.WRITEBACK_HINT_LLM_CATEGORIES = (self.SYNTHETIC_CATEGORY,)
        gen = HintGenerator(project=MagicMock())
        with patch.object(gen, "_llm_invoke", return_value="this is not JSON"):
            hint = gen.suggest(
                reason_category=self.SYNTHETIC_CATEGORY,
                payload=self.PAYLOAD,
            )
        assert hint.is_empty

    def test_returns_empty_when_llm_raises(self, settings):
        settings.WRITEBACK_HINT_LLM_FALLBACK = True
        settings.WRITEBACK_HINT_LLM_CATEGORIES = (self.SYNTHETIC_CATEGORY,)
        gen = HintGenerator(project=MagicMock())
        with patch.object(gen, "_llm_invoke", side_effect=RuntimeError("ollama down")):
            hint = gen.suggest(
                reason_category=self.SYNTHETIC_CATEGORY,
                payload=self.PAYLOAD,
            )
        assert hint.is_empty


class TestHintShape:
    def test_empty_hint_helper(self):
        h = Hint(text="", source="")
        assert h.is_empty
        assert not Hint(text="anything", source=SOURCE_REGISTRY).is_empty
