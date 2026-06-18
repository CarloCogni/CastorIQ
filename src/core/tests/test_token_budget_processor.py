# core/tests/test_token_budget_processor.py
"""Tests for the token_budget context processor.

Specifically covers the routing-aware banner-hide logic added with BYOK:
- Both Ask and Modify on BYOK → banner hidden.
- Both on Ollama → banner hidden.
- At least one purpose still managed → banner shown.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.context_processors import token_budget
from core.models import SiteLLMConfig, UserLLMConfig, UserTokenBudget
from core.services import byok_service
from environments.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _crypto_key(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    crypto._reset_cache()
    yield
    crypto._reset_cache()


@pytest.fixture
def site_cloud():
    """Site config: Ask=anthropic, Modify=groq (both cloud, both metered)."""
    cfg = SiteLLMConfig.load()
    cfg.ask_provider = "anthropic"
    cfg.ask_model = "claude-sonnet-4-6"
    cfg.modify_provider = "groq"
    cfg.modify_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    cfg.force_local_ollama = False
    cfg.save()
    return cfg


def _request_for(user):
    """Build a minimal request stand-in the context processor accepts."""
    req = MagicMock()
    req.user = user
    return req


@pytest.mark.django_db
def test_anonymous_user_hides_banner():
    req = MagicMock()
    req.user = MagicMock(is_authenticated=False)
    out = token_budget(req)
    assert out == {"token_show": False}


@pytest.mark.django_db
def test_managed_default_shows_banner(site_cloud):
    """Default user (no override) on site cloud → banner shown, mode=managed."""
    user = UserFactory()
    out = token_budget(_request_for(user))
    assert out["token_show"] is True
    assert out["token_mode"] == "managed"
    assert out["token_cap"] == UserTokenBudget.load(user).daily_cap


@pytest.mark.django_db
def test_both_byok_hides_banner(site_cloud):
    """When Ask and Modify both route to BYOK, the cap is meaningless → hide."""
    user = UserFactory()
    byok_service.save_key(user, "groq", "gsk_real-looking-key-1234567890")
    byok_service.set_provider_override(user, "ask", "groq")
    byok_service.set_provider_override(user, "modify", "groq")

    out = token_budget(_request_for(user))
    assert out["token_show"] is False
    assert out["token_mode"] == "byok"


@pytest.mark.django_db
def test_both_ollama_hides_banner(site_cloud):
    """Both purposes pinned to local Ollama → hide banner."""
    user = UserFactory()
    cfg = UserLLMConfig.load(user)
    cfg.ask_provider_override = "ollama"
    cfg.modify_provider_override = "ollama"
    cfg.save()

    out = token_budget(_request_for(user))
    assert out["token_show"] is False
    assert out["token_mode"] == "ollama"


@pytest.mark.django_db
def test_mixed_byok_managed_still_shows_banner(site_cloud):
    """Only one purpose on BYOK → the other still meters → keep banner."""
    user = UserFactory()
    byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
    byok_service.set_provider_override(user, "ask", "anthropic")
    # Modify still goes to managed groq via site default.

    out = token_budget(_request_for(user))
    assert out["token_show"] is True
    assert out["token_mode"] == "managed"  # mixed → mode follows the metered path


@pytest.mark.django_db
def test_cap_zero_hides_banner(site_cloud):
    """daily_cap=0 means 'unlimited' — no point showing the bar."""
    user = UserFactory()
    budget = UserTokenBudget.load(user)
    budget.daily_cap = 0
    budget.save()

    out = token_budget(_request_for(user))
    assert out["token_show"] is False


@pytest.mark.django_db
def test_force_local_ollama_hides_banner(site_cloud):
    """SiteLLMConfig kill-switch routes everything to Ollama → hide."""
    user = UserFactory()
    site_cloud.force_local_ollama = True
    site_cloud.save()

    out = token_budget(_request_for(user))
    assert out["token_show"] is False
    assert out["token_mode"] == "ollama"
