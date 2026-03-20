# core/tests/test_token_budget.py
"""Tests for core.token_budget — patches Ollama to avoid network calls."""

import pytest

from core.token_budget import (
    DEFAULT_CONTEXT_WINDOW,
    RESPONSE_RESERVE,
    SAFETY_RATIO,
    TokenBudget,
    _context_window_cache,
    compute_budget,
    get_context_window,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the module-level cache between tests."""
    _context_window_cache.clear()
    yield
    _context_window_cache.clear()


def test_token_budget_total_input_sums_all_sections():
    """total_input is the sum of all four input sections."""
    budget = TokenBudget(
        model_context_window=8192,
        max_usable=5000,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=100,
        entity_tokens=200,
        history_tokens=50,
        injected_tokens=75,
    )
    assert budget.total_input == 425


def test_token_budget_remaining_for_injection():
    """remaining_for_injection = max_usable - system - entity."""
    budget = TokenBudget(
        model_context_window=8192,
        max_usable=5000,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=1000,
        entity_tokens=500,
        history_tokens=0,
        injected_tokens=0,
    )
    assert budget.remaining_for_injection == 3500


def test_token_budget_utilization_pct():
    """utilization_pct reflects total_input / max_usable * 100."""
    budget = TokenBudget(
        model_context_window=8192,
        max_usable=1000,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=500,
        entity_tokens=0,
        history_tokens=0,
        injected_tokens=0,
    )
    assert budget.utilization_pct == 50.0


def test_token_budget_is_over_budget_true():
    """is_over_budget is True when total_input > max_usable."""
    budget = TokenBudget(
        model_context_window=8192,
        max_usable=100,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=200,
        entity_tokens=0,
        history_tokens=0,
        injected_tokens=0,
    )
    assert budget.is_over_budget is True


def test_token_budget_is_over_budget_false():
    """is_over_budget is False when total_input <= max_usable."""
    budget = TokenBudget(
        model_context_window=8192,
        max_usable=1000,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=200,
        entity_tokens=0,
        history_tokens=0,
        injected_tokens=0,
    )
    assert budget.is_over_budget is False


def test_compute_budget_falls_back_to_registry_when_ollama_down(monkeypatch):
    """When Ollama is unreachable, compute_budget uses registry value."""
    monkeypatch.setattr("core.token_budget._fetch_context_window_from_ollama", lambda model: None)
    budget = compute_budget("llama3.1:8b", system="Hello")
    # llama3.1:8b is in registry with context_window_size=8192
    assert budget.model_context_window == 8192


def test_compute_budget_uses_default_when_unknown_model(monkeypatch):
    """Unknown model not in registry falls back to DEFAULT_CONTEXT_WINDOW."""
    monkeypatch.setattr("core.token_budget._fetch_context_window_from_ollama", lambda model: None)
    budget = compute_budget("unknown-model:99b", system="Hello")
    assert budget.model_context_window == DEFAULT_CONTEXT_WINDOW


def test_get_context_window_caches_ollama_result(monkeypatch):
    """Ollama is called once; subsequent calls use the cache."""
    call_count = {"n": 0}

    def mock_fetch(model):
        call_count["n"] += 1
        return 16384

    monkeypatch.setattr("core.token_budget._fetch_context_window_from_ollama", mock_fetch)

    get_context_window("some-model")
    get_context_window("some-model")

    assert call_count["n"] == 1
