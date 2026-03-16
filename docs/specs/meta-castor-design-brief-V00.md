# MetaCastor — Design Brief

**Version:** v0.3 — Failure Memory & Diagnostic Loop added  
**Date:** March 13, 2026  
**Author:** Carlo  
**Status:** Ready for next brainstorming round

---

## What MetaCastor Is

A self-improvement layer that wraps Castor's existing RSAA agent loop. It intercepts conversations, scores outcomes using signals already present in the system (approvals, rejections, tier escalations, commit success/failure), builds a growing skill bank from real usage, captures and diagnoses execution failures to enable informed retries, and injects relevant past experience into the classifier's context window on every new turn. In its most ambitious form, it also includes dormant fine-tuning infrastructure that activates when sufficient data accumulates.

MetaCastor reasons about Castor's own performance, not about IFC data.

---

## Five Deliverables (Ordered by Risk)

### 1. Skill Bank + Few-Shot Injection (Guaranteed contribution)

**What:** A RAG-over-experience system. Instead of retrieving document chunks to answer questions, MetaCastor retrieves past successful interactions to guide the IntentClassifier on the current turn.

**SkillExample model (new):**

```python
class SkillExample(TimestampedModel):
    project         = ForeignKey(Project)
    query_text      = TextField()                # original user message
    query_embedding = VectorField(1024)           # pgvector retrieval
    intent_json     = JSONField()                 # the winning intent output
    outcome_tier    = IntegerField()              # 1, 2, or 3
    was_approved    = BooleanField()
    commit_success  = BooleanField()              # True only if execution + commit succeeded
    score           = FloatField()                # 0.0–1.0, computed from signals below
    skill_type      = CharField()                 # FAST_PATH | ESCALATION | RARE
```

**Scoring signals (automatic, zero human labeling):**

| Signal | Score |
|---|---|
| Tier 1 approved + commit success, no user edits | 1.0 |
| Tier 1 approved + commit success, user edited first | 0.7 |
| Tier 2 or 3 approved + commit success | 0.6 |
| Tier 1 escalated to Tier 2, then approved + commit success | 0.5 |
| Any proposal rejected | 0.1 |
| Confidence below 60% threshold (auto-rejected) | 0.0 |

**Injection mechanism:** Before every `IntentClassifier.classify()` call, query the skill bank by cosine similarity against the current user message embedding. Retrieve top-K highest-scoring examples and prepend them to the system prompt as few-shot demonstrations.

**Cold start mitigation:** Seed the skill bank with existing test cases and manually crafted examples from development. Don't wait for organic data.

**Why this works:** The RSAA tier system is a natural curriculum. Tier 1 successes teach the classifier when to be decisive. Tier 1→2 escalations teach it when not to oversimplify. RAV CONFLICT verdicts teach it which modifications clash with project documentation. No annotation needed beyond normal usage.

**Research question (answerable, publishable):** *Does dynamic few-shot injection from a curated real-usage skill bank outperform static prompt engineering for structured output generation in domain-specific LLM agents?*

---

### 2. Certified Tier 3 Reuse (Strong secondary contribution)

**What:** When a Tier 3 code-generation operation succeeds all the way through execution and Git commit, save it as a certified pattern. On future Tier 3 escalations, search for semantically similar certified operations before generating code from scratch.

**Critical design decision — gate on commit success, not user approval.** A user can approve code that then fails during IfcOpenShell execution. The commit is the only reliable success signal.

**Flow:**

```
Tier 3 escalation triggered
    → query certified Tier 3 bank by embedding similarity
    → if match above threshold:
        present proven code (with adapted filter parameters) for review
    → if no match:
        generate from scratch (existing Tier 3 pipeline)
    → in BOTH cases: full Tier 3 safety stack still applies
        (sandbox, forbidden patterns, timeout, code review, human approval)
```

**What this is NOT:** This is not Tier 3 → Tier 1 promotion. The code still runs through the full Tier 3 safety pipeline. We are improving the starting point for code generation, not bypassing any safety layer. The RSAA philosophy (minimal authority, tier escalation) remains intact.

**Key constraint — parameterization:** A certified script that hardcodes `GlobalId="2O2Fr$t4X7Zf8NOew3FLOH"` is useless for reuse. At minimum, literal entity references must be replaced with the filter spec from the new intent. This is a simpler problem than full function synthesis — we are swapping filter criteria, not generating new APIs.

**Scoping rule:** Certified Tier 3 operations are per-project initially. Cross-project reuse is future work (IFC schema drift between projects makes it unreliable without additional validation).

---

### 3. Fine-Tuning Infrastructure (Ambitious ceiling — build the engine, defer execution)

**What:** A dormant pipeline that collects high-scoring SkillExamples, exports them as Axolotl-compatible JSONL, and can trigger QLoRA fine-tuning when sufficient data accumulates.

**Honest assessment:** A realistic MSc deployment will generate 50–150 approved modifications. That is likely insufficient for meaningful QLoRA gains. The strategy is: build the complete pipeline so it can be demonstrated mechanically, present whatever results the data volume allows, and frame full fine-tuning as validated infrastructure ready for production-scale deployment.

**Pipeline components:**

1. Celery beat task checks data volume threshold (e.g., 200+ high-scoring examples)
2. Export task serializes SkillExamples (score ≥ 0.7) to Axolotl JSONL format
3. Axolotl YAML config template generation (QLoRA, r=16, alpha=32)
4. Cloud GPU training trigger (RunPod / vast.ai / Lambda Labs)
5. Post-training: merge LoRA → convert to GGUF → quantize to Q4_K_M
6. Ollama model versioning: `ollama create metacastor-v{n}`
7. `get_llm(user)` factory updated to resolve new model for affected project
8. Old model retained for rollback

**While training runs:** Base Ollama model continues serving normally. Celery handles the job asynchronously. Zero user interruption.

**Catastrophic forgetting mitigation:** Low LoRA rank (r=8 or r=16), mixed training data (domain + general conversational), perplexity tracking on held-out general benchmark between model versions.

---

### 4. Evaluation Harness (Build FIRST — everything is measured against this)

**What:** A management command that runs a frozen evaluation set against any model and produces a comparison table. This is the foundation for proving that Deliverables 1–3 actually improve performance.

**Interface:**

```bash
python manage.py evaluate_model \
    --model metacastor-v2 \
    --baseline qwen2.5-7b \
    --eval-set eval_cases.jsonl
```

**Evaluation set structure:** A frozen collection of `(user_message, ground_truth_intent_json)` pairs covering:

- Standard Tier 1 operations (SET_PROPERTY, ADD_PROPERTY, etc.)
- Multi-step requests that should produce chain intents
- Requests that should escalate to Tier 2 or Tier 3
- Ambiguous filter specs
- Requests that should be rejected for low confidence
- Adversarial cases (catches catastrophic forgetting: if a fine-tuned model starts outputting Tier 1 intents for Tier 3 tasks, the harness catches it)

**Metrics:**

| Metric | What it measures |
|---|---|
| Operation type exact match | Does the model pick the right operation? |
| Filter accuracy | Does the filter resolve to the correct entities? |
| Parameter accuracy | Are pset, property, value correct? |
| Tier prediction accuracy | Does the model assign the right tier? |
| Escalation rate | How often does Tier 1 fail and escalate? |
| Confidence calibration | Are high-confidence predictions actually correct? |

**Output:** Comparison table (baseline vs candidate), per-category breakdown, regression flags.

**Usage across deliverables:**

- **Skill Bank evaluation:** Run eval set with static prompt (baseline) vs skill-bank-augmented prompt (candidate). The delta is the primary MSc result.
- **Fine-tuning evaluation:** Run eval set with base model vs fine-tuned model. Must pass before any model is promoted to production.
- **Certified Tier 3:** Measure whether Tier 3 reuse reduces generation time and increases first-attempt success rate.
- **Failure Memory:** Measure retry success rate, chain resolution rate, and whether failure-recovery pairs improve skill bank quality.

---

### 5. Failure Memory + Diagnostic Loop (High-value UX and data contribution)

**The problem:** When a user-approved modification fails during execution, the failure signal is lost. If the user retries with the same or similar query, the system makes the same mistake. Execution failures contain rich diagnostic information — the gap between what the LLM *thinks* the IFC file looks like and what it *actually* looks like — and this information currently evaporates.

**What:** A failure capture, diagnosis, and guided retry system. Every execution failure produces a structured FailureRecord. A diagnostic step classifies the failure and determines whether retry is possible. If retryable, failure context is injected into the next attempt. If not retryable, the user receives actionable guidance. All retry decisions are user-initiated — the system never auto-retries.

**FailureRecord model (new):**

```python
class FailureRecord(TimestampedModel):
    project            = ForeignKey(Project)
    query_text         = TextField()
    query_embedding    = VectorField(1024)
    intent_json        = JSONField()           # what the LLM produced
    tier               = IntegerField()
    failure_phase      = CharField()           # VALIDATION | EXECUTION | SANDBOX
    error_type         = CharField()           # categorized (see taxonomy below)
    error_detail       = TextField()           # raw exception or validation message
    diagnosis          = JSONField()           # structured diagnosis
    ifc_context        = JSONField()           # minimal snapshot of relevant entities
    category           = CharField()           # RETRYABLE | NON_RETRYABLE
    was_retried        = BooleanField(default=False)
    retry_succeeded    = BooleanField(null=True)
    resolution_intent  = JSONField(null=True)  # the intent that eventually worked
    chain_parent       = ForeignKey('self', null=True)  # links retry chains together
```

**Failure taxonomy — two top-level categories:**

```
RETRYABLE — the approach was wrong, a different approach might work
    MISSING_PSET         — assumed a property set exists, it doesn't
    MISSING_PROPERTY     — pset exists but property doesn't
    TYPE_MISMATCH        — value type incompatible with property schema
    MIXED_TYPES          — filter matched heterogeneous entity types
    SCHEMA_VIOLATION     — generated code violates IFC schema constraints
    RELATIONSHIP_ERROR   — tried to modify a relationship incorrectly

NON_RETRYABLE — the file state doesn't support this request
    NO_MATCHING_ENTITIES — "no walls in the file"
    MISSING_PREREQUISITE — "walls exist but lack required structure"
    OUT_OF_SCOPE         — "geometric modification, Castor can't do this"
    AMBIGUOUS_TARGET     — "too vague to resolve even with diagnosis"
```

The distinction matters fundamentally for UX. RETRYABLE failures get a "Retry with diagnosis" button. NON_RETRYABLE failures get actionable guidance ("your file has no walls — add them in Bonsai and re-upload").

**Diagnostic step — structured output:**

On execution failure, a diagnostic LLM call receives the original intent, the generated plan/code, the exception, and a minimal IFC context snapshot. It produces:

```json
{
    "category": "RETRYABLE | NON_RETRYABLE",
    "error_type": "MISSING_PSET",
    "diagnosis": "one sentence explanation",
    "ifc_insight": "what the file actually looks like vs what was assumed",
    "suggestion": "what to try next OR what to fix externally"
}
```

**Full flow:**

```
User submits modification → RSAA pipeline → Proposal → User approves → Execution
    │
    ├── SUCCESS
    │   → GitCommit + SkillExample created
    │   → Done
    │
    └── FAILURE
        → FailureRecord created (error + IFC context snapshot)
        → Diagnostic LLM call (fast, constrained schema)
        → Branch on category:
            │
            ├── NON_RETRYABLE
            │   → Plain-language explanation + actionable external guidance
            │   → e.g. "Your IFC file contains 47 IfcSlab, 12 IfcColumn,
            │     3 IfcBeam, but zero IfcWall. Add walls in Bonsai
            │     and re-upload."
            │   → FailureRecord saved for analytics. No retry button.
            │
            └── RETRYABLE
                → Check: does failure context fit in remaining token budget?
                │
                ├── NO (budget exhausted from prior retries in chain)
                │   → "I've tried N approaches. Here's what I learned:
                │      [accumulated diagnoses]. This may need manual work."
                │   → Chain terminates.
                │
                └── YES
                    → Show diagnosis to user
                    → [Retry] button (ALWAYS user-initiated, never automatic)
                    → If user retries:
                        IntentClassifier receives:
                          - original query
                          - compressed failure chain context
                          - IFC context snapshot
                          - "avoid [error_type] because [diagnosis]"
                        → New proposal → user approve/reject cycle
                        → If succeeds:
                            FailureRecord.retry_succeeded = True
                            FailureRecord.resolution_intent = winning intent
                            SkillExample created (high-value failure-recovery pair)
                        → If fails again:
                            New FailureRecord linked via chain_parent
                            → Loop back to diagnostic step
```

**Retry depth is token-budget-driven, not a magic number.** Before each retry, the system calculates whether accumulated failure context + normal IntentClassifier prompt + response reserve fits within the model's context window. On llama3.1:8b (8K context), this practically means max ~2 retries. On 32K+ models, 3–4. The system self-regulates.

**Chain compression:** By retry N, carrying raw diagnostics from all prior attempts wastes tokens. The diagnostic step for retry N should compress the full chain into a dense summary: "Attempt 1: MISSING_PSET — Pset_WallCommon doesn't exist on matched entities. Attempt 2: SCHEMA_VIOLATION — wrong property type for FireRating." Dense, informative, token-efficient.

**Why manual retry matters (beyond safety):** Automatic retry pollutes the data. If the system retries on its own and fails, you get FailureRecords from system-generated attempts the user never intended. Keeping retry user-initiated means every record corresponds to a real user action — clean data for the skill bank.

**The resolution_intent field is the key connector to the Skill Bank.** When a failure chain eventually resolves (user retried, new approach worked), the failure-recovery pair is linked: "when you see this pattern, don't do X, do Y." These pairs are arguably more valuable than pure successes for fine-tuning data — they capture the decision boundary between what works and what doesn't.

**Data quality boundary — "wrong input" vs "wrong approach":** Sometimes the user's request is valid but unmeetable given the file state (no walls → can't set wall properties). The diagnostic step must distinguish between "Castor's approach was wrong" and "the IFC file doesn't support this request." The NON_RETRYABLE category handles this — no retry offered, just actionable guidance pointing the user to external tools (Bonsai).

**Long-term value — failure taxonomy as engineering feedback:** Failure patterns don't just train the LLM — they improve the deterministic code. If 80% of MISSING_PSET failures resolve on first retry with ADD_PSET, that suggests Tier 1 should auto-fallback more aggressively. If TYPE_MISMATCH almost never resolves through retry, the system could learn to preemptively classify those as NON_RETRYABLE. The taxonomy becomes self-refining over time.

**Open question for further brainstorming — diagnostic classification method:**

There are two possible approaches to the diagnostic step, with different tradeoffs:

- **LLM-based diagnosis (current design):** The diagnostic LLM call classifies the failure AND generates the human-readable explanation. Advantage: handles novel failure modes, produces nuanced explanations. Disadvantage: adds latency at the worst moment (user just saw a failure), costs tokens.
- **Deterministic classification + LLM explanation (hybrid):** Map exception types and validation errors to the failure taxonomy using deterministic code (e.g., `IfcOpenShell RuntimeError` with "not of type" → `SCHEMA_VIOLATION`, `FilterEngine ValueError` with "zero matches" → `NO_MATCHING_ENTITIES`). Only use the LLM for the human-readable explanation and the `ifc_insight` field. Advantage: classification is instant and reliable, LLM call is lighter. Disadvantage: can't classify truly novel failures that don't map to known exception patterns.
- **Possible hybrid:** Deterministic classification as the fast path with a fallback to LLM classification for unrecognized exception patterns. This gives you speed for known failures and flexibility for unknown ones.

This remains an open design decision — both approaches are viable and the right choice may depend on how diverse the failure modes turn out to be in practice.

---

## Cross-Cutting Concern: Dynamic Context Window Management

**Problem:** Local Ollama models (target: llama3.1:8b or similar ~8B models runnable on any decent computer without GPU) have limited context windows. The IntentClassifier prompt already includes entity context, pset registry hints, and operation schemas. Injecting few-shot examples on top of this can overflow the effective reasoning window, causing hallucinations.

**Design requirement:** The system must be model-aware and dynamically manage token budgets.

**Approach:**

1. **Model registry metadata:** Extend `core/llm_model_registry.py` to include `context_window_size` (in tokens) for each registered model.
2. **Token budget allocation:** Define a fixed budget split for the IntentClassifier prompt. Example allocation for an 8K context model:
   - System instructions + operation schemas: ~2,000 tokens (fixed)
   - Entity context (types, psets, sample properties): ~1,500 tokens (variable, can be trimmed)
   - Failure chain context (if retry): ~500–800 tokens (compressed diagnoses from prior attempts)
   - Injected few-shot examples from skill bank: **hard cap, dynamically computed** as remaining budget minus response reserve (yields fewer examples when failure context is present)
   - Response reserve: ~1,500 tokens
3. **Dynamic injection:** The skill retriever computes how many examples fit within the remaining budget. If only 1 fits, inject 1. If 0 fit, skip injection entirely and fall back to static prompt. Larger models (32K+ context) get more examples.
4. **Example truncation:** Intent JSONs in injected examples are truncated to essential fields only (operation, filter, tier, confidence). Full payloads are unnecessary for few-shot guidance.
5. **Minimum viable model:** The system must function correctly with llama3.1:8b (8K context) as the floor. Few-shot injection is a best-effort enhancement, not a hard dependency — the system degrades gracefully to static prompting when context is tight.

**Token counting:** Use a fast tokenizer estimate (e.g., `len(text) / 4` as rough heuristic, or tiktoken-equivalent for accuracy) before prompt assembly. Never send a prompt that exceeds 90% of the model's stated context window.

---

## Build Order

| Phase | Deliverable | Dependency |
|---|---|---|
| **Phase 0** | Evaluation harness + frozen eval set | None — build this first |
| **Phase 1** | SkillExample model + migration + scoring signals | Phase 0 (need harness to measure impact) |
| **Phase 2** | Skill retriever + dynamic injection into IntentClassifier | Phase 1 |
| **Phase 3** | Context window manager + model-aware token budgeting | Phase 2 |
| **Phase 4** | FailureRecord model + diagnostic step + retry flow | Phase 3 (needs token budget awareness for retry depth) |
| **Phase 5** | Failure-recovery pair linking + skill bank integration | Phase 1 + Phase 4 |
| **Phase 6** | Certified Tier 3 bank + semantic reuse lookup | Phase 1 |
| **Phase 7** | Celery + Redis integration + export pipeline | Phase 1 |
| **Phase 8** | Axolotl YAML generation + training trigger + GGUF pipeline | Phase 7 |
| **Phase 9** | Model versioning + rollback + evaluation gate | Phase 0 + Phase 8 |

---

## What MetaCastor Is NOT

- **Not a fully autonomous self-modifying agent** — humans still approve every IFC change
- **Not synthetic data generation** — the skill bank grows only from real sessions
- **Not online learning** — fine-tuning is a discrete, batched, offline event
- **Not a replacement for prompt engineering** — it augments it with dynamic evidence
- **Not a Tier 3 → Tier 1 promotion system** — certified Tier 3 reuse stays in Tier 3 with full safety stack
- **Not an auto-retry system** — all retry decisions are user-initiated; the system diagnoses and offers, never acts alone

---

## Research Narrative (for the dissertation)

> We demonstrate that structured workflow signals from a human-in-the-loop IFC modification pipeline can be repurposed as automatic supervision for agent self-improvement, measured across four mechanisms of increasing ambition: (1) dynamic few-shot injection from a curated skill bank, (2) failure memory with diagnostic retry loops that generate high-value failure-recovery training pairs, (3) semantic reuse of certified code patterns, and (4) periodic domain-specific fine-tuning. We evaluate all four against a frozen benchmark using a purpose-built evaluation harness, with the primary contributions being the skill bank's measurable improvement over static prompt engineering and the failure memory's impact on retry success rate and data quality for structured output generation.

---

*MetaCastor v0.3 — brainstorm consolidation by Carlo, March 13, 2026.*  
*Next session: deep-dive into SkillExample/FailureRecord model fields and evaluation set construction.*