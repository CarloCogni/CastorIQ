# core/llm.py
"""
Multi-provider LLM factory.

Every service that needs an LLM calls ``get_llm(user, purpose, ...)``. The
provider (Ollama, Anthropic, Groq) is chosen at call time by the
``SiteLLMConfig`` singleton — flipping a value in admin redirects the next
call without a deploy. Local Ollama remains a first-class option, never
just a fallback.

Provider-specific kwarg translation lives at this seam so call sites stay
provider-agnostic:

  - Ollama ``num_predict`` ≡ Anthropic / Groq ``max_tokens``
  - Ollama ``format="json"`` ≡ Groq ``response_format={"type": "json_object"}``
    (Anthropic doesn't have an equivalent flag — callers should rely on
    structured prompts and tool calling instead.)
  - ``client_kwargs={"timeout": N}`` is Ollama-only; Anthropic and Groq use
    their native ``timeout`` and ``max_retries`` parameters.

Embeddings stay Ollama-only — see ``embeddings/services/embedding_service.py``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)


class LLMConfigurationError(RuntimeError):
    """Raised when a provider is requested but its API key is missing."""


class LLMMasterKillError(RuntimeError):
    """Raised when ``LLM_MASTER_KILL`` is set and a cloud call is requested."""


def _resolve_site_config(purpose: str) -> tuple[str, str]:
    """Read SiteLLMConfig if reachable; fall back to safe local defaults."""
    try:
        from core.models import SiteLLMConfig  # late import; avoids app-loading order issues

        return SiteLLMConfig.load().resolve(purpose)
    except Exception as exc:  # DB not ready (migrations, tests, fresh checkout)
        logger.debug("SiteLLMConfig unavailable (%s); falling back to Ollama", exc)
        return ("ollama", settings.OLLAMA_MODEL)


def _user_ollama_override(user) -> str | None:
    """Per-user Ollama model preference. Only consulted when provider == ollama."""
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    try:
        from core.models import UserLLMConfig

        cfg = UserLLMConfig.load(user)
        return cfg.active_model or None
    except Exception:
        return None


def resolve_model_name(user=None, purpose: str = "modify") -> str:
    """
    Backward-compatible helper used by call sites that only need the model tag.

    Honours the per-user Ollama override when the active provider is Ollama.
    """
    provider, model = _resolve_site_config(purpose)
    if provider == "ollama":
        return _user_ollama_override(user) or model
    return model


def get_llm(
    user=None,
    *,
    purpose: str = "modify",
    temperature: float = 0.2,
    format_json: bool = False,
    **kwargs,
) -> BaseChatModel:
    """
    Build a configured langchain BaseChatModel for the active provider.

    Args:
        user:        Authenticated user (optional). Per-user Ollama model overrides apply.
        purpose:     'ask' (RAG) or 'modify' (writeback). Selects which SiteLLMConfig
                     field to read. Default 'modify' keeps existing call sites correct.
        temperature: Sampling temperature. 0.2 default for factual / structured output.
        format_json: Hint that the response should be JSON. Translated per provider.
        **kwargs:    Forwarded to the provider client. Common kwargs are translated
                     so call sites can stay provider-agnostic. Provider-specific
                     unknowns are passed through as-is.
    """
    provider, model = _resolve_site_config(purpose)

    if provider == "ollama":
        override = _user_ollama_override(user)
        if override:
            model = override

    if provider != "ollama" and settings.LLM_MASTER_KILL:
        raise LLMMasterKillError(
            "LLM_MASTER_KILL is set: cloud LLM calls are disabled. "
            "Flip force_local_ollama in SiteLLMConfig to use local Ollama."
        )

    builder = _BUILDERS.get(provider)
    if builder is None:
        raise LLMConfigurationError(f"Unknown LLM provider: {provider!r}")

    logger.debug(
        "LLM init: provider=%s model=%s user=%s temp=%s purpose=%s",
        provider,
        model,
        user,
        temperature,
        purpose,
    )
    return builder(model=model, temperature=temperature, format_json=format_json, kwargs=kwargs)


# --- provider builders ----------------------------------------------------


def _build_ollama(
    *, model: str, temperature: float, format_json: bool, kwargs: dict
) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    llm_kwargs: dict = {
        "model": model,
        "base_url": settings.OLLAMA_HOST,
        "temperature": temperature,
        **kwargs,
    }
    if format_json:
        llm_kwargs["format"] = "json"

    # Bounded per-request timeout unless the caller overrode it. Without this a
    # hung Ollama call holds the ASGI thread indefinitely and WebSocket disconnect
    # cancellation can never land.
    timeout_seconds = getattr(settings, "OLLAMA_REQUEST_TIMEOUT", 120.0)
    caller_client_kwargs = llm_kwargs.get("client_kwargs") or {}
    if "timeout" not in caller_client_kwargs:
        llm_kwargs["client_kwargs"] = {**caller_client_kwargs, "timeout": timeout_seconds}

    return ChatOllama(**llm_kwargs)


def _translate_for_cloud(kwargs: dict) -> dict:
    """Map Ollama-flavoured kwargs to the cloud-provider equivalents."""
    out = dict(kwargs)
    # Ollama's num_predict is the max output tokens budget; cloud providers call this max_tokens.
    if "num_predict" in out and "max_tokens" not in out:
        out["max_tokens"] = out.pop("num_predict")
    # Ollama-specific kwargs that cloud providers don't accept.
    for k in ("num_ctx", "num_gpu", "num_thread", "client_kwargs", "format"):
        out.pop(k, None)
    return out


def _build_anthropic(
    *, model: str, temperature: float, format_json: bool, kwargs: dict
) -> BaseChatModel:
    if not settings.ANTHROPIC_API_KEY:
        raise LLMConfigurationError("ANTHROPIC_API_KEY is not configured")
    from langchain_anthropic import ChatAnthropic

    cloud_kwargs = _translate_for_cloud(kwargs)
    cloud_kwargs.setdefault("max_tokens", 4096)
    if format_json:
        # Anthropic's API doesn't have a 'JSON mode' switch — JSON output is achieved
        # via prompt structure or tool calling. We log a debug breadcrumb so call
        # sites that pass format_json=True against Anthropic see why their hint
        # is a no-op rather than failing silently.
        logger.debug("format_json=True ignored for anthropic; use structured prompts or tools")
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        api_key=settings.ANTHROPIC_API_KEY,
        **cloud_kwargs,
    )


def _build_groq(
    *, model: str, temperature: float, format_json: bool, kwargs: dict
) -> BaseChatModel:
    if not settings.GROQ_API_KEY:
        raise LLMConfigurationError("GROQ_API_KEY is not configured")
    from langchain_groq import ChatGroq

    cloud_kwargs = _translate_for_cloud(kwargs)
    cloud_kwargs.setdefault("max_tokens", 4096)
    if format_json:
        cloud_kwargs.setdefault("model_kwargs", {})["response_format"] = {"type": "json_object"}
    return ChatGroq(
        model=model,
        temperature=temperature,
        api_key=settings.GROQ_API_KEY,
        **cloud_kwargs,
    )


_BUILDERS = {
    "ollama": _build_ollama,
    "anthropic": _build_anthropic,
    "groq": _build_groq,
}
