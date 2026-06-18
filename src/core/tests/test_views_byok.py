# core/tests/test_views_byok.py
"""Tests for core.views_byok HTMX endpoints — auth, validation, partial swap."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse

from core import crypto
from core.models import UserLLMConfig
from core.services import byok_service
from environments.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _crypto_key(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    crypto._reset_cache()
    yield
    crypto._reset_cache()


def _login(client, user):
    client.force_login(user)


# ── Auth gates ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAuthGates:
    """All BYOK endpoints require an authenticated user."""

    @pytest.mark.parametrize(
        "view_name,kwargs",
        [
            ("core:byok_save_key", {"provider": "anthropic"}),
            ("core:byok_remove_key", {"provider": "groq"}),
            ("core:byok_set_provider", {"purpose": "ask"}),
            ("core:byok_set_model", {"provider": "anthropic"}),
        ],
    )
    def test_anonymous_post_redirected_or_denied(self, client, view_name, kwargs):
        response = client.post(reverse(view_name, kwargs=kwargs))
        # LoginRequiredMixin redirects to login by default.
        assert response.status_code in (302, 401, 403)


# ── save_key endpoint ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSaveKeyView:
    def test_valid_key_persists_and_returns_partial_with_hint(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_save_key", kwargs={"provider": "anthropic"}),
            {"api_key": "sk-ant-real-looking-key-1234567"},
        )

        assert response.status_code == 200
        # Partial swap returns the panel; the masked hint should appear.
        assert b"sk-ant-" in response.content
        cfg = UserLLMConfig.load(user)
        assert cfg.anthropic_api_key == "sk-ant-real-looking-key-1234567"

    def test_too_short_key_returns_400_toast(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_save_key", kwargs={"provider": "anthropic"}),
            {"api_key": "tooshort"},
        )

        assert response.status_code == 400
        assert b"HX-Trigger" not in response.content  # toast is in headers
        assert "castor:toast" in response["HX-Trigger"]
        cfg = UserLLMConfig.load(user)
        assert cfg.anthropic_api_key is None

    def test_unknown_provider_returns_400(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_save_key", kwargs={"provider": "openai"}),
            {"api_key": "valid-looking-key-1234567"},
        )
        assert response.status_code == 400


# ── remove_key endpoint ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestRemoveKeyView:
    def test_remove_clears_key_and_resets_override(self, client):
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
        byok_service.set_provider_override(user, "ask", "anthropic")
        _login(client, user)

        response = client.post(reverse("core:byok_remove_key", kwargs={"provider": "anthropic"}))

        assert response.status_code == 200
        cfg = UserLLMConfig.load(user)
        assert cfg.anthropic_api_key is None
        assert cfg.ask_provider_override == ""

    def test_remove_returns_panel_with_save_form(self, client):
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
        _login(client, user)

        response = client.post(reverse("core:byok_remove_key", kwargs={"provider": "anthropic"}))

        # Save form should reappear (we render the empty-key branch).
        assert b'name="api_key"' in response.content


# ── set_provider endpoint ────────────────────────────────────────────────


@pytest.mark.django_db
class TestSetProviderView:
    def test_set_cloud_without_key_returns_400(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_set_provider", kwargs={"purpose": "ask"}),
            {"provider": "anthropic"},
        )
        assert response.status_code == 400

    def test_set_site_default_succeeds(self, client):
        user = UserFactory()
        byok_service.save_key(user, "anthropic", "sk-ant-real-looking-key-1234567")
        byok_service.set_provider_override(user, "ask", "anthropic")
        _login(client, user)

        response = client.post(
            reverse("core:byok_set_provider", kwargs={"purpose": "ask"}),
            {"provider": ""},
        )

        assert response.status_code == 200
        cfg = UserLLMConfig.load(user)
        assert cfg.ask_provider_override == ""


# ── set_model endpoint ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestSetModelView:
    def test_set_curated_model_succeeds(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_set_model", kwargs={"provider": "anthropic"}),
            {"model": "claude-opus-4-7"},
        )

        assert response.status_code == 200
        cfg = UserLLMConfig.load(user)
        assert cfg.anthropic_model == "claude-opus-4-7"

    def test_set_unknown_model_returns_400(self, client):
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:byok_set_model", kwargs={"provider": "anthropic"}),
            {"model": "gpt-4-turbo"},
        )
        assert response.status_code == 400


# ── Settings page integration ────────────────────────────────────────────


@pytest.mark.django_db
def test_settings_page_includes_byok_panel(client):
    """The full settings page renders the BYOK panel id so HTMX can target it."""
    user = UserFactory()
    _login(client, user)

    response = client.get(reverse("core:settings"))
    assert response.status_code == 200
    assert b'id="byok-panel"' in response.content
    assert b"Bring Your Own Key" in response.content


@pytest.mark.django_db
def test_settings_page_shows_masked_hint_for_stored_key(client):
    user = UserFactory()
    byok_service.save_key(user, "groq", "gsk_real-looking-key-1234567890")
    _login(client, user)

    response = client.get(reverse("core:settings"))
    assert response.status_code == 200
    assert b"gsk_" in response.content  # hint is rendered


@pytest.mark.django_db
def test_settings_dropdown_hides_ollama_by_default(client):
    """Default deployment (SiteLLMConfig.expose_ollama_to_users=False) must NOT
    render '<option value="ollama"' in either routing dropdown."""
    user = UserFactory()
    _login(client, user)

    response = client.get(reverse("core:settings"))
    assert response.status_code == 200
    assert b'value="ollama"' not in response.content


@pytest.mark.django_db
def test_settings_dropdown_shows_ollama_when_flag_on(client):
    """Self-hosted operator flipped the flag on → dropdown includes Ollama."""
    from core.models import SiteLLMConfig

    site = SiteLLMConfig.load()
    site.expose_ollama_to_users = True
    site.save()

    user = UserFactory()
    _login(client, user)

    response = client.get(reverse("core:settings"))
    assert response.status_code == 200
    assert b'value="ollama"' in response.content
