# Context Window Management

**Status:** Ready for implementation (no dependencies on other MetaCastor deliverables)  
**Estimated effort:** 2–3 days  
**Priority:** Build first — every future MetaCastor feature injects content into the LLM context window. Without token awareness, injections compete blindly and the first symptom is hallucinations from context overflow, which you'll misdiagnose as a model quality problem.

---

## Problem

Local Ollama models (target: llama3.1:8b or similar ~8B models runnable on any decent computer without GPU) have limited context windows. The system already packs multiple content types into each LLM call:

**IntentClassifier (Modify mode):**
- System instructions + operation schemas
- Entity context (types, psets, sample properties)
- Chat/conversation history
- *(Future: few-shot examples from skill bank)*
- *(Future: failure diagnostic context on retry)*

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

**IntentClassifier (Modify mode) — 8K floor model:**

| Budget slot | Tokens | Type |
|---|---|---|
| System instructions + operation schemas | ~2,000 | Fixed |
| Entity context (types, psets, sample properties) | ~1,500 | Variable, can be trimmed |
| Failure diagnostic context (if retry) | ~100 | Single block, future Deliverable 4 |
| Few-shot examples from skill bank | **Remaining budget** | Dynamic, future Deliverable 2 |
| Response reserve | 1,500 | Fixed |

On 8K (usable: ~7,300 after 90% ceiling): ~2,000 + ~1,500 + 1,500 reserve = ~5,000 committed. Leaves ~2,300 for dynamic injection. At 200–400 tokens per example, that's 5–11 examples theoretically — but in practice K=3 is the hardcoded cap (Deliverable 2), so the budget check is "do 3 fit? if not, try 2, then 1, then 0."

On 32K (usable: ~29,000): same fixed costs, ~24,000 remaining. Budget is never the bottleneck; K=3 cap still applies.

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

**Modify mode:** Token budget is managed per-call (each modification is a fresh IntentClassifier invocation with its own budget). The conversation history risk doesn't apply the same way. No UI indicator needed for Modify mode initially — the budget allocator handles it internally.

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

This system is designed to be usable immediately (for Ask mode conversations and current IntentClassifier prompts) and to serve as infrastructure for future MetaCastor features.

**Now (no MetaCastor dependencies):**

- Ask mode: token tracking + UI indicator prevents silent degradation on small models
- IntentClassifier: budget-aware prompt assembly prevents overflow when entity context is large
- Developer visibility: `TokenBudget` dataclass gives clear insight into where tokens are going

**Future (when MetaCastor deliverables are built):**

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
| Integrate budget into IntentClassifier prompt assembly | Budget module | 2–3h | Budget-aware Modify mode |
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
| IntentClassifier prompt at exactly 90% ceiling | System does not exceed ceiling, response reserve is respected |