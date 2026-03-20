# core/token_budget.py
"""
Token budget management for LLM calls.

Provides dynamic context window resolution (Ollama API → registry fallback →
default) and a budget dataclass that tracks token allocation across all
prompt components (system, entity context, history, injected content).

Design constraints:
- Ollama being down must never raise an exception here.
- No Django ORM usage — safe to call at any point in the request cycle.
- No circular imports: only imports from core.token_utils and core.llm_model_registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.llm_model_registry import DEFAULT_CONTEXT_WINDOW, MODEL_REGISTRY
from core.token_utils import estimate_tokens

logger = logging.getLogger(__name__)

# Tokens reserved for the model's output; never allocated to input.
RESPONSE_RESERVE = 1500

# Never consume more than 90% of the context window for input.
SAFETY_RATIO = 0.90

# Module-level cache: model_name → context_window_size
_context_window_cache: dict[str, int] = {}


def _fetch_context_window_from_ollama(model_name: str) -> int | None:
    """
    Query the Ollama API for a model's effective context window.

    Checks in priority order:
        1. ``parameters`` string — ``num_ctx`` line (user-set Modelfile override)
        2. ``modelinfo`` dict — any key ending in ``context_length``

    Returns None if Ollama is unreachable or the model is not installed.
    Never raises.
    """
    try:
        import ollama
        from django.conf import settings

        client = ollama.Client(host=settings.OLLAMA_HOST)
        response = client.show(model_name)

        # Priority 1: explicit num_ctx in Modelfile parameters
        parameters: str = getattr(response, "parameters", "") or ""
        for line in parameters.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].lower() == "num_ctx":
                try:
                    return int(parts[1])
                except ValueError:
                    pass

        # Priority 2: modelinfo context_length key
        modelinfo: dict = getattr(response, "modelinfo", {}) or {}
        for key, value in modelinfo.items():
            if key.endswith("context_length"):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    pass

        return None
    except Exception:
        return None


def get_context_window(model_name: str) -> int:
    """
    Resolve a model's effective context window in tokens.

    Resolution order:
        1. Module-level cache (avoids repeated Ollama calls per request)
        2. Ollama API — ``client.show(model_name)``
        3. Static registry entry (``MODEL_REGISTRY``)
        4. ``DEFAULT_CONTEXT_WINDOW`` (8192) with a WARNING log

    Always returns a positive integer. Never raises.
    """
    if model_name in _context_window_cache:
        return _context_window_cache[model_name]

    # Try live Ollama lookup
    ollama_value = _fetch_context_window_from_ollama(model_name)
    if ollama_value is not None:
        _context_window_cache[model_name] = ollama_value
        logger.debug("Context window for %s: %d (from Ollama)", model_name, ollama_value)
        return ollama_value

    # Fall back to registry
    info = MODEL_REGISTRY.get(model_name)
    if info is not None:
        _context_window_cache[model_name] = info.context_window_size
        logger.debug(
            "Context window for %s: %d (from registry)", model_name, info.context_window_size
        )
        return info.context_window_size

    # Last resort: conservative default
    logger.warning(
        "Unknown model '%s' and Ollama unreachable — defaulting context window to %d",
        model_name,
        DEFAULT_CONTEXT_WINDOW,
    )
    _context_window_cache[model_name] = DEFAULT_CONTEXT_WINDOW
    return DEFAULT_CONTEXT_WINDOW


@dataclass(frozen=True)
class TokenBudget:
    """
    Immutable snapshot of token allocation for a single LLM call.

    Tracks how many tokens each prompt section consumes and whether
    the total input would exceed the safe budget ceiling.
    """

    model_context_window: int
    max_usable: int  # floor(context_window * SAFETY_RATIO) - RESPONSE_RESERVE
    response_reserve: int

    system_tokens: int
    entity_tokens: int
    history_tokens: int
    injected_tokens: int

    @property
    def total_input(self) -> int:
        """Sum of all input token sections."""
        return self.system_tokens + self.entity_tokens + self.history_tokens + self.injected_tokens

    @property
    def remaining_for_injection(self) -> int:
        """Tokens available for additional content (history, injected context)."""
        return max(0, self.max_usable - self.system_tokens - self.entity_tokens)

    @property
    def utilization_pct(self) -> float:
        """Input tokens as a percentage of the safe usable budget (0–100+)."""
        if self.max_usable <= 0:
            return 100.0
        return (self.total_input / self.max_usable) * 100.0

    @property
    def is_over_budget(self) -> bool:
        """True if total input exceeds the safe usable ceiling."""
        return self.total_input > self.max_usable


def compute_budget(
    model_name: str,
    system: str,
    entity_context: str = "",
    conversation_history: list[dict] | None = None,
    injected: str = "",
) -> TokenBudget:
    """
    Compute a token budget for an LLM call.

    Args:
        model_name:            Ollama model tag (e.g. "llama3.1:8b").
        system:                System prompt string.
        entity_context:        Entity summary injected into the user message.
        conversation_history:  List of {role, content} message dicts.
        injected:              Any additional injected text (retrieved chunks, etc.).

    Returns:
        TokenBudget with allocation breakdown and utilization metrics.
    """
    context_window = get_context_window(model_name)
    max_usable = int(context_window * SAFETY_RATIO) - RESPONSE_RESERVE
    max_usable = max(max_usable, 0)

    history_tokens = 0
    if conversation_history:
        from core.token_utils import estimate_messages_tokens

        history_tokens = estimate_messages_tokens(conversation_history)

    return TokenBudget(
        model_context_window=context_window,
        max_usable=max_usable,
        response_reserve=RESPONSE_RESERVE,
        system_tokens=estimate_tokens(system),
        entity_tokens=estimate_tokens(entity_context),
        history_tokens=history_tokens,
        injected_tokens=estimate_tokens(injected),
    )
