# Context Window Management

**Status:** Ask-mode budget allocator is live; Modify-mode per-stage budgets are not enforced — the writeback pipeline's narrow per-stage prompts are small enough that runtime budget allocation isn't currently required. The largest residual budget concern is `Tier3Planner.entity_context`.

**Priority:** Reuse the Ask-mode allocator if the Modify-mode budget concern grows — every future feature that injects content (failure-context retry, etc.) competes blindly without token awareness, and the first symptom is hallucinations from context overflow.

---

## Problem

Local Ollama models (target: llama3.1:8b or similar ~8B models runnable on any decent computer without GPU) have limited context windows. The system packs multiple content types into each LLM call:

**Modify-mode pipeline stages:**
- `TriageClassifier`: system instructions + segmentation rules + user message. ~1–2K tokens; well under any model's window.
- `SlotExtractor`: per-kind narrow prompt + segment text. One call per segment, each ≤1K tokens.
- `EntityNameResolver`: resolver prompt + DB-grounded candidate hints (iter 1/2). ~2–4K tokens including hints.
- `Tier3Planner`: full entity_context block (existing parents + proposed names + entity class + delete targets). **The largest budget concern** — can grow with project size.

Each stage's prompt is narrow enough to hold only what it needs — there's no single LLM call carrying entity context + few-shot examples + failure context + system schema simultaneously.

**Ask mode (RAG pipeline):**
- System instructions
- Retrieved document/IFC chunks
- Conversation history (grows unbounded across a session)

Without a token budget system, there is no visibility into how full the context window is. The model silently degrades — responses become incoherent, structured output breaks, hallucinations increase — and there's no way to distinguish "the model is bad" from "the model is overloaded."

**Design requirement:** The system must be model-aware, dynamically manage token budgets, and surface usage to both the developer (for prompt assembly decisions) and the user (so they know when conversation quality is degrading).

---

## Approach

### 1. Model Registry Metadata

Extend `core/llm_model_registry.py` to include `context_window_size` (in tokens) for each registered model. This is the single source of truth for all budget calculations.

```python
# core/llm_model_registry.py

MODEL_REGISTRY = {
    "llama3.1:8b": {
        "context_window": 8192,
        "display_name": "Llama 3.1 8B",
        "category": "small",
    },
    "llama3.1:70b": {
        "context_window": 131072,
        "display_name": "Llama 3.1 70B",
        "category": "large",
    },
    "qwen2.5:7b": {
        "context_window": 32768,
        "display_name": "Qwen 2.5 7B",
        "category": "medium",
    },
    "mistral:7b": {
        "context_window": 32768,
        "display_name": "Mistral 7B",
        "category": "medium",
    },
    # Extend as models are added.
    # If a model is not in the registry, fall back to a conservative default (8192).
}

DEFAULT_CONTEXT_WINDOW = 8192  # Conservative fallback for unknown models
```

**Auto-discovery consideration:** Users can pull any Ollama model. The registry can't cover all of them. Two options:

1. **Manual registry (recommended for now).** Cover the models you actively test against. Unknown models get `DEFAULT_CONTEXT_WINDOW`. Log a warning when a user selects an unregistered model so you know to add it.
2. **Ollama API query (future).** `ollama show <model>` returns model metadata including context length. This could auto-populate the registry, but adds a startup dependency on Ollama being available. Defer this.

### 2. Token Estimation Utility

A fast, reusable utility for estimating token counts before prompt assembly.

```python
# core/token_utils.py

def estimate_tokens(text: str) -> int:
    """
    Fast token count estimate.
    
    Heuristic: len(text) / 4 is a reasonable approximation for most
    English text with LLM tokenizers. Tends to slightly overcount,
    which is safer than undercounting (we'd rather inject fewer
    examples than overflow the context).
    
    For structured content (JSON, code), the ratio is closer to 3.5
    due to punctuation and short tokens. We use 4 as a conservative
    ceiling across all content types.
    """
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """
    Estimate token count for a list of chat messages.
    Each message has overhead (~4 tokens for role/formatting).
    """
    total = 0
    for msg in messages:
        total += 4  # message framing overhead
        total += estimate_tokens(msg.get("content", ""))
    return total
```

**Why not a real tokenizer:** `tiktoken` or `transformers` tokenizers give exact counts but add dependency weight and per-model tokenizer loading. The `len(text) / 4` heuristic is consistently within 10–15% of actual counts for English text, and it overestimates — which is the safe direction. If the heuristic demonstrably causes problems (e.g., consistently leaving 20% of context unused), revisit with a model-specific tokenizer.

### 3. Token Budget Allocator

A budget system that computes how much space is available for each content type, given the model's context window and what's already packed into the prompt.

```python
# core/token_budget.py

from dataclasses import dataclass
from core.token_utils import estimate_tokens
from core.llm_model_registry import MODEL_REGISTRY, DEFAULT_CONTEXT_WINDOW


@dataclass
class TokenBudget:
    """Token budget breakdown for a single LLM call."""
    model_context_window: int
    max_usable: int              # 90% of context window — hard ceiling
    response_reserve: int        # tokens reserved for model output
    system_instructions: int     # measured, not estimated
    entity_context: int          # measured after assembly
    conversation_history: int    # measured from chat messages
    injected_content: int        # few-shot examples, failure context, etc.
    
    @property
    def total_input(self) -> int:
        return (self.system_instructions 
                + self.entity_context 
                + self.conversation_history 
                + self.injected_content)
    
    @property
    def remaining_for_injection(self) -> int:
        """How many tokens are left for dynamic content (few-shot, failure context)."""
        used = self.system_instructions + self.entity_context + self.conversation_history
        available = self.max_usable - self.response_reserve - used
        return max(0, available)
    
    @property
    def utilization_pct(self) -> float:
        """What percentage of the usable context is consumed by input."""
        if self.max_usable == 0:
            return 100.0
        return (self.total_input / self.max_usable) * 100
    
    @property
    def is_over_budget(self) -> bool:
        return self.total_input + self.response_reserve > self.max_usable


def get_context_window(model_name: str) -> int:
    """Resolve context window size for a model. Falls back to conservative default."""
    model_info = MODEL_REGISTRY.get(model_name)
    if model_info:
        return model_info["context_window"]
    # Log warning: unregistered model, using conservative default
    return DEFAULT_CONTEXT_WINDOW


RESPONSE_RESERVE = 1500  # Tokens reserved for model output — fixed across all models
SAFETY_RATIO = 0.90      # Never exceed 90% of stated context window


def compute_budget(
    model_name: str,
    system_instructions: str,
    entity_context: str,
    conversation_history: list[dict] | None = None,
    injected_content: str = "",
) -> TokenBudget:
    """
    Compute token budget for an LLM call.
    
    Call this BEFORE assembling the final prompt. Use `remaining_for_injection`
    to decide how much dynamic content (few-shot examples, failure context) to add.
    """
    context_window = get_context_window(model_name)
    max_usable = int(context_window * SAFETY_RATIO)
    
    hist_tokens = 0
    if conversation_history:
        hist_tokens = estimate_messages_tokens(conversation_history)
    
    return TokenBudget(
        model_context_window=context_window,
        max_usable=max_usable,
        response_reserve=RESPONSE_RESERVE,
        system_instructions=estimate_tokens(system_instructions),
        entity_context=estimate_tokens(entity_context),
        conversation_history=hist_tokens,
        injected_content=estimate_tokens(injected_content),
    )
```

### 4. Budget Allocation by Mode

**Modify-mode pipeline stages — 8K floor model:**

The pipeline runs four narrow stages, each with its own small budget; no single stage approaches the floor. Numbers below are typical, not enforced — runtime budget allocation isn't currently wired in for the Modify path.

| Stage | Tokens (typical) | Notes |
|---|---|---|
| `TriageClassifier` | ~1,500 | System prompt + user message |
| `SlotExtractor` (per segment) | ~800 | Per-kind narrow prompt + segment text |
| `EntityNameResolver` | ~2,000–4,000 | Includes DB-grounded candidate hints in iter 1/2 |
| `Tier2Planner` | n/a (skipped) | Plans built deterministically by `intent_assembler` |
| `Tier3Planner` | ~3,000–6,000 | The biggest concern — entity_context block (parents + proposed names + entity class + delete targets) can grow with project size |
| Response reserve | 1,500 | Fixed |

On 8K (usable: ~7,300 after 90% ceiling): every individual stage fits comfortably. Tier3Planner has the only realistic chance of approaching the floor on a large project.

On 32K (usable: ~29,000): no concern.

**Failure-context retry**: When a `RETRYABLE` failure feeds back into the pipeline via `failure_context`, the snippet is ~60 tokens — added to whichever stage's prompt the failure originated in. Negligible budget impact.

**Ask mode — unbounded conversation history is the risk:**

| Budget slot | Tokens | Type |
|---|---|---|
| System instructions | ~500 | Fixed |
| Retrieved RAG chunks | ~2,000–3,000 | Variable per query |
| Conversation history | **Grows with every turn** | This is what overflows |
| Response reserve | 1,500 | Fixed |

On 8K: after system + RAG + reserve (~4,000–5,000), only ~2,300–3,300 tokens remain for conversation history. That's roughly 10–15 message exchanges before the window fills. On 32K, it's ~100+ exchanges — rarely a problem.

**The critical insight:** On small models, Ask mode conversations become dangerous around 10–15 turns. The model doesn't crash — it silently degrades. The user has no way to know this is happening without a usage indicator.

### 5. Ask Mode Token Usage Indicator

Surface a simple usage indicator in the Ask UI so users know when conversation quality is degrading.

**Implementation:**

- After each Ask response, compute the token budget for the current conversation.
- Expose `utilization_pct` to the frontend via the existing response payload or a dedicated endpoint.
- Display as a minimal progress bar or percentage in the chat UI.

**Thresholds:**

| Utilization | Indicator | Action |
|---|---|---|
| 0–60% | Green / no indicator | Normal operation |
| 60–80% | Yellow | Show indicator, no warning |
| 80–90% | Orange + warning text | "This conversation is getting long. Responses may become less accurate. Consider starting a new session." |
| 90%+ | Red + strong warning | "Context limit nearly reached. Start a new session for best results." |

**UX notes:**

- The indicator should be unobtrusive — a thin bar below the chat input, or a small badge. Not a modal, not a toast.
- The warning at 80% is a suggestion, not a block. Users can continue — the system doesn't refuse to answer.
- The indicator is based on total conversation history tokens, not per-message. It grows monotonically within a session.

**Modify mode:** Each pipeline stage is a fresh LLM invocation with its own (small) prompt; conversation history is not threaded through. No UI indicator needed — per-stage prompts are small enough that runtime budget enforcement isn't currently wired in.

### 6. Entity Context Trimming (Graceful Degradation)

When the budget is tight (small model, long conversation, or heavy injection), entity context is the most compressible slot. Define a trimming strategy:

**Full context (~1,500 tokens):** All matched entity types, all property sets, sample property values. Used when budget allows.

**Trimmed context (~800 tokens):** Entity types and property set names only, no sample values. Used when `remaining_for_injection` would be <500 tokens with full context.

**Minimal context (~400 tokens):** Entity types and counts only ("47 IfcWall, 12 IfcSlab"). Used as last resort.

```python
def assemble_entity_context(entities, budget_remaining: int) -> str:
    """
    Assemble entity context at the appropriate detail level
    given the remaining token budget.
    """
    full = build_full_context(entities)
    if estimate_tokens(full) < budget_remaining * 0.5:
        return full  # Plenty of room
    
    trimmed = build_trimmed_context(entities)
    if estimate_tokens(trimmed) < budget_remaining * 0.5:
        return trimmed
    
    return build_minimal_context(entities)  # Last resort
```

The 0.5 factor ensures entity context never consumes more than half of the remaining budget, leaving room for dynamic injection.

---

## Integration Points

This system is shipped for Ask mode conversations. The Modify-mode pipeline does not use it; budgets there are managed implicitly by per-stage prompt size. The allocator remains available infrastructure for any future feature that needs it.

**Now (no MetaCastor dependencies):**

- Ask mode: token tracking + UI indicator prevents silent degradation on small models
- Modify-mode pipeline stages: per-stage prompts are small enough that overflow is not a current concern. `Tier3Planner.entity_context` is the single residual risk on large projects; budget-aware assembly there is a future option.
- Developer visibility: `TokenBudget` dataclass gives clear insight into where tokens are going

**Future (if richer in-prompt injection is added later):**

- Skill bank injection (Deliverable 2): call `budget.remaining_for_injection` before injecting few-shot examples. If remaining < 200 tokens, inject 0. Otherwise inject up to K=3 examples that fit.
- Failure context injection (Deliverable 4): single retry block is ~100 tokens — check it fits before injecting, fall back to no-context retry if it doesn't.
- Any future injection point: the same `compute_budget` → `remaining_for_injection` → "does it fit?" pattern applies.

---

## Minimum Viable Model

The system must function correctly with llama3.1:8b (8K context) as the floor. This means:

- All prompt assembly respects the 90% ceiling (7,372 usable tokens on 8K)
- Entity context trimming activates automatically when budget is tight
- Future few-shot injection is a best-effort enhancement, not a hard dependency — the system degrades gracefully to static prompting when context is tight
- Ask mode warns users before quality degrades, rather than silently producing bad output
- Unknown models default to 8,192 context window — conservative, never optimistic

---

## What This Is NOT

- **Not a token-level profiler.** The `len(text) / 4` heuristic is sufficient. Don't integrate tiktoken or model-specific tokenizers unless the heuristic demonstrably fails.
- **Not a dynamic K optimizer.** The budget tells you how many examples *fit*. The decision of how many to *inject* is K=3 hardcoded (future Deliverable 2). The budget is a bounds check, not an optimization algorithm.
- **Not a conversation summarizer.** When Ask mode history gets long, the system warns the user — it doesn't auto-summarize history to reclaim tokens. Summarization adds complexity, latency, and information loss. A clean session boundary is simpler and more honest.
- **Not an Ollama API integration.** Model metadata is manually registered, not auto-discovered from Ollama. Auto-discovery is a future enhancement (see Model Registry above).

---

## Execution Plan

| Step | Dependency | Time | Output |
|---|---|---|---|
| Model registry with `context_window` field + conservative default | None | 1h | `core/llm_model_registry.py` updated |
| Token estimation utilities (`estimate_tokens`, `estimate_messages_tokens`) | None | 30min | `core/token_utils.py` |
| `TokenBudget` dataclass + `compute_budget` function | Registry + utils | 2–3h | `core/token_budget.py` |
| Entity context trimming (3-tier: full / trimmed / minimal) | Token utils | 2–3h | Trimming logic in entity context assembly |
| Integrate budget into Tier3Planner entity_context | Budget module | 2–3h | Budget-aware Modify mode (only Tier3 needs this so far) |
| Integrate budget into Ask mode RAG pipeline | Budget module | 2h | Budget-aware Ask mode |
| Ask mode UI indicator (utilization bar + threshold warnings) | Ask mode integration | 3–4h | Frontend indicator |
| Logging: emit token budget breakdown on every LLM call (debug level) | Budget module | 30min | Observability |

**Total estimated effort: 2–3 days.**

---

## Testing

| Test case | What it validates |
|---|---|
| 8K model with large entity context (>2,000 tokens) | Entity trimming activates, prompt stays under 90% ceiling |
| 8K model with minimal entity context | Full context used, no unnecessary trimming |
| 32K model with same entity context | Full context always used, budget is never the bottleneck |
| Ask mode conversation at 15, 25, 40 messages | Utilization percentage increases, warnings fire at correct thresholds |
| Unknown model name | Falls back to 8,192 default, logs warning |
| Empty entity context | Budget calculation handles zero-length strings without error |
| Tier3Planner entity_context at exactly 90% ceiling | System does not exceed ceiling, response reserve is respected |