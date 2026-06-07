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
from dataclasses import dataclass
from typing import Any, TypeVar

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ResolvedLLM:
    """Outcome of (site config, user override, BYOK key) resolution.

    - provider/model: chosen provider tag and exact model identifier.
    - api_key: BYOK plaintext when ``byok`` is True; None otherwise (the
      builder falls back to settings.{ANTHROPIC,GROQ}_API_KEY).
    - byok: True when the request will use the user's own credentials,
      bypassing the shared-pool token budget.
    """

    provider: str
    model: str
    api_key: str | None
    byok: bool


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


class BYOKAuthError(RuntimeError):
    """Raised when a BYOK call to a cloud provider returns 401 (invalid key)."""

    def __init__(self, message: str, *, provider: str = ""):
        super().__init__(message)
        self.provider = provider


class BYOKRateLimitError(RuntimeError):
    """Raised when a BYOK call to a cloud provider returns 429 (rate-limited)."""

    def __init__(self, message: str, *, provider: str = ""):
        super().__init__(message)
        self.provider = provider


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
    # Groq production models — pricing as of 2026-06 from https://console.groq.com/docs/models
    ("groq", "llama-3.1-8b-instant"): (0.05, 0.08),
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.60),
    ("groq", "openai/gpt-oss-20b"): (0.075, 0.30),
    # Groq preview / legacy models — kept so the operator's site default (Scout)
    # and any historical LLMCallLog rows still price correctly.
    ("groq", "meta-llama/llama-4-scout-17b-16e-instruct"): (0.11, 0.34),
    ("groq", "qwen/qwen3-32b"): (0.29, 0.59),
    # Wildcard fallback for any Groq model we haven't priced yet — picks a
    # safe middle price so cost logging stays sane until the table is updated.
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

    def __init__(
        self,
        llm: BaseChatModel,
        *,
        user,
        provider: str,
        model: str,
        purpose: str,
        byok: bool = False,
    ):
        # Use object.__setattr__ to bypass any Pydantic frozen-field guards on
        # the wrapped LLM — we never write to the underlying model from here.
        object.__setattr__(self, "_llm", llm)
        object.__setattr__(self, "_user", user)
        object.__setattr__(self, "provider", provider)  # exposed so cached_system can detect
        object.__setattr__(self, "model_name", model)
        object.__setattr__(self, "_purpose", purpose)
        # BYOK calls use the user's own credentials, so they bypass the shared
        # token budget. They still write an LLMCallLog row (observability) but
        # never increment UserTokenBudget.used_today.
        object.__setattr__(self, "byok", byok)

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
        # BYOK calls use the user's own credentials — never gated by the
        # shared-pool token budget. Managed cloud calls still are.
        meters_budget = is_cloud and not self.byok
        budget = None
        if meters_budget and self._user and getattr(self._user, "is_authenticated", False):
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
            # Translate provider 401 / 429 into typed BYOK errors so the chat
            # and writeback layers can render a friendly "your key was
            # rejected, fix it in Settings" message instead of a raw traceback.
            # Detection is by status_code attribute, which both the anthropic
            # and groq SDKs set on their APIStatusError subclasses.
            if self.byok:
                status = getattr(e, "status_code", None)
                if status == 401:
                    raise BYOKAuthError(
                        f"Your {self.provider.title()} API key was rejected by the provider.",
                        provider=self.provider,
                    ) from e
                if status == 429:
                    raise BYOKRateLimitError(
                        f"Your {self.provider.title()} key hit the provider's rate limit.",
                        provider=self.provider,
                    ) from e
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
                    byok=self.byok,
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


def _resolve_llm_choice(user, purpose: str) -> ResolvedLLM:
    """Decide provider, model, and credentials for one call.

    Resolution order:

    1. ``LLM_MASTER_KILL`` is checked in get_llm() itself (raise rather than
       silently route).
    2. ``SiteLLMConfig.force_local_ollama`` always wins (kill switch — beats
       BYOK overrides too, so the operator can pin every call to local Ollama
       during an incident).
    3. ``UserLLMConfig.{purpose}_provider_override`` — if non-blank:
         - ``"ollama"``: switch to Ollama, apply the user's active_model.
         - ``"anthropic"``/``"groq"`` AND user has a stored key → BYOK
           (the user's chosen model + decrypted key).
         - ``"anthropic"``/``"groq"`` AND key blank → fall back to site
           default and log a warning. The atomic reset in ``byok_service``
           normally prevents this, but the fallback is defense-in-depth.
    4. Otherwise → SiteLLMConfig defaults (managed pool).
    """
    # Kill switch first: force_local_ollama beats every per-user setting so the
    # operator can pin every call to local Ollama during an incident.
    try:
        from core.models import SiteLLMConfig

        if SiteLLMConfig.load().force_local_ollama:
            return ResolvedLLM("ollama", settings.OLLAMA_MODEL, None, False)
    except Exception:
        pass  # DB not ready (migrations, tests) — fall through.

    site_provider, site_model = _resolve_site_config(purpose)

    if not (user and getattr(user, "is_authenticated", False)):
        # Anonymous or no user: site defaults only, no BYOK.
        if site_provider == "ollama":
            return ResolvedLLM("ollama", settings.OLLAMA_MODEL, None, False)
        return ResolvedLLM(site_provider, site_model, None, False)

    try:
        from core.models import UserLLMConfig

        cfg = UserLLMConfig.load(user)
    except Exception:
        cfg = None

    override = ""
    if cfg is not None:
        override = getattr(cfg, f"{purpose}_provider_override", "") or ""

    # No override — follow site config (Ollama may still be the user's model).
    if not override:
        if site_provider == "ollama":
            return ResolvedLLM(
                "ollama",
                (cfg.active_model if cfg else "") or settings.OLLAMA_MODEL,
                None,
                False,
            )
        return ResolvedLLM(site_provider, site_model, None, False)

    if override == "ollama":
        return ResolvedLLM(
            "ollama",
            (cfg.active_model if cfg else "") or settings.OLLAMA_MODEL,
            None,
            False,
        )

    if override in ("anthropic", "groq"):
        key = cfg.anthropic_api_key if override == "anthropic" else cfg.groq_api_key
        if not key:
            # Defense-in-depth: byok_service.remove_key() normally clears the
            # override atomically when the key is wiped. If we land here, treat
            # it as a transient race and fall back to the site default.
            logger.warning(
                "User %s set %s override to %s but has no stored key; falling back to site default",
                user.pk,
                purpose,
                override,
            )
            if site_provider == "ollama":
                return ResolvedLLM("ollama", settings.OLLAMA_MODEL, None, False)
            return ResolvedLLM(site_provider, site_model, None, False)

        catalog_model = getattr(cfg, f"{override}_model", "") or ""
        if not catalog_model:
            # User hasn't picked one yet — fall back to the curated default.
            from core.llm_catalog import default_model_for

            catalog_model = default_model_for(override)

        return ResolvedLLM(override, catalog_model, key, True)

    # Unknown override value — treat as site default rather than crashing.
    logger.warning("Unknown provider override %r for user %s; ignoring", override, user.pk)
    if site_provider == "ollama":
        return ResolvedLLM("ollama", settings.OLLAMA_MODEL, None, False)
    return ResolvedLLM(site_provider, site_model, None, False)


def resolve_model_name(user=None, purpose: str = "modify") -> str:
    """
    Backward-compatible helper used by call sites that only need the model tag.

    Honours per-user BYOK and Ollama overrides.
    """
    return _resolve_llm_choice(user, purpose).model


def friendly_provider_label(choice: ResolvedLLM) -> tuple[str, str]:
    """Render a ``ResolvedLLM`` as (provider_label, route_meta) for UI surfaces.

    Friendly names — stable across model swaps and matching the BYOK panel
    wording. Used by the settings page "About this build" block; safe to
    reuse anywhere a per-purpose route needs to be shown to a user.
    """
    if choice.provider == "ollama":
        return ("Local Ollama", "no shared-pool usage")
    if choice.byok:
        if choice.provider == "anthropic":
            return ("Your Anthropic key", "no shared-pool usage")
        if choice.provider == "groq":
            return ("Your Groq key", "no shared-pool usage")
        return (f"Your {choice.provider} key", "no shared-pool usage")
    if choice.provider == "anthropic":
        return ("Anthropic Claude", "shared host pool")
    if choice.provider == "groq":
        return ("Groq Llama", "shared host pool")
    return (choice.provider.title(), "shared host pool")


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
    choice = _resolve_llm_choice(user, purpose)

    if choice.provider != "ollama" and settings.LLM_MASTER_KILL:
        raise LLMMasterKillError(
            "LLM_MASTER_KILL is set: cloud LLM calls are disabled. "
            "Flip force_local_ollama in SiteLLMConfig to use local Ollama."
        )

    builder = _BUILDERS.get(choice.provider)
    if builder is None:
        raise LLMConfigurationError(f"Unknown LLM provider: {choice.provider!r}")

    logger.debug(
        "LLM init: provider=%s model=%s user=%s temp=%s purpose=%s byok=%s",
        choice.provider,
        choice.model,
        user,
        temperature,
        purpose,
        choice.byok,
    )
    raw = builder(
        model=choice.model,
        temperature=temperature,
        format_json=format_json,
        kwargs=kwargs,
        api_key=choice.api_key,
    )
    return TrackedChatModel(
        raw,
        user=user,
        provider=choice.provider,
        model=choice.model,
        purpose=purpose,
        byok=choice.byok,
    )


# --- provider builders ----------------------------------------------------


def _build_ollama(
    *,
    model: str,
    temperature: float,
    format_json: bool,
    kwargs: dict,
    api_key: str | None = None,  # accepted for signature parity, unused
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
    *,
    model: str,
    temperature: float,
    format_json: bool,
    kwargs: dict,
    api_key: str | None = None,
) -> BaseChatModel:
    """Build a ChatAnthropic. ``api_key`` overrides settings.ANTHROPIC_API_KEY (BYOK)."""
    effective_key = api_key or settings.ANTHROPIC_API_KEY
    if not effective_key:
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
        api_key=effective_key,
        **cloud_kwargs,
    )


def _build_groq(
    *,
    model: str,
    temperature: float,
    format_json: bool,
    kwargs: dict,
    api_key: str | None = None,
) -> BaseChatModel:
    """Build a ChatGroq. ``api_key`` overrides settings.GROQ_API_KEY (BYOK)."""
    effective_key = api_key or settings.GROQ_API_KEY
    if not effective_key:
        raise LLMConfigurationError("GROQ_API_KEY is not configured")
    from langchain_groq import ChatGroq

    cloud_kwargs = _translate_for_cloud(kwargs)
    cloud_kwargs.setdefault("max_tokens", 4096)
    if format_json:
        cloud_kwargs.setdefault("model_kwargs", {})["response_format"] = {"type": "json_object"}
    return ChatGroq(
        model=model,
        temperature=temperature,
        api_key=effective_key,
        **cloud_kwargs,
    )


_BUILDERS = {
    "ollama": _build_ollama,
    "anthropic": _build_anthropic,
    "groq": _build_groq,
}
