# core/llm_catalog.py
"""
Curated catalog of BYOK-eligible cloud models per provider.

The settings UI renders these as dropdown options. Validators in
``byok_service`` reject anything outside this list so:

1. Cost tracking stays accurate — every option here has a row in
   ``_PRICE_TABLE`` in ``core.llm``.
2. The help modal can enumerate exactly what's supported.
3. Users can't accidentally route a Modify call to a model that doesn't
   support tool calls or that triples the bill.

To add a model:
    1. Add the same string to ``_PRICE_TABLE`` in ``core/llm.py``.
    2. Append a ``ModelOption`` here.
    3. Update the help modal blurb.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    """One row in the BYOK model dropdown.

    ``identifier`` is the provider's exact model string passed to ``ChatAnthropic``
    or ``ChatGroq``. ``label`` and ``tagline`` are the human-facing display.
    """

    identifier: str
    label: str
    tagline: str


ANTHROPIC_MODELS: tuple[ModelOption, ...] = (
    ModelOption(
        identifier="claude-sonnet-4-6",
        label="Claude Sonnet 4.6",
        tagline="Balanced — best default for Ask and Modify",
    ),
    ModelOption(
        identifier="claude-opus-4-7",
        label="Claude Opus 4.7",
        tagline="Strongest reasoning — slower, ~3× the cost",
    ),
    ModelOption(
        identifier="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        tagline="Fastest and cheapest — lighter reasoning",
    ),
)


GROQ_MODELS: tuple[ModelOption, ...] = (
    # Groq Production-tier models only. Preview models (Scout, Qwen3-32B) are
    # deliberately excluded — Groq's own docs warn they "may be discontinued at
    # short notice." See https://console.groq.com/docs/models for the live list.
    ModelOption(
        identifier="llama-3.3-70b-versatile",
        label="Llama 3.3 70B Versatile",
        tagline="Strong reasoning — best default for Ask and Modify ($0.59/$0.79 per Mtok)",
    ),
    ModelOption(
        identifier="openai/gpt-oss-120b",
        label="GPT-OSS 120B",
        tagline="Open frontier model — balanced price/quality ($0.15/$0.60 per Mtok)",
    ),
    ModelOption(
        identifier="llama-3.1-8b-instant",
        label="Llama 3.1 8B Instant",
        tagline="Cheapest and fastest — light reasoning ($0.05/$0.08 per Mtok)",
    ),
)


# Public pricing pages the BYOK panel links to so users can verify the cost
# of each model directly with the provider.
PROVIDER_PRICING_URL: dict[str, str] = {
    "anthropic": "https://www.anthropic.com/pricing#api",
    "groq": "https://console.groq.com/docs/models",
}


_ANTHROPIC_IDS = frozenset(m.identifier for m in ANTHROPIC_MODELS)
_GROQ_IDS = frozenset(m.identifier for m in GROQ_MODELS)


def models_for(provider: str) -> tuple[ModelOption, ...]:
    """Return the catalog tuple for a provider, or empty for unknown."""
    if provider == "anthropic":
        return ANTHROPIC_MODELS
    if provider == "groq":
        return GROQ_MODELS
    return ()


def is_valid_model(provider: str, identifier: str) -> bool:
    """True if ``identifier`` is in the curated catalog for ``provider``."""
    if provider == "anthropic":
        return identifier in _ANTHROPIC_IDS
    if provider == "groq":
        return identifier in _GROQ_IDS
    return False


def default_model_for(provider: str) -> str:
    """Return the first (recommended) catalog entry for ``provider``."""
    options = models_for(provider)
    return options[0].identifier if options else ""
