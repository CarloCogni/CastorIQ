# core/tests/test_site_llm_config_admin.py
"""Tests for the Site LLM Configuration admin form and its test-connection target.

Ollama's ``/api/tags`` is always mocked; no network, no model calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.admin import SiteLLMConfigForm, _build_model_choices, _ping_target
from core.models import SiteLLMConfig

CODER = "qwen2.5-coder:14b"
PROSE = "llama3.1:8b"


@pytest.fixture
def ollama_tags():
    """Ollama reports a prose model, a coder model and an embedding model."""
    resp = MagicMock()
    resp.json.return_value = {
        "models": [
            {"name": PROSE},
            {"name": CODER},
            {"name": "mxbai-embed-large:latest"},
        ]
    }
    with patch("core.admin.http_requests.get", return_value=resp):
        yield


@pytest.fixture
def ollama_down():
    with patch("core.admin.http_requests.get", side_effect=ConnectionError("refused")):
        yield


def _form(data: dict) -> SiteLLMConfigForm:
    base = {
        "ask_provider": "ollama",
        "ask_model": "",
        "modify_provider": "ollama",
        "modify_model": "",
        "force_local_ollama": False,
        "expose_ollama_to_users": False,
    }
    return SiteLLMConfigForm(data={**base, **data}, instance=SiteLLMConfig.load())


def _group_values(choices: list, group: str) -> list[str]:
    for label, members in choices[1:]:
        if label == group:
            return [value for value, _ in members]
    return []


# ── Choices ───────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_installed_ollama_tags_form_a_group_without_embeddings(ollama_tags):
    """The dropdown lists pulled tags under 'Ollama (installed)', embedding models excluded."""
    form = _form({})
    values = _group_values(form.fields["modify_model"].choices, "Ollama (installed)")
    assert CODER in values
    assert PROSE in values
    assert not any("embed" in v for v in values)


@pytest.mark.django_db
def test_env_defaults_are_selectable_even_when_not_pulled(ollama_down, settings):
    """With Ollama down the .env defaults still appear, so the form never locks the operator out."""
    settings.OLLAMA_MODEL = PROSE
    settings.MODIFY_MODEL = CODER
    values = _group_values(_build_model_choices("", []), "Ollama (installed)")
    assert values == [PROSE, CODER]


@pytest.mark.django_db
def test_saved_unknown_tag_renders_selected(ollama_down):
    """A tag saved via shell that Ollama no longer reports gets the 'Unknown' fallback group."""
    values = _group_values(_build_model_choices("phi4:latest", []), "Unknown (saved value)")
    assert values == ["phi4:latest"]


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ollama_provider_accepts_installed_tag(ollama_tags):
    form = _form({"modify_model": CODER})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["modify_model"] == CODER


@pytest.mark.django_db
def test_ollama_provider_rejects_cloud_model_id(ollama_tags):
    """A cloud id on the Ollama provider is refused: Ollama cannot serve it."""
    form = _form({"modify_model": "claude-sonnet-4-6"})
    assert not form.is_valid()
    assert "cloud model id" in form.errors["modify_model"][0]


@pytest.mark.django_db
def test_cloud_provider_still_requires_a_model(ollama_tags):
    form = _form({"modify_provider": "anthropic", "modify_model": ""})
    assert not form.is_valid()
    assert "modify_model" in form.errors


@pytest.mark.django_db
def test_cloud_provider_rejects_ollama_tag(ollama_tags):
    form = _form({"modify_provider": "anthropic", "modify_model": CODER})
    assert not form.is_valid()
    assert "not a valid" in form.errors["modify_model"][0]


# ── Test-connection target ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_ping_target_blank_ollama_uses_purpose_default(settings):
    """Blank Modify on Ollama pings the coder model, blank Ask pings the prose model."""
    settings.OLLAMA_MODEL = PROSE
    settings.MODIFY_MODEL = CODER
    cfg = SiteLLMConfig.load()
    cfg.ask_provider = cfg.modify_provider = "ollama"
    cfg.ask_model = cfg.modify_model = ""
    assert _ping_target(cfg, "modify") == ("ollama", CODER)
    assert _ping_target(cfg, "ask") == ("ollama", PROSE)


@pytest.mark.django_db
def test_ping_target_ignores_force_local_ollama():
    """The test button probes the configured provider, not the emergency switch."""
    cfg = SiteLLMConfig.load()
    cfg.modify_provider = "anthropic"
    cfg.modify_model = "claude-sonnet-4-6"
    cfg.force_local_ollama = True
    assert _ping_target(cfg, "modify") == ("anthropic", "claude-sonnet-4-6")
    assert cfg.resolve("modify")[0] == "ollama"
