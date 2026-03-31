# D2 — Skill Bank + Few-Shot Injection

> Status: **Complete**
> Depends on: D1 (eval baseline frozen before implementation)

---

## What Was Built

| File | Purpose |
|---|---|
| `src/metacastor/models.py` | `SkillExample` model — stores approved interactions |
| `src/metacastor/migrations/0001_initial.py` | DB migration — `metacastor_skillexample` table with `vector(1024)` column |
| `src/metacastor/services/entity_type_extractor.py` | Keyword + regex scanner → IFC type hints from raw user messages |
| `src/metacastor/services/skill_retriever.py` | Two-stage retrieval: entity type pre-filter → cosine similarity top-K=3 |
| `src/metacastor/services/skill_harvester.py` | Post-commit hook — creates `SkillExample` records automatically |
| `src/metacastor/admin.py` | `SkillExampleAdmin` — browse and inspect the skill bank |
| `src/metacastor/management/commands/seed_skill_bank.py` | Cold-start seeding from `dev_cases.jsonl` |
| `src/writeback/services/intent_classifier.py` | `_format_skill_injection()` + backward-compatible `skill_examples` param on `classify()` |
| `src/writeback/services/modification_service.py` | Retrieval before budget sizing, injection-aware token budget, harvester hook in `execute()` |

---

## The Core Thesis

The system learns from its own approved interactions. Every time a user approves and commits a modification:

```
User request → IntentClassifier (+ injected examples) → Proposal → Approval → Execute → Commit
                                                                                         ↓
                                                                              SkillExample created
                                                                         (harvested for future queries)
```

On the next classification call, the most semantically similar past interactions are injected into the
classifier's system prompt as few-shot examples. The LLM is anchored to real patterns, not invented ones.

---

## How Retrieval Works

### Two-Stage Pipeline

**Stage 1 — Entity type pre-filter (hard gate, Python-level)**

Before cosine similarity, the candidate pool is narrowed to examples whose `entity_types` overlap
with the types extracted from the current query. This prevents cross-domain poisoning.

Example: `"Set fire rating on walls"` → extracts `["IfcWall"]` → only wall examples are candidates.
Without this gate, `"Set thermal resistance on walls"` would poison wall-fire-rating lookups because
the embeddings are semantically close but the intents are completely different.

The extractor is a lightweight keyword scan + IfcXxx regex — it runs in microseconds before the LLM call.
It falls back to an empty list (no filter → all candidates), which is safe.

**Stage 2 — Cosine similarity (pgvector)**

Within the entity-type-filtered pool, examples are ranked by embedding cosine distance and top-K=3 are returned.

K=3 is hardcoded. On llama3.1:8b (8K context), the realistic budget after system instructions (~2,000 tokens),
entity context (~1,500), and response reserve (~1,500) leaves ~3,000 tokens. Three injected examples consume
~600–900 tokens. Dynamic K adds complexity without evidence of benefit.

### Retrieval Gate

Only examples where **`was_approved=True AND commit_success=True`** are retrievable. Binary, not gradient.
This is intentional — there is not yet enough data for fine-grained scoring to matter, and gradient scoring
would inflate trivial SET_PROPERTY operations (easy, high-confidence) over harder AMBIGUOUS cases.

### Token Budget Interaction

Skill injection reduces the budget available for entity context. This is accounted for explicitly:

```python
# In modification_service.propose():
skill_examples = _retrieve_skills(user_message)
injection_text = _format_skill_injection(skill_examples)

# Budget computed AFTER injection is known — entity context sized to fit
prelim_budget = compute_budget(model_name, system=CLASSIFY_SYSTEM_PROMPT, injected=injection_text)
entity_context = classifier.build_entity_context(all_entities,
                    available_tokens=prelim_budget.remaining_for_injection)
```

Entity context will be trimmed more aggressively when examples are injected. This is correct — on
AMBIGUOUS cases (the primary target), few-shot examples are more valuable than extra entity detail.

---

## Cold Start: Seeding from dev_cases.jsonl

New projects start with zero organic examples. To mitigate this:

```bash
uv run manage.py seed_skill_bank
```

This loads `dev_cases.jsonl` (35 curated development cases) and creates synthetic `SkillExample` records
with `is_organic=False, project=None` (global — available to all projects).

The seeded examples are honest about their origin — `is_organic=False` is tracked, logged during retrieval,
and will eventually be filterable in analysis. The dissertation acknowledges this as cold-start mitigation,
not training data.

**The command is idempotent** — safe to re-run. Uses `get_or_create` keyed on `(query_text, is_organic=False)`.

```bash
uv run manage.py seed_skill_bank --clear   # wipe synthetic examples and re-seed
uv run manage.py seed_skill_bank --skip-embeddings  # seed without Ollama (records stored, but not retrievable until embedded)
```

---

## Injection Format

When `skill_examples` is non-empty, this block is appended to `SYSTEM_PROMPT` in `classify()`:

```
## Learned Examples (from similar past requests)

Example 1:
Query: "Set fire rating to EI60 for all external walls"
Intent: {"operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall"}, "tier": 1, "confidence": 0.9}

Example 2:
...
```

The injected intent is stripped to `operation`, `filter`, `tier`, `confidence` — not the full intent JSON.
Full payloads waste tokens without improving few-shot guidance. The retriever already returns truncated dicts.

The `SYSTEM_PROMPT` module-level constant is never modified — only a local `system_content` variable inside
`classify()` gets the block appended. This is critical: `modification_service.py` imports `SYSTEM_PROMPT`
directly for budget pre-computation, and that value must remain stable.

---

## Organic Harvest

Every approved+committed proposal is automatically harvested after `execute()`:

```python
# In modification_service.execute(), after proposal.save(status=APPLIED):
harvest_skill_example(proposal, commit_success=True)
```

Harvest conditions (both must be true):
- `proposal.reviewed_by is not None` — a human explicitly reviewed it
- `commit_success=True` — the IFC write and Git commit completed

The harvester is fully non-blocking. Its entire body is wrapped in `try/except Exception`, logged as a warning,
and the pipeline continues regardless. A harvest failure never blocks a commit.

---

## Seeing It In Action

### Admin UI

Browse the skill bank at `/admin/metacastor/skillexample/`. Filter by `is_organic`, `outcome_tier`,
`was_approved`. The `query_embedding` field is read-only (a 1024-float vector is not human-readable).

### Debug Logs

With `DEBUG=True`, the following log lines confirm injection is active:

```
DEBUG metacastor.services.skill_retriever  Entity type filter (['IfcWall']): 12 candidates remaining.
DEBUG writeback.services.intent_classifier  Injecting 3 skill examples into classifier.
DEBUG writeback.services.modification_service  Modify token budget — model=llama3.1:8b util=72% total=5834/8192 skill_examples=3
```

`skill_examples=0` means the retriever returned nothing (bank empty, no type match, or Ollama unavailable
during embedding). The pipeline continues normally with the static prompt.

### Django Shell Smoke Test

```python
from metacastor.models import SkillExample
from metacastor.services.skill_retriever import retrieve
from metacastor.services.entity_type_extractor import extract_entity_types

# Check bank size
print(SkillExample.objects.count())

# Entity type extraction
print(extract_entity_types("Set fire rating to EI120 for all external walls"))
# → ['IfcWall']

# Retrieval
results = retrieve("Set fire rating to EI120 for all external walls")
for r in results:
    print(f'[T{r["tier"]}] {r["operation"]} | {r["query_text"][:60]}')
```

---

## Measuring the Impact

Re-run the eval harness with the skill bank seeded:

```bash
cd src
uv run python metacastor/eval/run_eval.py
```

Compare the output against the D1 frozen baseline:

| Tier | D1 (static) | D2 (with injection) | Delta |
|---|---|---|---|
| TRIVIAL | 85% (params) | — | Expected: negligible |
| STANDARD | 76% (params) | — | Expected: modest |
| **AMBIGUOUS** | **0% (params), 40% (tier)** | — | **Primary dissertation result** |
| ESCALATION | 15% (tier) | — | Expected: improvement from T2 examples |
| ADVERSARIAL | 10% (operation) | — | Expected: improvement from T3 labelled examples |

**The immutability rule still applies**: `eval_cases.jsonl` is never edited. The comparison is always
static prompt (D1 run) vs injection-augmented prompt (D2 run), on the same frozen 115 cases.

### D2 Measured Results (2026-03-30, llama3.1:8b, T=0, synthetic cold-start bank)

| Tier | Metric | D1 (static) | D2 (injected) | Delta |
|---|---|---|---|---|
| TRIVIAL | operation | 95% | 90% | -5% |
| TRIVIAL | tier | 100% | 100% | — |
| TRIVIAL | filter | 95% | 100% | +5% |
| TRIVIAL | params | 85% | 85% | — |
| STANDARD | operation | 88% | 84% | -4% |
| STANDARD | tier | 84% | 84% | — |
| STANDARD | filter | 76% | 80% | +4% |
| STANDARD | params | 76% | 76% | — |
| **AMBIGUOUS** | **tier** | **40%** | **57%** | **+17pp** |
| **AMBIGUOUS** | **params** | **0%** | **27%** | **+27pp** |
| **AMBIGUOUS** | operation | 83% | 73% | -10% |
| ESCALATION | tier | 15% | 25% | +10% |
| **ADVERSARIAL** | **operation** | **10%** | **50%** | **+40pp** |
| **ADVERSARIAL** | **params** | **35%** | **75%** | **+40pp** |
| **OVERALL** | **tier** | **63%** | **70%** | **+7pp** |
| **OVERALL** | **filter** | **60%** | **71%** | **+11pp** |
| **OVERALL** | **params** | **41%** | **54%** | **+13pp** |

#### Interpretation

**Primary claim confirmed.** AMBIGUOUS tier accuracy +17pp and parameters +27pp (from floor zero) directly
validates the MetaCastor thesis: injecting past approved examples anchors the model to correct escalation
patterns and prevents overconfident value-filling on ambiguous inputs.

**Unexpected ADVERSARIAL improvement (+40pp operation, +40pp params).** Injected examples appear to teach
the model to correctly decline or reframe edge-case requests. Not the primary hypothesis — a secondary
finding.

**Negative transfer on TRIVIAL/STANDARD operation (-4–5pp).** Classic few-shot effect: easy cases receive
examples anchored to complex patterns, causing mild over-engineering. Small magnitude, acceptable trade-off.

**ESCALATION tier accuracy still low (25%).** The synthetic seed bank (`dev_cases.jsonl`) contains no
Tier 2/3 examples. The model has no injected escalation patterns to learn from. This will improve as
organic harvest accumulates T2/T3 approved commits.

These measurements used synthetic cold-start examples (`is_organic=False`). Organic examples from real
usage are expected to improve results further, particularly on ESCALATION.

---

## Design Decisions

**Why entity type pre-filter and not pure cosine similarity?**
Embeddings capture semantic similarity of the natural language query, not structural similarity of the IFC operation.
Two queries about different properties on the same entity type produce very similar embeddings but completely
different intents. Without the pre-filter, semantically close but structurally wrong examples would be injected
with high confidence — worse than no injection.

**Why JSONField and not ArrayField for entity_types?**
Django's `ArrayField` is PostgreSQL-specific and not used elsewhere in the codebase. The entity type pre-filter
runs in Python (not SQL), so `JSONField` is functionally identical and avoids introducing a new field type.

**Why binary approval gate and not a gradient score?**
At D2 scale, there is not enough data for fine-grained score distinctions to matter. A gradient would inflate
high-volume trivial operations (SET_PROPERTY on common entity types) and suppress rare but important patterns
(ESCALATION, ADVERSARIAL). Binary filter plus recency ordering keeps recent examples prominent without bias.

**Why K=3 hardcoded?**
Diminishing returns on few-shot hit fast. On 8K-context models, K=3 is the token-safe ceiling. Evidence for
dynamic K computation requires empirical data that does not yet exist. Hardcode now, revisit if the eval shows
K=1 or K=2 consistently outperforms K=3.
