# core/tests/test_byok_service.py
"""Tests for core.services.byok_service — save/remove/override/model mutations."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.models import UserLLMConfig
from core.services import byok_service
from environments.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _crypto_key(settings):
    """Every test runs with a freshly generated Fernet key."""
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    crypto._reset_cache()
    yield
    crypto._reset_cache()


# ── save_key ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSaveKey:
    def test_save_anthropic_encrypts_and_sets_hint(self):
        user = UserFactory()
        plaintext = "sk-ant-api03-AbCdEf1234567890_real_looking"

        cfg = byok_service.save_key(user, "anthropic", plaintext)

        assert cfg.anthropic_api_key_ciphertext  # populated
        assert cfg.anthropic_api_key_ciphertext != plaintext  # encrypted
        assert cfg.anthropic_api_key_hint == "sk-ant-…oking"
        assert cfg.anthropic_api_key == plaintext  # decrypts back
        assert cfg.keys_updated_at is not None

    def test_save_groq(self):
        user = UserFactory()
        plaintext = "gsk_abcdef1234567890ABCDEFGHIJ"

        cfg = byok_service.save_key(user, "groq", plaintext)

        assert cfg.groq_api_key == plaintext
        assert cfg.groq_api_key_hint == "gsk_abc…FGHIJ"

    def test_save_empty_key_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.save_key(user, "anthropic", "")

    def test_save_too_short_key_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.save_key(user, "anthropic", "tooshort")

    def test_save_unknown_provider_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.save_key(user, "openai", "valid-looking-key-string-12345")

    def test_save_strips_whitespace(self):
        user = UserFactory()
        plaintext = "  sk-ant-api03-WithSpacesAround12345  "
        cfg = byok_service.save_key(user, "anthropic", plaintext)
        assert cfg.anthropic_api_key == plaintext.strip()


# ── remove_key ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRemoveKey:
    def test_remove_wipes_ciphertext_and_hint(self):
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-api03-real-looking-key-1234567")

        cfg = byok_service.remove_key(user, "anthropic")

        assert cfg.anthropic_api_key_ciphertext == ""
        assert cfg.anthropic_api_key_hint == ""
        assert cfg.anthropic_api_key is None

    def test_remove_atomically_resets_ask_override(self):
        """If the Ask override pointed at the removed provider, it must reset."""
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-api03-real-looking-key-1234567")
        byok_service.set_provider_override(user, "ask", "anthropic")

        cfg = UserLLMConfig.load(user)
        assert cfg.ask_provider_override == "anthropic"

        byok_service.remove_key(user, "anthropic")

        cfg.refresh_from_db()
        assert cfg.ask_provider_override == ""

    def test_remove_atomically_resets_modify_override(self):
        user = UserFactory()
        byok_service.save_key(user, "groq", "gsk_real-looking-key-1234567890")
        byok_service.set_provider_override(user, "modify", "groq")

        byok_service.remove_key(user, "groq")

        cfg = UserLLMConfig.load(user)
        assert cfg.modify_provider_override == ""

    def test_remove_does_not_touch_override_pointing_at_other_provider(self):
        """Removing the Anthropic key must NOT clear a Groq-pointed override."""
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
        byok_service.save_key(user, "groq", "gsk_real-looking-key-1234567890")
        byok_service.set_provider_override(user, "ask", "groq")

        byok_service.remove_key(user, "anthropic")

        cfg = UserLLMConfig.load(user)
        assert cfg.ask_provider_override == "groq"


# ── set_provider_override ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestSetProviderOverride:
    def test_set_site_default(self):
        user = UserFactory()
        cfg = byok_service.set_provider_override(user, "ask", "")
        assert cfg.ask_provider_override == ""

    def test_set_ollama_does_not_require_key(self):
        # Ollama selection is gated by SiteLLMConfig.expose_ollama_to_users
        # (default False — see plan we-are-getting-close-radiant-melody).
        # Flip it on so the original "ollama needs no API key" invariant is
        # still reachable.
        from core.models import SiteLLMConfig

        site = SiteLLMConfig.load()
        site.expose_ollama_to_users = True
        site.save()

        user = UserFactory()
        cfg = byok_service.set_provider_override(user, "modify", "ollama")
        assert cfg.modify_provider_override == "ollama"

    def test_set_ollama_rejected_when_flag_off(self):
        """Default SiteLLMConfig hides Ollama — service must refuse the override."""
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError) as excinfo:
            byok_service.set_provider_override(user, "ask", "ollama")
        assert "Local Ollama isn't available" in str(excinfo.value)

    def test_set_ollama_accepted_when_flag_on(self):
        """Operator flips expose_ollama_to_users on → service accepts ollama."""
        from core.models import SiteLLMConfig

        site = SiteLLMConfig.load()
        site.expose_ollama_to_users = True
        site.save()

        user = UserFactory()
        cfg = byok_service.set_provider_override(user, "ask", "ollama")
        assert cfg.ask_provider_override == "ollama"

    def test_set_cloud_without_key_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError) as excinfo:
            byok_service.set_provider_override(user, "ask", "anthropic")
        assert "Anthropic" in str(excinfo.value)

    def test_set_cloud_with_key_succeeds(self):
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
        cfg = byok_service.set_provider_override(user, "ask", "anthropic")
        assert cfg.ask_provider_override == "anthropic"

    def test_unknown_purpose_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.set_provider_override(user, "translate", "ollama")

    def test_unknown_provider_value_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.set_provider_override(user, "ask", "openai")


# ── set_model ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSetModel:
    def test_set_anthropic_model(self):
        user = UserFactory()
        cfg = byok_service.set_model(user, "anthropic", "claude-opus-4-7")
        assert cfg.anthropic_model == "claude-opus-4-7"

    def test_set_groq_model(self):
        user = UserFactory()
        cfg = byok_service.set_model(user, "groq", "llama-3.3-70b-versatile")
        assert cfg.groq_model == "llama-3.3-70b-versatile"

    def test_clearing_to_blank_allowed(self):
        """Empty identifier is allowed — _resolve_llm_choice falls back to catalog default."""
        user = UserFactory()
        cfg = byok_service.set_model(user, "anthropic", "")
        assert cfg.anthropic_model == ""

    def test_unknown_model_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.set_model(user, "anthropic", "gpt-4-turbo")

    def test_unknown_provider_rejected(self):
        user = UserFactory()
        with pytest.raises(byok_service.BYOKValidationError):
            byok_service.set_model(user, "openai", "gpt-4-turbo")
