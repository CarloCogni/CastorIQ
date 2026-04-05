# MetaCastor D2 — Skill Bank + Few-Shot Injection

> Status: **Complete** — measured results recorded below. Depends on D1 (eval baseline frozen before implementation).

## What D2 Is

D2 gives the intent classifier memory. Every approved and committed modification is harvested as a
SkillExample and stored with a 1024-d embedding. On the next classification call, the three most
semantically similar past interactions are injected into the classifier's system prompt as few-shot
examples. The model is anchored to real patterns from the same project, not invented ones.

---

## Two-Stage Retrieval

Before cosine similarity, candidates are filtered to examples whose entity types overlap with the
types extracted from the current query. This entity-type pre-filter prevents cross-domain poisoning:
without it, a query about thermal resistance on walls would pull in fire-rating examples because the
embeddings are semantically close even though the intents diverge.

The extractor is a lightweight keyword scan and IfcXxx regex. It falls back to an empty list (no
filter) when nothing is extracted, which is safe. Within the filtered pool, cosine similarity ranks
candidates and the top three are returned.

---

## Retrieval Gate and K

Only examples where both `was_approved=True` and `commit_success=True` are retrievable. This is a
binary gate, not a gradient score. At D2 scale there is not enough data for gradient distinctions to
matter, and a gradient would inflate high-volume trivial operations over rare but important patterns.

K=3 is hardcoded. On llama3.1:8b (8K context), three injected examples consume roughly 600–900 tokens
against a realistic budget of around 3,000 after system instructions, entity context, and response
reserve. Dynamic K computation adds complexity without evidence of benefit.

---

## Token Budget Interaction

Skill injection reduces the budget available for entity context. The budget is computed after
injection is known, so entity context is trimmed to fit. This is intentional: on AMBIGUOUS cases
(the primary dissertation target) few-shot examples are more valuable than extra entity detail.

---

## Cold Start and Organic Harvest

New projects start with zero organic examples. The `seed_skill_bank` management command loads
`dev_cases.jsonl` (35 curated development cases) as synthetic examples with `is_organic=False`.
Seeded examples are global (project=None) and the command is idempotent.

After every `execute()` call that succeeds, the harvester creates a new SkillExample automatically.
The harvester is fully non-blocking — it is wrapped in try/except and never raises. A harvest
failure never blocks a commit.

---

## Measured Results (2026-03-30, llama3.1:8b, T=0, synthetic cold-start bank)

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

AMBIGUOUS tier +17pp and parameters +27pp (from floor zero) directly validate the MetaCastor thesis.
ADVERSARIAL improvement (+40pp) was unexpected — injected examples appear to teach the model to
correctly decline or reframe edge-case requests. Negative transfer on TRIVIAL/STANDARD operation
(-4–5pp) is a classic few-shot effect: easy cases receive examples anchored to complex patterns.
ESCALATION remains low because the synthetic seed bank has no Tier 2/3 examples.

---

## Key Files

- `metacastor/models.py` — SkillExample model with `was_approved`, `commit_success`, `is_organic` flags
- `metacastor/services/entity_type_extractor.py` — keyword + regex scanner for entity type hints
- `metacastor/services/skill_retriever.py` — two-stage retrieval: entity pre-filter + cosine similarity
- `metacastor/services/skill_harvester.py` — post-commit hook that creates SkillExample records
- `metacastor/management/commands/seed_skill_bank.py` — cold-start seeding from dev_cases.jsonl
- `writeback/services/intent_classifier.py` — `_format_skill_injection()`, `skill_examples` param on `classify()`
- `writeback/services/modification_service.py` — retrieval before budget sizing, harvester hook in `execute()`

Admin at `/admin/metacastor/skillexample/` lets you browse and filter the skill bank by `is_organic`,
`outcome_tier`, and `was_approved`.
