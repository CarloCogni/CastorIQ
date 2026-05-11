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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class LLMConfigurationError(RuntimeError):
    """Raised when a provider is requested but its API key is missing."""


class LLMMasterKillError(RuntimeError):
    """Raised when ``LLM_MASTER_KILL`` is set and a cloud call is requested."""


class TokenBudgetExceededError(RuntimeError):
    """Raised when a user's UserTokenBudget would be exceeded by a request."""

    def __init__(self, message: str, *, used: int = 0, cap: int = 0):
        super().__init__(message)
        self.used = used
        self.cap = cap


# Provider price table — USD per million tokens (input / output). Numbers are
# rough public list prices as of 2026-05; exact reconciliation against the
# provider dashboards happens during dogfood. Update without ceremony as
# providers change tiers.
_PRICE_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    ("ollama", "*"): (0.0, 0.0),
    ("anthropic", "claude-sonnet-4-6"): (3.0, 15.0),
    ("anthropic", "claude-opus-4-7"): (5.0, 25.0),
    ("anthropic", "claude-haiku-4-5"): (1.0, 5.0),
    # Wildcard fallback for any future Anthropic model; mirrors Sonnet (the Ask default).
    # Cache-aware pricing (write/read tiers) is not modelled here yet — see follow-up note.
    ("anthropic", "*"): (3.0, 15.0),
    ("groq", "meta-llama/llama-4-scout-17b-16e-instruct"): (0.11, 0.34),
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),
    ("groq", "qwen/qwen3-32b"): (0.29, 0.59),
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.60),
    # Wildcard fallback for any Groq model we haven't priced yet — picks the
    # current default's prices so cost logging stays sane until the table is updated.
    ("groq", "*"): (0.11, 0.34),
}


def _price_for(provider: str, model: str) -> tuple[float, float]:
    """Lookup (input_price_per_million, output_price_per_million) for a model."""
    return _PRICE_TABLE.get((provider, model)) or _PRICE_TABLE.get((provider, "*"), (0.0, 0.0))


def _compute_cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = _price_for(provider, model)
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


def _estimate_input_tokens(messages) -> int:
    """Rough input-token estimate from a list of langchain message objects."""
    from core.token_utils import estimate_tokens

    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        else:
            text = str(content)
        total += 4 + estimate_tokens(text)
    return total


def _read_usage(response) -> tuple[int, int]:
    """Pull (tokens_in, tokens_out) from a langchain response, robust to provider quirks."""
    if response is None:
        return (0, 0)
    meta = getattr(response, "usage_metadata", None) or {}
    if isinstance(meta, dict) and meta:
        return (int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0))
    # Fallback: response_metadata.token_usage (older Anthropic / Groq shapes)
    rmeta = getattr(response, "response_metadata", {}) or {}
    usage = rmeta.get("token_usage") or rmeta.get("usage") or {}
    if usage:
        return (
            int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
            int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
        )
    return (0, 0)


class TrackedChatModel:
    """
    Lightweight wrapper that intercepts ``.invoke`` to enforce per-user token
    budgets and append an LLMCallLog row on each call.

    Pre-call: lazy-reset on new UTC day, refuse with TokenBudgetExceededError if
    ``budget.would_exceed(estimate)`` (cloud only — Ollama is free).
    Post-call: read ``response.usage_metadata``, ``record_usage``, log the row.

    Other Runnable methods (``stream``, ``__or__``, etc.) delegate to the
    wrapped model so chain composition keeps working.
    """

    def __init__(self, llm: BaseChatModel, *, user, provider: str, model: str, purpose: str):
        # Use object.__setattr__ to bypass any Pydantic frozen-field guards on
        # the wrapped LLM — we never write to the underlying model from here.
        object.__setattr__(self, "_llm", llm)
        object.__setattr__(self, "_user", user)
        object.__setattr__(self, "provider", provider)  # exposed so cached_system can detect
        object.__setattr__(self, "model_name", model)
        object.__setattr__(self, "_purpose", purpose)

    def __getattr__(self, name):
        # Falls through to the underlying LLM for everything we don't override.
        return getattr(self._llm, name)

    def __or__(self, other):
        return self._llm.__or__(other)

    def __ror__(self, other):
        return self._llm.__ror__(other)

    def invoke(self, input, config=None, **kwargs):
        import time

        from django.db import OperationalError, ProgrammingError

        from core.models import LLMCallLog, UserTokenBudget

        is_cloud = self.provider != "ollama"
        budget = None
        if is_cloud and self._user and getattr(self._user, "is_authenticated", False):
            try:
                budget = UserTokenBudget.load(self._user)
                budget.reset_if_new_day()
                estimated_in = _estimate_input_tokens(input) if isinstance(input, list) else 0
                # Reserve a conservative output budget for the pre-check.
                estimated = estimated_in + 1500
                if budget.would_exceed(estimated):
                    raise TokenBudgetExceededError(
                        f"Daily token cap reached: {budget.used_today}/{budget.daily_cap}.",
                        used=budget.used_today,
                        cap=budget.daily_cap,
                    )
            except (OperationalError, ProgrammingError):
                # DB not ready (migrations, tests). Don't block the call.
                budget = None

        t0 = time.perf_counter()
        succeeded = True
        error_type = ""
        response = None
        try:
            response = self._llm.invoke(input, config=config, **kwargs)
            return response
        except Exception as e:
            succeeded = False
            error_type = type(e).__name__
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            tokens_in, tokens_out = _read_usage(response) if succeeded else (0, 0)
            if budget and succeeded:
                budget.record_usage(tokens_in + tokens_out)
            try:
                LLMCallLog.objects.create(
                    user=self._user
                    if (self._user and getattr(self._user, "is_authenticated", False))
                    else None,
                    provider=self.provider,
                    model=self.model_name or "",
                    purpose=self._purpose,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    estimated_cost_usd=_compute_cost_usd(
                        self.provider, self.model_name or "", tokens_in, tokens_out
                    ),
                    latency_ms=elapsed_ms,
                    succeeded=succeeded,
                    error_type=error_type[:120],
                )
            except (OperationalError, ProgrammingError):
                pass  # DB not ready — observability is best-effort.


def cached_system(llm, content: str):
    """
    Build a SystemMessage marked for Anthropic prompt caching.

    Anthropic's prompt cache cuts spend by ~90% on stable system prompts. We
    encode this with a content-block list and ``cache_control={"type":
    "ephemeral"}`` — recognised by langchain-anthropic's message translator.

    For Ollama and Groq, returns a plain string-content SystemMessage so
    behaviour is unchanged. Detection accepts both raw ChatAnthropic
    instances and TrackedChatModel wrappers (which expose ``.provider``).
    """
    from langchain_core.messages import SystemMessage

    is_anthropic = getattr(llm, "provider", None) == "anthropic"
    if not is_anthropic:
        try:
            from langchain_anthropic import ChatAnthropic

            is_anthropic = isinstance(llm, ChatAnthropic)
        except ImportError:
            is_anthropic = False

    if is_anthropic:
        return SystemMessage(
            content=[
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}},
            ]
        )
    return SystemMessage(content=content)


def safe_invoke(
    func: Callable[..., _T],
    *args: Any,
    timeout: float,
    thread_name_prefix: str = "llm-call",
    **kwargs: Any,
) -> _T:
    """
    Run a blocking LLM invocation in a worker thread with a hard wall-clock cap.

    Ollama (and some httpx-based clients) advertise per-read timeouts that don't
    fire while the connection is streaming tokens — a stalled call can hold an
    ASGI thread for many minutes. Wrapping invoke() in a thread we can abandon
    gives the caller a real wall-clock cap.

    Raises ``concurrent.futures.TimeoutError`` if the call doesn't return in
    ``timeout`` seconds. The worker thread is daemonic so it won't block
    process exit; the in-flight provider request still occupies one server-side
    slot until it returns on its own.
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name_prefix)
    try:
        future = executor.submit(func, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        # Never wait on shutdown — a wedged worker would defeat the whole point.
        executor.shutdown(wait=False)


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
    raw = builder(model=model, temperature=temperature, format_json=format_json, kwargs=kwargs)
    return TrackedChatModel(raw, user=user, provider=provider, model=model, purpose=purpose)


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
