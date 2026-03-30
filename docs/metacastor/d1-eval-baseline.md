# D1 — Evaluation Baseline

> Status: **Complete** — committed and tagged `eval-baseline-v1`
> Model: `llama3.1:8b` (pinned — all future runs must use the same model to be comparable)

---

## What Was Built

| File | Purpose |
|---|---|
| `src/metacastor/eval/eval_cases.jsonl` | 115 frozen test cases — never edited after initial commit |
| `src/metacastor/eval/dev_cases.jsonl` | 35 dev/tuning cases — free to edit during development |
| `src/metacastor/eval/run_eval.py` | Measurement script — calls the real classifier, prints results |
| `src/metacastor/eval/generate_context.py` | One-time helper — reads an IFC fixture and prints the entity_context string |
| `src/metacastor/eval/fixtures/architecture.ifc` | Real architecture model (walls, slabs, roof) |
| `src/metacastor/eval/fixtures/structural.ifc` | Real structural model (beams, footings, walls) |
| `src/metacastor/eval/fixtures/plumbing.ifc` | Real plumbing/sewer model (pipe segments, manholes) |

---

## The Baseline Numbers (llama3.1:8b, T=0, static prompt, no MetaCastor)

```
Tier              operation         tier  filter_type   parameters    n
-----------------------------------------------------------------------
TRIVIAL                 95%         100%          95%          85%   20
STANDARD                88%          84%          76%          76%   25
AMBIGUOUS               83%          40%          17%           0%   30
ESCALATION              15%          15%          75%          20%   20
ADVERSARIAL             10%          80%          55%          35%   20
-----------------------------------------------------------------------
OVERALL                 62%          63%          60%          41%  115
```

These numbers are the D1 frozen baseline. The dissertation comparison is: these numbers vs the same run after D2 (skill bank active).
**Do not re-run and report new numbers as the baseline** — the baseline is this table, recorded here.

---

## How to Re-Run

```bash
cd src
uv run python metacastor/eval/run_eval.py
```

Requires Ollama running with `llama3.1:8b` pulled. The model is pinned inside `run_eval.py` via
`EVAL_MODEL = "llama3.1:8b"` — it overrides whatever is in `.env` or user settings.

---

## Case Distribution

### By difficulty tier

| Tier | Count | 95% CI (±pp) | What it tests |
|---|---|---|---|
| TRIVIAL | 20 | ±22 | Simple SET_PROPERTY on a single, obvious entity type |
| STANDARD | 25 | ±20 | Typical tier-1 operations with a clear filter and property |
| AMBIGUOUS | 30 | ±18 | Underspecified requests — model should escalate, not guess |
| ESCALATION | 20 | ±22 | Genuine tier-2 ops: ADD_PSET, SET_MATERIAL, SET_CLASSIFICATION, compound plans |
| ADVERSARIAL | 20 | ±22 | Tier-3 ops (CREATE, DELETE, SPATIAL, RELATIONSHIPS) the model tends to underestimate |

CI computed at p=0.5 (worst case), 95% confidence: ±1.96√(p(1-p)/n).

### By fixture

| Fixture | Cases | Entity types present |
|---|---|---|
| `architecture.ifc` | ~55 | IfcWall (4), IfcSlab (3), IfcRoof (1), IfcChimney (1), IfcFurniture (1) |
| `structural.ifc` | ~40 | IfcBeam (6), IfcWall (4), IfcSlab (1), IfcFooting (1), IfcDiscreteAccessory (2) |
| `plumbing.ifc` | ~20 | IfcPipeSegment (24), IfcElementAssembly (4) |

**Note on fixture gaps:** The architecture fixture has no `IfcDoor` or `IfcWindow`, and the structural fixture has no `IfcColumn`. 
Several cases ask about those entity types anyway — this is intentional. It tests whether the classifier correctly 
extracts the intent from the query when the entity type is not visible in the entity_context. 
In the real system, users frequently ask about entity types with no existing instances.

---

## Reading the Results

### TRIVIAL — strong (95% operation, 100% tier)

The model almost always picks the right operation and tier on clear, single-property requests. The 10pp gap in
`parameters` is pset naming uncertainty — it knows *what to do* but occasionally selects the wrong pset name.
Static prompting is sufficient here; D2 improvement will be modest.

### STANDARD — solid but degrades on filters (88% operation, 84% tier, 76% parameters)

Clear requests with property filters or chained operations start showing cracks. The model handles simple
SET_PROPERTY reliably but loses accuracy when the filter requires a `property_match` or the ground truth is a list
(chained ops). This is the primary area where few-shot examples of filtered operations will help.

### AMBIGUOUS — overconfident (83% operation, 40% tier)

The most instructive failure. 83% operation accuracy is misleading — the model picks SET_PROPERTY (matching the label)
but at the *wrong tier*: it invents specific values and executes rather than escalating. Only 40% of vague queries
are correctly routed to tier 2. 0% parameters accuracy confirms it is guessing properties that were never specified.

**This is the primary target for D2.** The dissertation claim lives here: can few-shot injection teach the model
to recognise underspecification and escalate instead of hallucinating?

### ESCALATION — collapsed (15% operation, 15% tier)

Tier-2 operations (ADD_PSET, SET_MATERIAL, SET_CLASSIFICATION) are almost entirely misclassified — the model
defaults to SET_PROPERTY regardless. The 75% `filter_type` score shows it at least extracts the entity type
correctly, but it doesn't recognise these as structurally different from property edits. Few-shot examples
of each T2 operation type are the direct fix.

### ADVERSARIAL — tier correct, operation wrong (10% operation, 80% tier)

Surprisingly, the model routes most tier-3 requests to the right tier (80%) but fails to name the correct
operation (DELETE, CREATE, SPATIAL, RELATIONSHIPS → only 10% match). It appears to recognise "this is dangerous /
structural" without understanding the specific operation type. D2 should bring operation accuracy up by providing
labelled examples of each T3 operation.

---

## Why the entity_context Strings Are Pre-Baked

Each eval case contains a complete `entity_context` string — the same formatted summary of IFC entities that the 
classifier receives in production. This string was generated once by running `generate_context.py` on each fixture, 
then frozen into the JSONL.

**The alternative** — parsing the IFC fixture at eval time — was rejected because:
1. It requires a running database with the fixture imported as a project
2. It adds ~30 lines to `run_eval.py` and a DB write dependency
3. If the IFC parsing logic changes, eval results would change for reasons unrelated to classifier quality

Frozen entity_context strings mean the eval is fully self-contained. The only dependency is a running Ollama instance.

---

## The Immutability Rule

`eval_cases.jsonl` is append-only after the initial commit. The rules:

1. **Never edit an existing case.** If a ground truth is genuinely wrong, document the error in a changelog note — do not silently fix it, because the baseline numbers were produced with the wrong ground truth and the correction must be recorded.
2. **Never add cases to the frozen set.** New cases go in `dev_cases.jsonl`.
3. **The final dissertation numbers come from one run of `run_eval.py` at the end**, using the frozen eval cases and the frozen model (`llama3.1:8b`). Not an average of multiple runs.
4. All iterative prompt tuning and few-shot experimentation uses `dev_cases.jsonl` only.

---

## How entity_context Was Generated

For each fixture, this command was run once from `src/`:

```bash
uv run python metacastor/eval/generate_context.py metacastor/eval/fixtures/<fixture>.ifc
```

The script reads the IFC file directly with IfcOpenShell (no database needed), creates duck-typed objects that match 
the `IFCEntity` ORM interface, and calls `IntentClassifier.build_entity_context()`. The output was pasted into the
JSONL cases for that fixture.

To regenerate (only needed if a fixture file is replaced — which invalidates the baseline):

```bash
uv run python metacastor/eval/generate_context.py metacastor/eval/fixtures/architecture.ifc
```
