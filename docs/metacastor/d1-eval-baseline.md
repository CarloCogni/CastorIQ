# MetaCastor D1 — Evaluation Baseline

> Status: **Complete** — frozen, model pinned to `llama3.1:8b` at temperature 0.

## What D1 Is

A reproducible measurement of the raw intent classifier before any MetaCastor improvements.
115 frozen test cases across five difficulty tiers. The baseline numbers below are the reference
point for every D2/D3 comparison. They are recorded here — not to be re-run and replaced.

---

## Frozen Baseline Numbers

```
Tier              operation    tier    filter_type    parameters    n
--------------------------------------------------------------------
TRIVIAL                 95%    100%           95%           85%   20
STANDARD                88%     84%           76%           76%   25
AMBIGUOUS               83%     40%           17%            0%   30
ESCALATION              15%     15%           75%           20%   20
ADVERSARIAL             10%     80%           55%           35%   20
--------------------------------------------------------------------
OVERALL                 62%     63%           60%           41%  115
```

AMBIGUOUS is the primary dissertation target: 40% tier accuracy and 0% parameters means the model
guesses specific values on underspecified requests instead of escalating. D2 tests whether
few-shot injection fixes this.

---

## Case Distribution

Five difficulty tiers cover different failure modes. TRIVIAL and STANDARD test happy-path
classification. AMBIGUOUS tests whether the model escalates when a request is underspecified.
ESCALATION tests Tier 2 operations (ADD_PSET, SET_MATERIAL, SET_CLASSIFICATION). ADVERSARIAL
tests Tier 3 ops (CREATE, DELETE, SPATIAL) that the model tends to underestimate.

Three IFC fixtures provide entity context: an architecture model (walls, slabs, roof),
a structural model (beams, footings), and a plumbing model (pipe segments). Some cases
intentionally reference entity types absent from the fixture — this tests intent extraction
independent of visible entity context.

---

## Key Files

- `metacastor/eval/eval_cases.jsonl` — 115 frozen cases, never edited
- `metacastor/eval/dev_cases.jsonl` — 35 dev/tuning cases, free to modify
- `metacastor/eval/run_eval.py` — measurement script, model pinned to `llama3.1:8b`
- `metacastor/eval/fixtures/` — three real IFC fixtures (architecture, structural, plumbing)

---

## The Immutability Rule

`eval_cases.jsonl` is append-only after initial commit. Never edit existing cases — if a ground
truth is wrong, record the error in a note but do not silently fix it, because the baseline was
produced with the flawed case. All iterative tuning uses `dev_cases.jsonl` only.

---

## Why entity_context Strings Are Pre-Baked

Each case stores a complete entity_context string generated once from the fixture, not parsed at
eval time. This keeps the eval fully self-contained (Ollama is the only dependency) and prevents
IFC parsing changes from silently affecting classifier measurements.
