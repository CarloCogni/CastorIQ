> **Superseded 2026-09-16.** The memory's §5 is now built from
> [`report-sections/05-results.md`](report-sections/05-results.md), which adds
> the evidence map (Table 5.1) and the independent testing of §5.4. This draft
> is kept as history; do not paste from it.

# Report §5 — Evaluation and validation (replacement text)

Drafted 2026-09-15 to replace §5.2 and §5.3 of the final memory. Every number
below is copied from a dated record under `docs/evaluation/` and can be
checked against the committed run artifact in `runs/` (index: `runs/README.md`).
Pointer for the memory:

> Evaluation records and raw run artifacts:
> `https://github.com/CarloCogni/CastorIQ/tree/fmp-final/docs/evaluation` and
> `…/tree/fmp-final/runs` (tag `fmp-final`, the commit this memory describes).
> Each harness is reproducible from the committed corpora with the commands in
> `docs/benchmarks.md`.

What this section replaces, and why: the earlier draft's Table 1 reported
tier-level correctness, schema adherence and approval latency by tier for a
qwen3:14b reference model over ~110 prompts and a 55-item RAV set. Those
figures do not trace to any record or artifact in the repository, and the
tiered pipeline they describe was replaced on 2026-09-15 by the V3
code-and-verify pipeline. The section now reports only what the repository
can reproduce.

---

## 5.2 Quantitative evaluation

Castor is measured by four independent harnesses (`docs/benchmarks.md`). Each
table below names its harness, corpus, model, machine, date and artifact; a
number without those five things is not reported.

### 5.2.1 Modify — natural-language write-back (Harness B)

**Harness:** `manage.py benchmark_writeback` · **Corpus:** 98 prompts in 20
sections, 95 scored (3 advisory), expectations GlobalId-free and resolved
through the index at run time · **Fixture:** `Ifc4_SampleHouse.ifc` (47,369
entities) · **Machine:** RTX 4070 Laptop, 8 GB graphics memory, Ollama, no CPU
offload · **Date:** 2026-09-15 · **Repeat policy:** `--repeat 2`, the worse of
two runs is kept, so non-determinism counts against the model · **Record:**
`docs/evaluation/2026-09-15-writeback-v3-bakeoff.md` · **Artifacts:**
`runs/writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json`,
`runs/writeback-v3-2026-09-15-claude-sonnet-4-6.json`.

| | Local floor, `qwen2.5-coder:7b` (8 GB) | Frontier ceiling, `claude-sonnet-4-6` (cloud) |
|---|---|---|
| passed (scored) | **41 / 95** | **65 / 95** |
| targets match (`select()` returned exactly the named entities) | 19 / 64 | 38 / 64 |
| diff match (the measured diff holds the expected rows and counts) | 18 / 66 | 39 / 66 |
| integrity (nothing outside the selection changed, no geometry moved; independent re-read of the written copy) | **46 / 46** | not measured on this row (see record) |
| reject / no-change handled correctly | 23 / 29 | 26 / 29 |
| repairs used (≤ 2 per request) | 59 | 58 |
| harness errors | 0 | 0 |
| latency median / p90 per request | 6.4 s / 14.0 s | 8.1 s / 21.5 s |
| tokens in / out (cost) | 509,066 / 26,081 (local) | 896,951 / 72,510 ($3.78) |

Table 5.1. Writeback V3 bake-off, 2026-09-15. Denominators: 64 selection
cases, 66 change cases, 29 reject/no-change cases; a refused change case scores
false on targets and diff (it stays in the denominator). The Claude row's
targets and diff columns were re-scored from the stored artifact after a
harness correction the same day (the artifact carries the earlier 38/46 and
39/47); the record documents the correction.

How the rows fail is more informative than the pass rate. Of the 7B row's 54
failures, 19 were proposals on the wrong entities (a subtype confused with its
parent, a name fragment missed), 15 were rejections after three code errors
(a hallucinated helper, a wrong API keyword), 10 were rejections after three
empty selections. None was a change outside the selection: every wrong attempt
ended in a repair or a rejection, which is the property the design is built
to guarantee. Two-thirds of the ceiling row's 30 failures are corpus
conventions (a Revit export suffix read as a STEP id; whether a curtain wall is
a wall) rather than model errors; with those settled the ceiling would sit
near 80 / 95. The 14B non-coder control could not be run: offloaded to system
memory on this machine it took 135 s per model call, which is the spec's own
argument for "no CPU offload by design". The 14B and 30B coder rows are
pending a 12 GB and a 24 GB machine.

Two further measurements from the same record: `ifcopenshell.open()` 0.12 s
and one snapshot 0.49 s on the sample house (two snapshots per attempt cost
about one second, so no snapshot cache is needed); and the blind explanation
(the model describes the measured diff without seeing the request) stated the
property, value and count, and nothing the diff did not show, on 10 of 10
randomly sampled passing cases (seed 20260915).

**The V2 "before".** The tiered pipeline was measured once
(`docs/evaluation/2026-08-05-writeback-nl-benchmark.md`, `llama3.1:8b`, 92
prompts): 64 / 91 on "understanding" (the router picked the corpus's tier) and
53 / 53 on journal fidelity. Those numbers are not comparable to Table 5.1:
V2 scored whether the right tier was chosen, V3 scores what the executed code
did to the file. The design change is argued in `docs/writeback_V3/what-changed.md`,
not by the two pass rates.

### 5.2.2 Retrieval-augmented verification — the conflict scanner (Harness D)

**Harness:** `manage.py benchmark_rav` · **Corpus:** three consultant-style
documents planted over the sample house (`fixtures/benchmark/rav/`), 26
labelled cases (12 planted conflicts of three severities — clear, marginal,
missing-property — and 14 aligned requirements) over 15 entities, scored per
entity: 25 conflict triples and 26 negatives · **Models:** `llama3.1:8b`
(continuity with the first run) and `qwen2.5-coder:7b` (the site's Modify
model, which the scanner uses) · **Machine:** as above · **Date:** 2026-09-15
· **Repeat policy:** `--repeat 3`; mean and min–max reported; an improvement
is claimed only when the delta exceeds the baseline's own min–max spread ·
**Records:** `docs/evaluation/2026-08-30-rav-benchmark.md` (first run, diagnosis),
`docs/evaluation/2026-09-15-rav-retrieval-fix.md` (before/after) ·
**Artifacts:** `runs/rav-2026-09-15-{before,after}-{llama3.1-8b,qwen2.5-coder-7b}-repeat3.json`,
`runs/rav-2026-09-15-ablate-qwen2.5-coder-7b.json`.

The first measured run (2026-08-30) put a number on what pilot testing had
only described: precision 0.29, recall 0.20, F1 0.24, with recall on clear
conflicts at 1 / 11. Its diagnosis was that the bottleneck was retrieval, not
the model's comparison: with five entities per requirement chunk chosen by
embedding distance, three identical external walls crowded each other out and
no thermal-specification chunk reached any wall or window, so two entire
conflict cases were never presented to the model. The ablation of the four
existing false-positive mitigations moved results by one or two findings,
inside single-run variance. The response was therefore not prompt work but a
retrieval change: two exact lookup passes before the embedding pass (the
chunk quotes the entity's model reference; the chunk names the element class
and a property), verification of the current value a finding claims against
the indexed properties before it is stored, and attribution of a finding to
the chunk that quotes the requirement (`docs/conflict-scan.md`).

| | before (mean [min–max], n=3) | after (mean [min–max], n=3) | delta vs. baseline spread |
|---|---|---|---|
| `llama3.1:8b` precision | 0.29 [0.26–0.31] | **0.71 [0.68–0.73]** | +0.42, spread 0.05 |
| `llama3.1:8b` recall | 0.20 [0.20–0.20] | **0.77 [0.76–0.80]** | +0.57, spread 0.00 |
| `llama3.1:8b` F1 | 0.24 [0.23–0.24] | **0.74 [0.72–0.75]** | +0.50, spread 0.02 |
| `qwen2.5-coder:7b` precision | 0.60 [0.57–0.67] | **0.70 [0.67–0.74]** | +0.10, equal to the spread (0.10): no fall |
| `qwen2.5-coder:7b` recall | 0.16 [0.16–0.16] | **0.65 [0.64–0.68]** | +0.49, spread 0.00 |
| `qwen2.5-coder:7b` F1 | 0.25 [0.25–0.26] | **0.68 [0.65–0.71]** | +0.42, spread 0.01 |
| recall on clear conflicts (worst of three runs) | 1 / 11 on both models | 9 / 11 (llama) · 6 / 11 (coder) | |
| aligned requirements left alone (negatives held, worst of three) | 21 / 26 (llama) · 25 / 26 (coder) | 18 / 26 (llama) · 21 / 26 (coder) | the cost of the fix |
| key entities reached by the right document (no model involved) | 11 / 15 | **15 / 15** | deterministic |

Table 5.2. Conflict scanner before and after the retrieval fix, same corpus,
same settings, same day, three runs each.

The movement is where the diagnosis said it would be: clear conflicts, the
class retrieval had hidden, went from 1 of 11 to 9–10 of 11 on llama and 6–8
of 11 on the coder model; missing-property conflicts from 3–4 of 13 to 9–10 of
13. The one marginal case (0.117 against a limit of 0.10) is found by neither
model on any run. Value verification fired on every run: on the coder model 6
claimed current values per run were replaced by the indexed value and 3
claims on properties the entity does not carry were stored as "(not set)"
instead of the invented value (llama: 5 and 10 per run). The cost is on aligned
requirements: the model now sees every entity and in a few cases applies a
neighbouring limit to an "as designed" value in the same excerpt; negatives
held fell by two to four of 26. What still fails is the model's reading, not
retrieval: an absent property the requirement targets is read as "not
applicable" (acoustic rating on doors, fire rating on a slab), and two
documents that disagree about the same walls are not both reported on the
coder model. The record names every persistently failing case with its
mechanism. The seven-arm ablation on the after-scanner attributes the gain:
with the retrieval passes off, recall falls from 0.72 to 0.28 and
clear-conflict recall from 8 of 11 back to 1 of 11 (the baseline's numbers);
with value verification off, recall falls to 0.56 and precision to 0.61,
because a model that copies the document's value into the "current value"
field had its real conflicts dropped as already matching; the three earlier
mitigations (type gate, keyword filter, confidence cut) each move one to
three findings, inside the three-run spread, as the first record found; and
with everything off the after-scanner reproduces the baseline (0.58 / 0.28).

The retrieval-coverage line is the one that does not depend on a model: it is
recomputed from the entity–chunk map alone. Before the fix, 4 of the 15 key
entities were never retrieved and several were reached only from a document
that had no requirement for them; after it, every key entity is reached from
every document that constrains it.

### 5.2.3 Ask — question answering over real models (Harness C)

**Harness:** `manage.py benchmark_ask` · **Corpus:** 10 fixture-agnostic
questions (6 Tier 1 with one right answer: counts of doors, windows, walls and
spaces, storey names, schema; 4 Tier 2 narrative: materials, spaces, fire
rating, external walls) run against four public IFC models
(`AC20-FZK-Haus`, `IfcOpenHouse_IFC4`, `Office_A`, `duplex`); every expected
value is computed independently with IfcOpenShell from the fixture at run
time · **Model:** `llama3.1:8b`, the site's Ask model · **Date:** 2026-09-15 · **Record:**
`docs/evaluation/2026-09-15-ask-benchmark.md` · **Artifacts:** `runs/ask-2026-09-15.*.json`.

| Fixture (schema) | Tier 1 passed / scored | Tier 2 passed / scored | skipped | median latency |
|---|---|---|---|---|
| open-house (IFC4, 23 entities) | 4/4 | 2/3 | 3 | 3.3 s |
| duplex (IFC2X3, 241 entities) | 5/6 | 4/4 | 0 | 2.7 s |
| fzk-haus (IFC4, 51 entities) | 6/6 | 2/3 | 1 | 2.2 s |
| office-a (IFC2X3, 901 entities) | 5/6 | 3/4 | 0 | 2.0 s |
| **total** | **20 / 22** | **11 / 14** | 4 | |

Table 5.3. Ask benchmark, first published run. Scoring is answer-level (does
the exact count, a storey name, the schema string or an expected keyword
appear; was the question refused). Counts of doors, windows and walls, storey
lists and the schema were right on every model; the five failures are two
space counts on the IFC2X3 models answered from excerpts instead of the count
recipe, two narrative answers that explained a method instead of answering,
and one correct "no fire rating in this model" the keyword scorer counts as a
refusal. The scorer also passes one useless keyword-matching answer; the
record keeps both verdicts as scored. It does not measure retrieval directly:
hit@k and MRR over the retrieved entities and chunks are future work, and
N = 36 scored answers is a first measurement, not a characterisation.

### 5.2.4 What is not measured

No Castor-side OCR accuracy has been measured; the GLM-OCR layer is described
with its vendor benchmark only (`docs/ocr-pipeline.md`). Approval latency by
tier, schema adherence and the ~110-prompt corpus of the earlier draft do not
exist and are withdrawn. The 12 GB and 24 GB Modify rows, an adversarial
sandbox corpus with a measured block rate, and a structured expert labelling
of outputs (Cohen's κ against the harness verdicts;
`docs/fmp-delivery/expert-rerun-protocol.md`) are open items, listed in
`docs/fmp-delivery/rubric-map.md` §5.

### 5.2.5 Threats to validity

- **One model per row, one machine.** Every row is a single model on one
  laptop; the ceiling row is a single cloud model. Rows are not averaged over
  models, and no row claims to generalise beyond its model.
- **Self-labelled corpora.** The Modify corpus and the planted-conflict
  corpus were written and labelled by the authors. The corpus edges that
  cost the ceiling row most (the Revit `:285330` suffix, curtain walls) are
  documented in the record rather than fixed after the fact; expert
  labelling is the open item above.
- **Small N, denominators shown.** 95 scored prompts, 26 conflict cases over
  15 entities, 40 Ask answers. Every rate is given with its denominator; no
  rate is reported to more precision than its N supports.
- **Run-to-run variance.** Local models are non-deterministic at temperature
  0.1. The Modify rows keep the worse of two runs; the RAV rows report the
  min–max of three, and a change is called an improvement only when it
  exceeds the baseline's spread. The single-run 2026-08-30 RAV figures are
  therefore reproduced (three repeats, same result) before being used as a
  baseline.
- **Corrected denominators.** The V3 record's targets and diff columns were
  re-scored after a harness defect was found the same day; the correction and
  the uncorrected artifact are both kept. Readers can recompute either.
- **Non-comparability of V2 and V3.** Stated in 5.2.1; the pipelines measure
  different things and the report does not rank them by pass rate.

---

## 5.3 Test suite and quality assurance

The automated suite collects **2,668 tests** from `src/` plus 19 Playwright
end-to-end tests (`pytest --collect-only`, 2026-09-15, at the commit tagged
`fmp-final`), with `ruff check` and `ruff format` as a gate before any
merge. Coverage is broad rather than concentrated:

| Area | Tests | Notes |
|---|---|---|
| Facilities (7D) | 473 | assets, work, permits, requests, maintenance |
| Quantities / 5D takeoff | 412 | |
| Write-back (Modify V3, Guardian, conflict scanner, RAV harness, Git, consumers) | 411 | 392 after the V3 rewrite on 2026-09-15 deleted 19 V2 test files, plus 19 added with the retrieval fix the same day |
| Core platform (BYOK, budgets, LLM routing, staff) | 338 | |
| IFC processing (parser, diff, sandbox, round-trip) | 295 | |
| Scheduling (4D) | 263 | |
| Environments and deployment | 162 | |
| Ask / RAG and the Ask benchmark scorer | 109 | |
| 5D cost | 66 | |
| Documents and OCR | 37 | |
| Classification | 35 | |
| IFC viewer | 20 | |
| Embeddings | 16 | |
| Users, beta funnel, MetaCastor, model quality | 31 | |
| Playwright end-to-end | 19 | |

Table 5.4. Collected tests per application, `pytest --collect-only`.

Every LLM, embedding, Git and file-system boundary is mocked in the unit
layers; the benchmark harnesses above are the only tests that call a model.
The suite is evidence of behaviour under specified inputs, not of end-to-end
efficacy: that is what the harnesses and the open expert-validation item are
for. Formal load and regression testing has not been performed.

The write-back suite's drop from 649 (2026-09-02) to 392 is the V3 rewrite
removing the tier router, triage, slot extraction, the tier validators and the
journal executor with their tests; the entity-resolution defect fixed in PR #16
on 2026-09-02 (four fixes to the V2 resolver, verified live) is recorded as
history: the files it fixed no longer ship. The 2026-09-15 retrieval fix
(§5.2.2) added 19 tests: retrieval passes, value verification, attribution,
and the repeat aggregation of the harness.
