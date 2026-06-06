# core/tests/test_llm_routing.py
"""Tests for core.llm._resolve_llm_choice — table-driven over the 8 main combos."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.llm import _resolve_llm_choice
from core.models import SiteLLMConfig, UserLLMConfig
from core.services import byok_service
from environments.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _crypto_key(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    crypto._reset_cache()
    yield
    crypto._reset_cache()


@pytest.fixture
def site_anthropic_for_ask():
    """Site config: Ask=anthropic claude-sonnet-4-6, Modify=groq scout."""
    cfg = SiteLLMConfig.load()
    cfg.ask_provider = "anthropic"
    cfg.ask_model = "claude-sonnet-4-6"
    cfg.modify_provider = "groq"
    cfg.modify_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    cfg.force_local_ollama = False
    cfg.save()
    return cfg


# ── No user / anonymous ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_anonymous_user_gets_site_default(site_anthropic_for_ask):
    choice = _resolve_llm_choice(None, "ask")
    assert choice.provider == "anthropic"
    assert choice.model == "claude-sonnet-4-6"
    assert choice.api_key is None
    assert choice.byok is False


# ── User with no override ────────────────────────────────────────────────


@pytest.mark.django_db
def test_user_no_override_falls_back_to_site(site_anthropic_for_ask):
    user = UserFactory()
    choice = _resolve_llm_choice(user, "modify")
    assert choice.provider == "groq"
    assert choice.byok is False
    assert choice.api_key is None


# ── Ollama override ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_user_overrides_to_ollama_uses_active_model(site_anthropic_for_ask, settings):
    settings.OLLAMA_MODEL = "qwen3:14b"
    user = UserFactory()
    cfg = UserLLMConfig.load(user)
    cfg.ask_provider_override = "ollama"
    cfg.active_model = "qwen3:32b"
    cfg.save()

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "ollama"
    assert choice.model == "qwen3:32b"
    assert choice.byok is False


@pytest.mark.django_db
def test_user_overrides_to_ollama_no_active_model_falls_back_env(site_anthropic_for_ask, settings):
    settings.OLLAMA_MODEL = "llama3.1:8b"
    user = UserFactory()
    cfg = UserLLMConfig.load(user)
    cfg.ask_provider_override = "ollama"
    cfg.save()

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "ollama"
    assert choice.model == "llama3.1:8b"


# ── BYOK override with key set ───────────────────────────────────────────


@pytest.mark.django_db
def test_byok_anthropic_with_key_uses_user_credentials(site_anthropic_for_ask):
    user = UserFactory()
    byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
    byok_service.set_model(user, "anthropic", "claude-opus-4-7")
    byok_service.set_provider_override(user, "ask", "anthropic")

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "anthropic"
    assert choice.model == "claude-opus-4-7"
    assert choice.api_key == "sk-ant-real-looking-key-1234567"
    assert choice.byok is True


@pytest.mark.django_db
def test_byok_groq_with_key_uses_user_credentials(site_anthropic_for_ask):
    user = UserFactory()
    byok_service.save_key(user, "groq", "gsk_real-looking-key-1234567890")
    byok_service.set_model(user, "groq", "llama-3.3-70b-versatile")
    byok_service.set_provider_override(user, "modify", "groq")

    choice = _resolve_llm_choice(user, "modify")
    assert choice.provider == "groq"
    assert choice.model == "llama-3.3-70b-versatile"
    assert choice.api_key == "gsk_real-looking-key-1234567890"
    assert choice.byok is True


@pytest.mark.django_db
def test_byok_with_no_model_chosen_falls_back_to_catalog_default(site_anthropic_for_ask):
    user = UserFactory()
    byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
    byok_service.set_provider_override(user, "ask", "anthropic")
    # Note: did NOT call set_model. Should fall back to catalog default.

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "anthropic"
    assert choice.byok is True
    assert choice.model == "claude-sonnet-4-6"  # catalog default (first entry)


# ── BYOK override with key MISSING (defensive fallback) ──────────────────


@pytest.mark.django_db
def test_byok_override_without_key_falls_back_to_site(site_anthropic_for_ask, caplog):
    """If override=anthropic but ciphertext is blank, fall back rather than crash."""
    user = UserFactory()
    cfg = UserLLMConfig.load(user)
    cfg.ask_provider_override = "anthropic"  # set directly, bypassing service validation
    cfg.save()

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "anthropic"  # site default still anthropic
    assert choice.byok is False  # NOT a BYOK call — fell back to managed
    assert choice.api_key is None


# ── force_local_ollama kill switch ───────────────────────────────────────


@pytest.mark.django_db
def test_force_local_ollama_overrides_byok(site_anthropic_for_ask, settings):
    """SiteLLMConfig.force_local_ollama beats every per-user setting."""
    settings.OLLAMA_MODEL = "llama3.1:8b"
    user = UserFactory()
    byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
    byok_service.set_provider_override(user, "ask", "anthropic")

    cfg = SiteLLMConfig.load()
    cfg.force_local_ollama = True
    cfg.save()

    choice = _resolve_llm_choice(user, "ask")
    assert choice.provider == "ollama"
    assert choice.byok is False
