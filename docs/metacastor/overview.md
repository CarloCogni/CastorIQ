# MetaCastor — Concepts & Architecture

> Plain-English reference for the MetaCastor self-improvement layer.
> For the full technical spec, see `docs/specs/meta-castor-design-brief-V02.md`.

---

## What MetaCastor Is

Castor answers questions about IFC models and proposes modifications. MetaCastor watches Castor do its job and tries to
make it better over time — automatically, without requiring the user to do extra work.

It does this by keeping a memory of past successful interactions and feeding relevant examples back into the classifier
the next time a similar request arrives. The core idea is: *the more the system is used, the better it gets at 
understanding what users actually want.*

MetaCastor does not touch IFC geometry. It does not change the approval flow. It does not replace the RSAA safety tiers. 
It only influences what the intent classifier sees before it makes a decision.

---

## Why It's a Separate App

MetaCastor lives in `src/metacastor/`, not inside `writeback/`. The reason: `writeback` reasons about IFC data — 
what properties to change, what entities to target, whether a commit succeeded. 
MetaCastor reasons about *Castor's own performance* — which past interactions were successful, which failure patterns repeat, 
whether the classifier is getting better. These are different concerns and mixing them would pollute both.

Import direction is one-way: `metacastor` imports from `writeback` and `environments`. 
Nothing in the existing apps imports from `metacastor` except a single injection point in the intent classifier.

---

## The Three Deliverables (Build Order)

### D1 — Evaluation Baseline (built first, before any other MetaCastor code)

A frozen set of test cases and a measurement script. This exists so that when MetaCastor features are added, 
there is an objective, pre-existing benchmark to compare against.

**Why it must come first:** If the test cases were written after the implementation, the author would unconsciously write
cases that the implementation already handles well. The benchmark would be compromised. By writing the cases first — 
before knowing what the implementation will look like — the comparison is honest.

See: [D1 — Evaluation Baseline](./d1-eval-baseline.md)

### D2 — Skill Bank + Few-Shot Injection

A database of past successful interactions (`SkillExample` model) and a retrieval service that finds the 3 most relevant
examples for a new incoming query. Those examples are injected into the classifier's context window as demonstrations of good output.

See: [D2 — Skill Bank](./d2-skill-bank.md) *(added when D2 is built)*

### D3 onwards — Failure Diagnosis, Fine-Tuning

See the individual deliverable docs for status.

### D5 — Certified Tier 3 Reuse

A field-level extension of the skill bank: when a Tier 3 code-generation operation is approved and committed, the generated IfcOpenShell code is stored alongside the `SkillExample`. On future Tier 3 requests, the retriever surfaces qualifying past successes as few-shot **reference patterns** in the code-generation prompt. No separate model, no parameterisation — one new field, one conditional branch.

See: [D5 — Certified Tier 3 Reuse](./d5-tier3-injection.md) *(implemented and measured 2026-04-16)*

---

## D1 In Detail — What the Eval Measures and What It Doesn't

### What it measures

The eval calls `IntentClassifier.classify()` for each test case and compares the output against a hand-written ground
truth on four axes:

| Metric | What it checks |
|---|---|
| **operation** | Did the model pick the right operation type? (SET_PROPERTY, SET_ATTRIBUTE, etc.) |
| **tier** | Did the model assign the right RSAA tier? (1=execute, 2=plan, 3=code) |
| **filter_type** | Did the model target the right IFC entity type? (IfcWall, IfcBeam, etc.) |
| **parameters** | Did the model identify the right property set and property name? |

Results are broken down by **difficulty tier** — not by fixture or discipline. The difficulty tiers are:

| Difficulty | What it tests |
|---|---|
| TRIVIAL | Simple, obvious requests. Sanity check only. |
| STANDARD | Typical day-to-day requests with a clear filter and property. |
| AMBIGUOUS | Underspecified requests. The correct answer is **tier 0** (reject) whenever WHAT/WHICH/VALUE is missing; the frozen ground truth says `tier: 2` because Tier 0 didn't exist when the cases were written — the eval scorer treats tier 0 as a correct answer for AMBIGUOUS cases. |
| ESCALATION | Requests that require a multi-step plan or human code review (tier 2/3). |
| ADVERSARIAL | Requests that look simple but must NOT be flattened to tier 1 (entity creation, deletion). |

### What it does NOT measure

The eval does **not** run the full RSAA pipeline. It does not:
- Create a `ModificationProposal`
- Apply the modification to the IFC file
- Make a Git commit
- Check whether the commit succeeded

It only tests the *judgment call at the classification step*: did the model understand what kind of operation was being 
requested, and did it pick the right confidence level?

Whether the modification would have succeeded is a separate question — captured in production by `commit_success` on the
`WritbackProposal` model, and used by the Skill Harvester in D2.

### The ground_truth_post_state field

Each eval case has a `ground_truth_post_state` field, currently null for most cases. When filled, it describes the state
the IFC file should be in after a successful end-to-end execution. These are the highest-value cases — they verify the
complete loop, not just the classification step. Filling them requires running the modification against the IFC fixture 
and checking the output file. This is planned for a later pass.

---

## The Overconfidence Problem (Why MetaCastor Is Needed)

This is the finding that motivates the whole project.

**The baseline result on AMBIGUOUS cases (llama3.1:8b, no MetaCastor):**

```
AMBIGUOUS    operation: 83%    tier: 40%    filter_type: 17%    parameters: 0%
```

When a user says *"make the building more fire-safe"*, the model invents a specific answer:

```json
{"tier": 1, "operation": "SET_PROPERTY", "filter": {"ifc_type": "IfcWall"},
 "pset": "Pset_WallCommon", "property": "FireRating", "new_value": "EI60"}
```

The user never said `EI60`. The user never said walls only. The model filled in values because it is trained to be helpful 
and complete. This produces a proposal that looks reasonable but makes assumptions the user didn't authorise.

The correct behaviour is either **tier 0** (reject and ask for the missing value/target) or tier 2 (escalate to a
multi-step plan when every value IS specified but can't fit in a single tier-1 call).

**Why the operation score is misleadingly high (83%):** Many of those "correct" operations are cases where the model
returned SET_PROPERTY at tier 1 — which matches the operation label in the ground truth, but at the wrong tier. 
The model is right about *what kind of change* to make, but wrong about *whether it's certain enough to commit to specific values*.

**How Castor fixes this (two layers):**

1. **Tier 0 rejection (classifier-level gate).** Rule 4 in the classifier system prompt tells the model to return
   `tier: 0` whenever it cannot determine WHAT to change, on WHICH entities, and to WHAT value. Tier 0 returns a
   user-facing explanation and short-circuits the pipeline before any plan generation or IFC write.
2. **MetaCastor skill injection (soft refinement).** The skill bank contains past examples of similar queries
   that were resolved successfully. When a new query arrives, those examples are injected into the classifier's
   context so the model learns *"when queries look like this, the right answer is <tier X>"* — which can pull
   legitimate multi-step requests out of a mistaken Tier 0, or anchor truly vague requests as Tier 0.

### The ADVERSARIAL result — a different problem

```
ADVERSARIAL    operation: 10%    tier: 80%
```

For requests like *"create a complete new floor with walls, slabs, and columns"*, the model gets the tier roughly
right four times in five but picks the wrong operation nine times in ten — it tends to pick a tier-1-shaped operation
label (SET_PROPERTY) instead of a creation op, even when it correctly flagged the request as tier 3.

This is a **reasoning deficit**, not a formatting deficit. Few-shot injection shows the model what good output looks 
like — but if the model doesn't recognise that creating new IFC entities is inherently tier 3, examples won't teach it that.
This is the result most likely to remain unimproved after D2, and the dissertation must address it honestly.

---

## The Self-Correction Loop (D2 Preview)

Once the skill bank is built, the system closes a feedback loop:

```
User request
    → Skill retriever finds 3 similar past interactions
        → Classifier sees those examples in its context
            → Produces intent
                → Proposal shown to user
                    → User approves or rejects
                        → If approved AND commit succeeds:
                            → SkillHarvester saves to skill bank
                        → If rejected:
                            → Nothing saved (bad guesses don't propagate)
```

The key filter is `was_approved=True AND commit_success=True`. A proposal that the user approved but that failed to
commit (e.g. IFC schema conflict) also does not enter the skill bank. Only clean, successful, human-verified interactions 
become training signal.

**On cold start:** When the system is first deployed for a new project, the skill bank is empty. The system falls back 
to static prompting (same as the D1 baseline). The `seed_skill_bank` management command pre-populates the bank with 
examples from `dev_cases.jsonl`, labeled `is_organic=False` so retrieval logs can distinguish synthetic from real experience.

---

## The Dissertation Claim

The comparison the dissertation makes:

> **"Dynamic few-shot injection from real usage outperforms static prompt engineering for structured output generation in domain-specific LLM agents."**

Measured as: delta on AMBIGUOUS tier accuracy between D1 baseline and D2 (skill bank active).

- D1 baseline: **40% tier accuracy on AMBIGUOUS**
- D2 target: meaningfully higher — the dissertation argues if it moves above ~60%, the contribution is real

If the delta is only on TRIVIAL cases (which are already at 100%), the contribution is not meaningful. The AMBIGUOUS 
tier is the only one that matters for the claim.

See [Post-Tier-0 Re-measurement](#post-tier-0-re-measurement-2026-04-16) below for the updated numbers under the
corrected Tier 0 validator and eval scoring.

---

## Post-Tier-0 Re-measurement (2026-04-16)

Tier 0 was added to the classifier prompt (rule 4) but the validator at `_validate_structure()` only accepted tiers
`(1, 2, 3)`, so every `tier: 0` response raised `IntentParseError` and was scored as all-false in the eval. This was
fixed on 2026-04-16 by accepting tier 0 in the validator (requiring only `explanation`) and updating the eval scorer
to treat `tier: 0` as a correct answer for AMBIGUOUS cases.

**D1 (static prompt) — baseline vs post-Tier-0-fix:**

| Tier | Metric | D1 baseline | D1 post-fix | Delta |
|---|---|---|---|---|
| AMBIGUOUS | tier | 40% | **67%** | +27pp |
| AMBIGUOUS | filter | 17% | **77%** | +60pp |
| AMBIGUOUS | parameters | 0% | **70%** | +70pp |
| OVERALL | tier | 63% | **71%** | +9pp |
| OVERALL | parameters | 41% | **66%** | +25pp |

**D2 (skill injection) — baseline vs post-Tier-0-fix:**

| Tier | Metric | D2 baseline | D2 post-fix | Delta |
|---|---|---|---|---|
| AMBIGUOUS | tier | 57% | **77%** | +20pp |
| AMBIGUOUS | filter | 33% | **80%** | +47pp |
| AMBIGUOUS | parameters | 27% | **77%** | +50pp |
| OVERALL | tier | 70% | **77%** | +6pp |

The AMBIGUOUS parameters metric jumping from 0% to 70% (D1) is the primary signal: the classifier now correctly
recognises that missing values should trigger rejection, not guessing.

**Known follow-up items:**
- AMBIGUOUS operation dropped on D2 (73% → 33%). Some AMBIGUOUS cases get pulled out of Tier 0 by skill injection
  and classified as T1/T2 with the wrong operation — possibly fabrication of values borrowed from injected examples.
- ESCALATION/ADVERSARIAL filter accuracy regressed slightly — Tier 0 may be over-firing on edge cases.

Both are flagged for audit, not blockers for the fix itself.

---

## Files

| Path | Purpose |
|---|---|
| `src/metacastor/` | Django app — models, services, management commands |
| `src/metacastor/eval/eval_cases.jsonl` | 115 frozen eval cases — never edited after initial commit |
| `src/metacastor/eval/dev_cases.jsonl` | 35 dev/tuning cases — free to edit |
| `src/metacastor/eval/run_eval.py` | Measurement script — pinned to llama3.1:8b |
| `src/metacastor/eval/generate_context.py` | One-time helper — reads IFC fixture → entity_context string |
| `src/metacastor/eval/fixtures/` | IFC files used by eval cases — committed to git, never replaced |
| `docs/specs/meta-castor-design-brief-V02.md` | Full technical spec — implementation decisions and rationale |
