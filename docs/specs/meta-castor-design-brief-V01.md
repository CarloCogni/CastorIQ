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

## Deliverables (Ordered by execution)

### 1. Evaluation Baseline (Build FIRST — two days, no more)

**What:** A frozen set of eval cases written *before* any MetaCastor feature exists, committed to Git, never modified after initial commit. This is not a framework — it's a JSONL file and a short script. The tooling comes later; the ground truth comes now.

**Why first:** MetaCastor's primary dissertation claim is comparative ("skill bank injection outperforms static prompting"). If the eval cases are authored after the implementation, the comparison is compromised by unconscious bias. The measurement must predate the thing it measures.

**Deliverables:**

1. `eval_cases.jsonl` — 20–30 cases, frozen, committed, never touched during development
2. `dev_cases.jsonl` — separate file, used freely for iterative tuning, never reported in dissertation
3. `run_eval.py` — ≤50 lines, loads JSONL, calls IntentClassifier, prints results table
4. One IFC fixture file per case set (cases are IFC-file-dependent — don't pretend otherwise)

**Eval case structure:**

```json
{
    "id": "eval_017",
    "query": "Set the fire rating of all external walls to EI120",
    "ifc_fixture": "fixtures/residential_block.ifc",
    "tier": "difficulty_tier",
    "ground_truth_intent": { "...": "..." },
    "ground_truth_post_state": null
}
```

- `tier` is **difficulty tier**, not RSAA tier. Values: `TRIVIAL`, `STANDARD`, `AMBIGUOUS`, `ESCALATION`, `ADVERSARIAL`. Metrics are reported per tier — a 90% overall score that hides 30% on `AMBIGUOUS` is worthless.
- `ground_truth_post_state` is optional. For 5–10 end-to-end cases, this describes what the IFC file should contain after successful execution. These are worth more than 50 intent-match-only cases.

**Case distribution target (not rigid, but avoid skew):**

| Difficulty tier | Count | Purpose |
|---|---|---|
| TRIVIAL | 4–5 | SET_PROPERTY on obvious targets. Sanity check only. |
| STANDARD | 8–10 | Typical Tier 1/2 operations with clear filters |
| AMBIGUOUS | 4–6 | Underspecified filters, missing context, multiple valid interpretations |
| ESCALATION | 3–4 | Requests that *should* escalate to Tier 2 or 3 |
| ADVERSARIAL | 3–4 | Catches catastrophic forgetting: Tier 3 tasks the model should not flatten to Tier 1 |

**Metrics (computed by `run_eval.py`):**

| Metric | What it measures |
|---|---|
| Operation type exact match | Correct operation selected |
| Filter accuracy | Filter resolves to correct entities against fixture |
| Parameter accuracy | pset, property, value correct |
| Tier prediction accuracy | Model assigns correct RSAA tier |
| Per-difficulty-tier breakdown | Above metrics split by tier — this is where the real story lives |

**What this is NOT:**

- Not a management command with CLI flags. A Python script is sufficient.
- Not a framework. No `--baseline --candidate` comparison UX until dissertation month.
- Not file-agnostic. Each case is coupled to a specific IFC fixture. That's honest, not a limitation.

**Execution plan:**

| Step | Time | Output |
|---|---|---|
| Select/create 1–2 IFC fixture files covering diverse entity types | 2h | `fixtures/*.ifc` |
| Author 20–30 eval cases + 10–15 dev cases | 4–6h | `eval_cases.jsonl`, `dev_cases.jsonl` |
| Write `run_eval.py` | 1h | Script that loads, runs, prints |
| Commit everything, tag `eval-baseline-v1` | 10min | Frozen in Git history |

**Rules after commit:**

1. `eval_cases.jsonl` is immutable. If you find a genuine error (wrong ground truth), document the correction in a changelog — don't silently edit.
2. All iterative prompt tuning and few-shot experimentation uses `dev_cases.jsonl` only.
3. Final dissertation numbers come from `eval_cases.jsonl` run once at the end.
4. `run_eval.py` can be upgraded to a proper harness later (dissertation month). The cases themselves don't change.

### 2. Skill Bank + Few-Shot Injection (Core thesis of MetaCastor)

**What:** A RAG-over-experience system. Instead of retrieving document chunks to answer questions, MetaCastor retrieves past successful interactions to guide the IntentClassifier on the current turn. The RSAA pipeline's structure (tiers, approvals, validators, Guardian) provides implicit supervision — the user never does extra work to train the system.

**Why this matters:** The dissertation's primary comparative claim lives here: "dynamic few-shot injection from real usage outperforms static prompt engineering for structured output generation in domain-specific LLM agents."

**Critical prerequisite:** Before building this, diagnose *why* the IntentClassifier currently fails. Few-shot injection fixes formatting and pattern deficits, not reasoning deficits. If the classifier fails because it can't reason about IFC entity hierarchies, injected examples won't help. Run the eval baseline (Deliverable 1) against the current static prompt first. Categorize failures. Only proceed if the failure mode is "the model doesn't know what good output looks like" rather than "the model can't hold the logic."

---

**SkillExample model:**

```python
class SkillExample(TimestampedModel):
    project         = ForeignKey(Project)
    query_text      = TextField()
    query_embedding = VectorField(1024)
    intent_json     = JSONField()
    entity_types    = ArrayField(CharField())   # IFC classes targeted (IfcWall, IfcSlab, etc.)
    outcome_tier    = IntegerField()            # RSAA tier at resolution
    was_approved    = BooleanField()
    commit_success  = BooleanField()
    is_organic      = BooleanField(default=True) # False for seed/synthetic examples
    created_at      = DateTimeField(auto_now_add=True)
```

**Scoring — binary, not gradient.** A SkillExample enters the retrievable pool if and only if `commit_success = True AND was_approved = True`. Everything else is excluded from retrieval. The original design's 0.0–1.0 six-tier scoring formula is premature optimization — there won't be enough data for fine-grained score distinctions to matter. Binary filter plus recency weighting (prefer recent examples) is simpler and avoids score inflation where trivial SET_PROPERTY operations dominate the high-score band and crowd out harder cases.

**Retrieval mechanism — two-stage filtering:**

1. **Entity type filter (hard gate).** Before cosine similarity, narrow the candidate pool to SkillExamples whose `entity_types` overlap with the current query's target entity types. This prevents cross-domain poisoning — "set fire rating on walls" should not retrieve examples about IfcSpace properties just because the query phrasing is similar. Entity type extraction comes from the current query's filter spec or a lightweight keyword extraction step.
2. **Cosine similarity on query_embedding (ranking).** Within the entity-type-filtered pool, rank by embedding similarity. Return top-K.

**Why entity type filtering matters:** "Set fire rating on all walls" and "set thermal resistance on all walls" are semantically close embeddings but produce completely different intent JSONs (different psets, different properties, different value types). Raw cosine similarity without entity type gating will retrieve confidently wrong examples. This is worse than no injection — it anchors the model to an incorrect pattern.

**Injection parameters:**

- **Hard cap: K=3.** On llama3.1:8b (8K context), the realistic token budget after system instructions (~2,000), entity context (~1,500), and response reserve (~1,500) leaves ~3,000 tokens. A single SkillExample with truncated intent JSON is 200–400 tokens. K=3 is the safe ceiling. On 32K+ models, K=3 is still sufficient — diminishing returns hit fast with few-shot. Do not build dynamic K computation now; hardcode K=3, test whether K=2 or K=1 performs better.
- **Example truncation:** Injected intent JSONs are stripped to essential fields only: `operation`, `filter`, `tier`, `confidence`. Full payloads waste tokens without improving few-shot guidance.
- **Injection point:** Prepended to the IntentClassifier system prompt as labeled examples before the current query.

**Cold start — be honest about it:**

The philosophy section states "no synthetic data." Cold start seeding contradicts this. Resolve the contradiction explicitly:

1. Seed the skill bank with examples from `dev_cases.jsonl`, labeled `is_organic=False`.
2. Track retrieval source — when an injected example is synthetic vs organic, log it.
3. Measure whether organic examples outperform synthetic ones over time. This is itself a publishable result: "organic few-shot examples outperform hand-crafted ones after N interactions."
4. Don't pretend seeding doesn't happen. State it in the dissertation as a cold start mitigation with a phase-out mechanism.

**What to measure (using Deliverable 1's eval baseline):**

The comparison is: static prompt (no injection) vs skill-bank-augmented prompt (with injection) on the **same frozen eval cases** with the **same IFC fixtures**.

| Comparison | What it proves |
|---|---|
| Delta on TRIVIAL tier | Probably negligible — static prompting already handles these |
| Delta on STANDARD tier | Modest expected improvement — the safe win |
| Delta on AMBIGUOUS tier | **This is the dissertation contribution.** If no delta here, the feature adds nothing meaningful |
| Delta on ESCALATION tier | Tests whether examples help the model know when *not* to be decisive |
| Organic vs synthetic examples | Cold start phase-out evidence |

If the delta is only on TRIVIAL cases, the feature is not a contribution. Be prepared for this result.

**What this is NOT:**

- Not a replacement for prompt engineering — it augments it with dynamic evidence
- Not a guarantee of improvement — the eval baseline comparison may show marginal gains
- Not a dynamic K system — hardcoded K=3 until evidence justifies complexity
- Not score-ranked — binary commit_success gate, not a gradient

---

**Execution plan:**

| Step | Dependency | Output |
|---|---|---|
| Run eval baseline with current static prompt, categorize failure modes | Deliverable 1 complete | Failure mode report — confirms few-shot is the right intervention |
| SkillExample model + migration | None | Model in DB |
| Auto-harvest hook: on successful commit, create SkillExample | Existing RSAA pipeline | Organic data starts accumulating |
| Seed skill bank from `dev_cases.jsonl` with `is_organic=False` | Deliverable 1 | Cold start mitigation |
| Entity type extraction from query (lightweight, keyword or filter-spec based) | None | Pre-filter for retrieval |
| Two-stage retriever: entity type filter → cosine similarity → top-3 | SkillExample model + pgvector | Retrieval pipeline |
| Injection into IntentClassifier system prompt | Retriever | Feature complete |
| Run eval baseline again with injection enabled, compare per-tier deltas | All above | **Primary dissertation result** |


### 3. Failure Memory + Diagnostic Loop

**What:** A failure capture and diagnosis system. Every execution failure produces a structured FailureRecord. A deterministic classification step categorizes the failure and determines whether retry is possible. If retryable, failure context is injected into the next attempt. If not retryable, the user receives actionable guidance. All retry decisions are user-initiated — the system never auto-retries.

**Core value:** Execution failures contain rich diagnostic information — the gap between what the LLM *thinks* the IFC file looks like and what it *actually* looks like. This information currently evaporates. Capturing it is the minimum viable contribution; the retry mechanism is the extension.

**Critical prerequisite:** Before building, diagnose whether failures are primarily deterministically classifiable (exception type maps cleanly to failure category) or genuinely novel (unknown patterns requiring LLM reasoning). Run a sample of real failures through the taxonomy manually. If 80%+ map deterministically, the hybrid approach is confirmed — don't default to full-LLM diagnosis.

---

**FailureRecord model — simplified, no chain tracking:**

```python
class FailureRecord(TimestampedModel):
    project         = ForeignKey(Project)
    query_text      = TextField()
    query_embedding = VectorField(1024)
    intent_json     = JSONField()
    tier            = IntegerField()
    failure_phase   = CharField()           # VALIDATION | EXECUTION | SANDBOX
    error_type      = CharField()           # from taxonomy below
    error_detail    = TextField()           # raw exception or validation message
    diagnosis       = TextField()           # human-readable explanation
    ifc_context     = JSONField()           # minimal snapshot of relevant entities
    category        = CharField()           # RETRYABLE | NON_RETRYABLE
```

Chain-tracking fields (`chain_parent`, `resolution_intent`, `retry_succeeded`, `was_retried`) are intentionally omitted. Multi-step retry chains are over-engineered for MSc scale — analytics will show ~90% of retries are depth=1. Add chain infrastructure later if data justifies it.

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

**The RETRYABLE vs NON_RETRYABLE distinction is the primary UX win.** "You can try again with a different approach" vs "your file doesn't support this — add walls in Bonsai and re-upload." Build this distinction well. The taxonomy is more valuable than the retry machinery.

---

**Classification approach — deterministic primary, LLM fallback:**

**Primary path (instant, reliable):** Map known exception patterns to the failure taxonomy using a deterministic function. This is a ~50-line dictionary lookup, not an LLM call.

```python
EXCEPTION_MAP = {
    ("IfcOpenShell", "not found"):        "NO_MATCHING_ENTITIES",
    ("FilterEngine", "zero matches"):     "NO_MATCHING_ENTITIES",
    ("IfcOpenShell", "not of type"):      "SCHEMA_VIOLATION",
    ("Validation", "property set"):       "MISSING_PSET",
    ("Validation", "property"):           "MISSING_PROPERTY",
    ("Validation", "type mismatch"):      "TYPE_MISMATCH",
    # ... extend as failure patterns emerge
}
```

**Fallback (for genuinely unknown exception patterns):** LLM call to classify the failure. Only triggers when deterministic lookup returns no match. This should be rare — most IFC-related failures map to known IfcOpenShell and validation exception patterns.

**Human-readable explanation — two options by error type:**

- **Known patterns:** Template-based. "The property set `{pset_name}` does not exist on the matched `{entity_type}` entities. You may need to add it first or check if your IFC file includes this structure." Instant, no LLM call, no latency at the worst moment (user just saw a failure).
- **Unknown patterns (LLM fallback):** Lightweight LLM generation call for `diagnosis` string only. Constrained output, small prompt. Acceptable latency trade-off because these are rare.

**Why not full-LLM diagnosis as default:** The diagnostic call happens at failure time — the worst moment for latency. Users expect instant feedback when something breaks, not 5–15 seconds of Ollama inference. Deterministic classification + templated explanation delivers instant response for 80%+ of cases.

---

**Single retry, not chains:**

The original design supports multi-step retry chains with token budget tracking, chain compression, and budget-driven depth limits. This is over-engineered for MSc-scale usage.

**What to build:**

- One "Retry with diagnosis" button for RETRYABLE failures.
- On retry, inject exactly one context block into the IntentClassifier prompt:

```
Previous attempt failed.
Category: MISSING_PSET
Diagnosis: Pset_WallCommon does not exist on the matched IfcWall entities.
Avoid: assuming Pset_WallCommon exists. Consider ADD_PSET or ADD_PROPERTY instead of SET_PROPERTY.
```

- This is ~50–100 tokens. No collision with few-shot budget (Deliverable 2). No compression needed.
- If the retry also fails, show the new diagnosis and stop. No chaining.
- NON_RETRYABLE failures get actionable guidance only, no retry button. Example: "Your IFC file contains 47 IfcSlab, 12 IfcColumn, 3 IfcBeam, but zero IfcWall. Add walls in Bonsai and re-upload."

**Why single retry is sufficient at MSc scale:** Multi-step chains require chain-parent linked lists, compression logic, and token budget calculators. In practice, users retry once. If it fails again, they rephrase manually or give up. Build for the 90% case now.

---

**Full flow (simplified):**

```
User submits modification → RSAA pipeline → Proposal → User approves → Execution
    │
    ├── SUCCESS
    │   → GitCommit + SkillExample created (Deliverable 2)
    │   → Done
    │
    └── FAILURE
        → Deterministic classification (instant)
            │
            ├── Known pattern → category + error_type + templated diagnosis
            │
            └── Unknown pattern → LLM fallback for classification + diagnosis
                │
        → FailureRecord saved
        → Branch on category:
            │
            ├── NON_RETRYABLE
            │   → Plain-language explanation + actionable external guidance
            │   → No retry button
            │
            └── RETRYABLE
                → Show diagnosis to user
                → [Retry with diagnosis] button
                → If user retries:
                    IntentClassifier receives:
                      - original query
                      - single failure context block (~50-100 tokens)
                    → New proposal → user approve/reject cycle
                    → If succeeds: SkillExample created (high value)
                    → If fails: show new diagnosis, stop. No further chaining.
```

---

**What to defer:**

| Deferred item | Reason | Revisit condition |
|---|---|---|
| `chain_parent` FK and linked list traversal | Over-engineered for MSc scale | Data shows >10% of users retry more than once |
| `resolution_intent` field linking failures to successful intents | Requires chain tracking | Chain infrastructure is built |
| Chain compression logic | No chains to compress | Chain infrastructure is built |
| Token-budget-driven retry depth | Single retry uses ~50-100 tokens, no budget pressure | Chains are implemented and need depth control |
| Failure-recovery pair injection into skill bank | Valuable but depends on chain resolution data | Enough failure→success pairs exist to measure impact |
| LLM-based classification as primary path | Deterministic covers 80%+ of cases | Failure diversity exceeds deterministic coverage |

---

**What to measure:**

| Metric | What it proves |
|---|---|
| % of failures classified deterministically vs LLM fallback | Validates hybrid approach — target: 80%+ deterministic |
| Retry success rate (single retry) | Core UX metric — does diagnosis actually help? |
| Distribution across failure taxonomy | Identifies dominant failure modes — informs deterministic code improvements |
| User retry rate (offered retry vs actually retried) | Tests whether users trust the diagnosis enough to act on it |
| Latency: failure → diagnosis displayed | Must be <1s for deterministic, <10s for LLM fallback |

---

**Execution plan:**

| Step | Dependency | Output |
|---|---|---|
| FailureRecord model + migration | None | Model in DB |
| Failure taxonomy constants + deterministic classification function | None | ~50-line mapping function |
| Hook into RSAA execution failure path: create FailureRecord on failure | Existing RSAA pipeline | Failures start being captured |
| Templated diagnosis generation for known patterns | Taxonomy | Instant human-readable explanations |
| LLM fallback for unknown patterns (constrained output, small prompt) | Ollama integration | Coverage for novel failures |
| UX: RETRYABLE → retry button, NON_RETRYABLE → guidance only | FailureRecord + WebSocket layer | User-facing feature |
| Single retry injection: failure context block in IntentClassifier prompt | FailureRecord | Retry flow complete |
| Analytics: failure distribution, retry success rate, classification coverage | FailureRecord data accumulating | Dissertation metrics |

--- 

### 4. Fine-Tuning Infrastructure (Data export + future-proofing, not automation)

**What:** A clean export path from the SkillExample and FailureRecord tables to Axolotl-compatible JSONL, plus a documented manual pipeline for QLoRA fine-tuning. No automation, no new infrastructure dependencies. The data harvesting already happens in Deliverables 2 and 3 — this deliverable only adds the serialization layer and the dissertation narrative.

**Why this is scoped down:** The original design included Celery + Redis orchestration, automated training triggers, GGUF conversion automation, Ollama model versioning, and rollback logic. That's a deployment pipeline for a training run that will produce ~200 examples at MSc scale — likely insufficient for meaningful QLoRA gains. Building automation around an event that may happen once (or never) is misallocated effort. The time is better spent on Deliverables 2 and 3 where the actual dissertation results live.

**What the data harvesting already covers (no new work needed):**

- SkillExample is auto-created on every successful commit (Deliverable 2). Fields: query, embedding, intent JSON, entity types, tier, approval status, commit success.
- FailureRecord is auto-created on every execution failure (Deliverable 3). Fields: query, embedding, intent JSON, tier, error classification, diagnosis, IFC context.
- Both accumulate passively — the user never does extra work to generate training data. This is the "implicit supervision from structured workflows" philosophy already in action.

---

**Deliverables:**

**1. Management command: `export_training_data`**

```bash
python manage.py export_training_data \
    --format jsonl \
    --project <project_id> \
    --min-examples 50
```

- Queries SkillExamples where `commit_success=True AND was_approved=True`
- Serializes to Axolotl-compatible JSONL (instruction/input/output format)
- Optionally includes high-value failure-recovery pairs if retry data exists
- 50–80 lines of code. No Celery, no Redis, no scheduler. Run manually when needed.

**JSONL output format (Axolotl-compatible):**

```json
{
    "instruction": "You are an IFC modification intent classifier. Given the user query and entity context, output a structured intent JSON.",
    "input": "User query: Set the fire rating of all external walls to EI120\nEntity context: [truncated IFC context]",
    "output": "{\"operation\": \"SET_PROPERTY\", \"filter\": {...}, \"tier\": 1, \"confidence\": 0.92}"
}
```

**2. Axolotl YAML config file**

A single working config checked into the repo. Not parameterized, not templated. Tested against a toy dataset (5 examples) to confirm it parses and Axolotl accepts it.

```yaml
base_model: meta-llama/Meta-Llama-3.1-8B-Instruct
model_type: LlamaForCausalLM
load_in_4bit: true
adapter: qlora
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
dataset_path: data/training_export.jsonl
sequence_len: 4096
micro_batch_size: 2
num_epochs: 3
learning_rate: 2e-4
output_dir: ./qlora-out
```

**3. README: manual pipeline documentation**

Step-by-step instructions covering the full chain, manually executable:

1. `python manage.py export_training_data --format jsonl --project X` → `training_export.jsonl`
2. Upload JSONL + YAML to cloud GPU (RunPod / vast.ai / Lambda Labs)
3. `accelerate launch -m axolotl.cli.train config.yaml` → LoRA adapter
4. Merge LoRA: `python -m axolotl.cli.merge_lora config.yaml --lora_model_dir ./qlora-out`
5. Convert to GGUF: `python convert_hf_to_gguf.py ./merged-model`
6. Quantize: `llama-quantize ./model.gguf ./model-Q4_K_M.gguf Q4_K_M`
7. Create Ollama model: `ollama create metacastor-v1 -f Modelfile`
8. Run eval baseline (Deliverable 1) against both base model and fine-tuned model, compare

Each step documented with expected inputs, outputs, and common failure points.

---

**Dissertation framing:**

Be explicit about the data volume reality. The honest narrative is stronger than pretending fine-tuning will produce breakthrough results:

> "At N=~200 examples, QLoRA fine-tuning did not produce statistically significant improvements over base model + skill bank injection. We provide the complete, validated pipeline as infrastructure for production-scale deployment when data volume reaches projected thresholds. The primary contribution of this work remains the skill bank's measurable improvement over static prompt engineering (Deliverable 2) and the failure memory's impact on retry success rate (Deliverable 3)."

If the colleague generates 1,000+ records, update the narrative with actual results. If they don't, the above framing stands on its own. The architecture does not depend on a resource you don't control.

---

**What was dropped from the original design and why:**

| Dropped item | Reason |
|---|---|
| Celery + Redis | New infrastructure dependency (Docker service, broker config, failure modes) for a task that runs once manually. No other feature currently needs Celery. |
| Automated training trigger (Celery beat) | Automating a one-time event is misallocated effort |
| GGUF conversion automation | Fragile tool-version dependencies; manual execution is more reliable and debuggable |
| Ollama model versioning + rollback | Over-engineered for a single training run |
| `get_llm(user)` factory modifications | Conflicts with per-user model selection architecture; revisit only if fine-tuning produces meaningful results |
| Post-training evaluation gate | The eval harness (Deliverable 1) already supports manual comparison |
| Catastrophic forgetting mitigation (perplexity tracking, mixed training data) | Relevant only when fine-tuning runs regularly; premature for MSc scale |

---

**Execution plan:**

| Step | Dependency | Time | Output |
|---|---|---|---|
| Write `export_training_data` management command | Deliverable 2 (SkillExample model exists) | 3–4h | Management command |
| Create Axolotl YAML config, test against toy dataset | None | 2h | `config.yaml` in repo |
| Write pipeline README with step-by-step instructions | None | 2h | Documentation |
| (Optional) Run one training cycle on whatever data exists near dissertation deadline | All above + accumulated data | 1 day | Results for dissertation, pass or fail |

**Total estimated effort: 1–2 days.** The rest of the time originally allocated to fine-tuning goes back to Deliverables 2 and 3.

---
---


### 5. Certified Tier 3 Reuse (Folded into Skill Bank, not a separate system)

**What:** When a Tier 3 code-generation operation succeeds all the way through execution and Git commit, preserve the generated code alongside the SkillExample. On future Tier 3 escalations, if the skill bank retrieves a relevant example that includes generated code, surface it to the LLM as a reference pattern — not as executable code to be adapted, but as a few-shot example of what working Tier 3 code looks like for this type of operation.

**Why this is not a separate deliverable:** The original design proposed a dedicated certified Tier 3 bank with its own retrieval pipeline, parameterization logic, and similarity thresholds. That's unjustified at MSc scale. Tier 3 fires rarely by design (RSAA pushes most operations to lower tiers). A realistic deployment produces 10–20 Tier 3 invocations, of which maybe 5–8 succeed. Building dedicated retrieval infrastructure for a pool of 5–8 entries is a dictionary, not a system. The Skill Bank (Deliverable 2) already handles retrieval, scoring, and injection. Tier 3 reuse is a field extension, not a new subsystem.

---

**Implementation — extend SkillExample, don't create a new model:**

```python
# Add to SkillExample (Deliverable 2)
class SkillExample(TimestampedModel):
    # ... existing fields ...
    generated_code  = TextField(null=True, blank=True)  # Tier 3 only: the IfcOpenShell script that succeeded
```

- On Tier 3 commit success, the auto-harvest hook (Deliverable 2) saves the generated code alongside the standard SkillExample fields.
- Tier 1 and Tier 2 SkillExamples have `generated_code=None`. No impact on existing retrieval.

**Injection — one conditional branch in the skill retriever:**

```python
# In the skill retriever (Deliverable 2)
if current_tier == 3 and retrieved_example.generated_code:
    # Include working code as reference in the prompt
    prompt += f"\nReference: a similar Tier 3 operation succeeded with this code:\n{retrieved_example.generated_code}\n"
    prompt += "Adapt the approach for the current request. Do not copy verbatim — entity references and property targets differ.\n"
```

- The LLM sees a working example and generates new code informed by it.
- The code still goes through the full Tier 3 safety stack (sandbox, forbidden patterns, timeout, human approval). Nothing is bypassed.
- This avoids the parameterization problem entirely. The LLM handles adaptation as part of generation, not a mechanical find-and-replace.

**Why not parameterization/adaptation logic:**

The original design assumed "swapping filter criteria is simpler than full function synthesis." In practice, Tier 3 scripts don't just differ in entity filters — they differ in property sets, value types, validation logic, and entity traversal paths. Mechanical parameterization would need to handle all of these, which is nearly as hard as generating from scratch. Letting the LLM use the prior script as a reference pattern sidesteps this entirely.

---

**What was dropped from the original design and why:**

| Dropped item | Reason |
|---|---|
| Dedicated CertifiedTier3 model | SkillExample with `generated_code` field serves the same purpose |
| Separate retrieval pipeline with distinct similarity thresholds | Skill bank retrieval (Deliverable 2) already handles this; entity-type filtering prevents cross-domain poisoning |
| Parameterization/adaptation logic | Mechanical filter swapping doesn't cover property-level differences; LLM adaptation via few-shot is more robust |
| Per-project scoping rules | SkillExample is already project-scoped (Deliverable 2) |
| "Certified" vs "uncertified" distinction | `commit_success=True` on SkillExample is the certification signal; no new concept needed |
| Similarity threshold tuning for code reuse | False positive cost is higher for code than for few-shot examples, but since the code is a reference pattern (not auto-executed), the existing retrieval threshold is sufficient |

---

**Honest limitations:**

- **Small pool.** At MSc scale, expect 5–8 Tier 3 SkillExamples with generated code. The feature may never trigger a match during evaluation. That's fine — the infrastructure costs one field and one conditional branch.
- **No guarantee of better generation.** The LLM might ignore the reference pattern or copy it too literally. Measure this: compare Tier 3 first-attempt success rate with and without code reference injection. If no improvement, the feature is inert but also cost nothing to build.
- **Stale references.** A script that worked when the IFC file had 47 walls with Pset_WallCommon may reference assumptions that no longer hold after subsequent commits. The LLM is expected to adapt, but it might not catch all stale assumptions. The full safety stack (sandbox, validation) catches execution failures — but the user still waits for a failure instead of getting it right the first time.

---

**What to measure:**

| Metric | What it proves |
|---|---|
| Tier 3 first-attempt success rate (with vs without code reference) | Whether reference patterns improve generation quality |
| Tier 3 generation time (with vs without code reference) | Whether the LLM converges faster with a reference — minor metric, human review is the real bottleneck |
| Number of Tier 3 SkillExamples with generated_code accumulated | Pool size reality check |
| Number of times code reference was actually retrieved and injected | Feature activation frequency — may be zero at MSc scale |

---

**Execution plan:**

| Step | Dependency | Time | Output |
|---|---|---|---|
| Add `generated_code` field to SkillExample + migration | Deliverable 2 complete | 30min | Schema update |
| Modify Tier 3 auto-harvest hook to save generated code on commit success | Deliverable 2 harvest hook | 1h | Code capture |
| Add conditional branch in skill retriever: if Tier 3 + code exists, include in prompt | Deliverable 2 retriever | 1h | Injection logic |
| Measure Tier 3 success rate delta when data allows | Accumulated Tier 3 data | Ongoing | Dissertation metric |

**Total estimated effort: half a day.** This is a field and a conditional, not a system.

---
---