# core/llm_cloud_registry.py
"""
Curated list of cloud LLM models the operator can flip to from Django admin.

Single source of truth for the ``SiteLLMConfig`` admin dropdowns. Model IDs
here are deliberately a subset of the keys used in ``core.llm._PRICE_TABLE``
so cost tracking and the admin selector stay aligned. If a future model isn't
listed here, the price table's per-provider wildcard rows still cover cost
estimation — but the operator can't pick it from the admin without adding it
to ``CLOUD_MODELS`` first (intentional: typos in cloud model IDs silently
break runtime calls).
"""

from __future__ import annotations

from typing import NamedTuple


class CloudModel(NamedTuple):
    """A single cloud model the operator can route Ask or Modify traffic to."""

    provider: str  # 'anthropic' | 'groq'
    model_id: str  # exact ID accepted by the provider's API, e.g. 'claude-opus-4-7'
    label: str  # human-readable, shown in the admin dropdown


CLOUD_MODELS: tuple[CloudModel, ...] = (
    # Anthropic — for the Ask (RAG) pipeline.
    CloudModel("anthropic", "claude-opus-4-7", "Claude Opus 4.7 (highest quality)"),
    CloudModel("anthropic", "claude-sonnet-4-6", "Claude Sonnet 4.6 (balanced — Ask default)"),
    CloudModel("anthropic", "claude-haiku-4-5", "Claude Haiku 4.5 (cheapest)"),
    # Groq Production-tier models only. Preview models (Scout, Qwen3-32B) are
    # deliberately excluded — Groq's own docs warn they "may be discontinued at
    # short notice." See https://console.groq.com/docs/models for the live list.
    # Scout remains in core.llm._PRICE_TABLE so any historical LLMCallLog rows
    # still price correctly, but operators should not pick it for new traffic.
    CloudModel(
        "groq",
        "llama-3.3-70b-versatile",
        "Llama 3.3 70B Versatile (production — strong reasoning, $0.59/$0.79 per Mtok)",
    ),
    CloudModel(
        "groq",
        "openai/gpt-oss-120b",
        "GPT-OSS 120B (production — frontier reach for Tier 3 code-gen, $0.15/$0.60 per Mtok)",
    ),
    CloudModel(
        "groq",
        "llama-3.1-8b-instant",
        "Llama 3.1 8B Instant (production — cheapest and fastest, $0.05/$0.08 per Mtok)",
    ),
    CloudModel(
        "groq",
        "openai/gpt-oss-20b",
        "GPT-OSS 20B (production — fastest at ~1000 t/s, $0.075/$0.30 per Mtok)",
    ),
)


PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic Claude",
    "groq": "Groq",
}


def models_for(provider: str) -> tuple[CloudModel, ...]:
    """Return all curated models for the given provider, in registry order."""
    return tuple(m for m in CLOUD_MODELS if m.provider == provider)


def is_valid(provider: str, model_id: str) -> bool:
    """True if the (provider, model_id) pair is a curated, currently-supported model."""
    return any(m.provider == provider and m.model_id == model_id for m in CLOUD_MODELS)
