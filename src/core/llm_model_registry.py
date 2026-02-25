# core/llm_model_registry.py
"""
Curated registry of Ollama models recommended for Castor.

Each entry maps an Ollama tag to metadata the UI needs.
Models not in this registry still work — they just show as "Unknown model"
in the selector (Ollama's /api/tags is the source of truth for availability).

VRAM estimates assume Q4_K_M quantization at 8k context.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelInfo:
    label: str                  # Human-readable display name
    family: str                 # llama | qwen | deepseek | mistral | ...
    vram_gb: float              # Approximate VRAM needed (Q4_K_M, 8k ctx)
    tier: str                   # lite | standard | performance | high_end | workstation
    description: str            # One-liner for the UI tooltip
    supports_thinking: bool = False   # Qwen3 thinking/non-thinking mode
    is_moe: bool = False        # Mixture-of-Experts (activated params < total)


# ── Tier boundaries ──────────────────────────────────────────
# lite:         ≤ 6 GB   (GTX 1660, RTX 3060 6GB)
# standard:     ≤ 8 GB   (RTX 4060, RTX 3060 Ti)
# performance:  ≤ 12 GB  (RTX 4070, RTX 3080)
# high_end:     ≤ 24 GB  (RTX 4090, RTX 4070 Ti Super)
# workstation:  > 24 GB  (A6000, multi-GPU)

VRAM_TIERS = {
    "lite":        {"max_gb": 6,  "label": "Lite (≤ 6 GB)"},
    "standard":    {"max_gb": 8,  "label": "Standard (≤ 8 GB)"},
    "performance": {"max_gb": 12, "label": "Performance (≤ 12 GB)"},
    "high_end":    {"max_gb": 24, "label": "High-End (≤ 24 GB)"},
    "workstation": {"max_gb": 999, "label": "Workstation (48 GB+)"},
}

MODEL_REGISTRY: dict[str, ModelInfo] = {

    # ── Lite ─────────────────────────────────────────────────
    "qwen3:0.6b": ModelInfo(
        label="Qwen3 0.6B",
        family="qwen",
        vram_gb=2.0,
        tier="lite",
        description="Minimal model. Fast but limited reasoning.",
        supports_thinking=True,
    ),
    "qwen3:1.7b": ModelInfo(
        label="Qwen3 1.7B",
        family="qwen",
        vram_gb=3.5,
        tier="lite",
        description="Lightweight. Suitable for simple property lookups.",
        supports_thinking=True,
    ),
    "llama3.2:3b": ModelInfo(
        label="Llama 3.2 3B",
        family="llama",
        vram_gb=4.0,
        tier="lite",
        description="Compact Meta model. Good baseline for constrained hardware.",
    ),

    # ── Standard ─────────────────────────────────────────────
    "qwen3:4b": ModelInfo(
        label="Qwen3 4B",
        family="qwen",
        vram_gb=5.0,
        tier="standard",
        description="Rivals Qwen2.5-72B on some benchmarks despite small size.",
        supports_thinking=True,
    ),
    "llama3.1:8b": ModelInfo(
        label="Llama 3.1 8B",
        family="llama",
        vram_gb=6.5,
        tier="standard",
        description="Castor's original default. Solid all-rounder.",
    ),
    "qwen3:8b": ModelInfo(
        label="Qwen3 8B",
        family="qwen",
        vram_gb=6.5,
        tier="standard",
        description="Strong reasoning + 100 language support. Great 8B option.",
        supports_thinking=True,
    ),

    # ── Performance ──────────────────────────────────────────
    "qwen3:14b": ModelInfo(
        label="Qwen3 14B",
        family="qwen",
        vram_gb=10.0,
        tier="performance",
        description="Excellent reasoning. Sweet spot for 12 GB GPUs.",
        supports_thinking=True,
    ),
    "qwen3:30b-a3b": ModelInfo(
        label="Qwen3 30B-A3B (MoE)",
        family="qwen",
        vram_gb=10.0,
        tier="performance",
        description="30B knowledge, only 3B activated per token. Best value at 12 GB.",
        supports_thinking=True,
        is_moe=True,
    ),

    # ── High-End ─────────────────────────────────────────────
    "qwen3:32b": ModelInfo(
        label="Qwen3 32B",
        family="qwen",
        vram_gb=22.0,
        tier="high_end",
        description="Matches Qwen2.5-72B quality. Single RTX 4090 territory.",
        supports_thinking=True,
    ),
    "qwen3.5:35b": ModelInfo(
        label="Qwen3.5 35B",
        family="qwen",
        vram_gb=23.0,
        tier="high_end",
        description="Latest (Feb 2026). Native multimodal, hybrid attention.",
        supports_thinking=True,
        is_moe=True,
    ),
    "llama3.3:70b": ModelInfo(
        label="Llama 3.3 70B",
        family="llama",
        vram_gb=42.0,
        tier="workstation",
        description="Meta's flagship. Needs 48 GB+ VRAM.",
    ),

    # ── Workstation ──────────────────────────────────────────
    "qwen3:235b-a22b": ModelInfo(
        label="Qwen3 235B-A22B (MoE)",
        family="qwen",
        vram_gb=60.0,
        tier="workstation",
        description="Frontier-class MoE. Multi-GPU or cloud only.",
        supports_thinking=True,
        is_moe=True,
    ),
}


def get_model_info(ollama_tag: str) -> Optional[ModelInfo]:
    """Look up model metadata by Ollama tag. Returns None if unknown."""
    return MODEL_REGISTRY.get(ollama_tag)


def get_models_for_tier(tier: str) -> dict[str, ModelInfo]:
    """Return all registry models at or below a given VRAM tier."""
    max_gb = VRAM_TIERS.get(tier, {}).get("max_gb", 0)
    return {
        tag: info
        for tag, info in MODEL_REGISTRY.items()
        if info.vram_gb <= max_gb
    }