# core/tests/test_llm_model_registry.py
"""Tests for core.llm_model_registry — pure Python, no DB or network."""

import pytest

from core.llm_model_registry import (
    DEFAULT_CONTEXT_WINDOW,
    MODEL_REGISTRY,
    ModelInfo,
    get_model_info,
    get_models_for_tier,
)


def test_get_model_info_known_model_returns_model_info():
    """Known model tag returns a ModelInfo dataclass."""
    info = get_model_info("llama3.1:8b")
    assert info is not None
    assert isinstance(info, ModelInfo)
    assert info.family == "llama"


def test_get_model_info_unknown_model_returns_none():
    """Unknown model tag returns None."""
    assert get_model_info("totally-unknown-model:99b") is None


def test_get_models_for_tier_lite_returns_only_small_models():
    """Lite tier (≤6 GB) should not include models > 6 GB."""
    lite_models = get_models_for_tier("lite")
    for tag, info in lite_models.items():
        assert info.vram_gb <= 6, f"{tag} has vram_gb={info.vram_gb} > 6"


def test_all_registry_entries_have_required_fields():
    """Every registry entry has the required ModelInfo fields."""
    required = ["label", "family", "vram_gb", "tier", "description"]
    for tag, info in MODEL_REGISTRY.items():
        for field in required:
            assert hasattr(info, field), f"{tag} missing field '{field}'"
        assert info.context_window_size > 0, f"{tag} has non-positive context window"
