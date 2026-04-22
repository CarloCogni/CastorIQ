# core/llm.py
"""
Centralized LLM factory.

Every service that needs a ChatOllama instance calls get_llm().
Model resolution: user's preference → settings.OLLAMA_MODEL (fallback).
"""

import logging

from django.conf import settings
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)


def resolve_model_name(user=None) -> str:
    """
    Determine which Ollama model tag to use.

    Priority:
        1. UserLLMConfig.active_model (user's choice from settings page)
        2. settings.OLLAMA_MODEL      (.env global default)
    """
    if user and user.is_authenticated:
        from core.models import UserLLMConfig  # Late import to avoid circular deps

        try:
            config = UserLLMConfig.load(user)
            if config.active_model:
                return config.active_model
        except Exception:
            # DB not ready (migrations, tests) — fall through silently
            pass

    return settings.OLLAMA_MODEL


def get_llm(user=None, temperature=0.2, format_json=False, **kwargs) -> ChatOllama:
    """
    Build a ChatOllama instance with the user's preferred model.

    Args:
        user:          User instance (optional). Resolves their model preference.
        temperature:   LLM temperature. Low for factual/structured output.
        format_json:   If True, sets format="json" for structured responses.
        **kwargs:      Passed through to ChatOllama (e.g. num_ctx, top_p).

    Returns:
        Configured ChatOllama instance.
    """
    model_name = resolve_model_name(user)

    llm_kwargs = {
        "model": model_name,
        "base_url": settings.OLLAMA_HOST,
        "temperature": temperature,
        **kwargs,
    }

    if format_json:
        llm_kwargs["format"] = "json"

    # Apply a bounded per-request timeout unless the caller overrode it.
    # Without this, a hung Ollama call holds the ASGI thread indefinitely and
    # WebSocket disconnect cancellation can never land.
    timeout_seconds = getattr(settings, "OLLAMA_REQUEST_TIMEOUT", 120.0)
    caller_client_kwargs = llm_kwargs.get("client_kwargs") or {}
    if "timeout" not in caller_client_kwargs:
        llm_kwargs["client_kwargs"] = {**caller_client_kwargs, "timeout": timeout_seconds}

    logger.debug("LLM init: model=%s, user=%s, temp=%s", model_name, user, temperature)

    return ChatOllama(**llm_kwargs)
