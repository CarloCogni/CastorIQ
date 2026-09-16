# Results and evaluation

The evaluation is of three kinds: delivered scope; quantitative measurement by reproducible harnesses; and independent testing by team members outside the write path against external ground truth. Every number below is copied from a dated record in the repository and can be checked against its committed run artifact. Table 5.1 maps each result to its source at the tag `fmp-final`, the commit this memory describes ([evaluation records](https://github.com/CarloCogni/CastorIQ/tree/fmp-final/docs/evaluation), [run artifacts](https://github.com/CarloCogni/CastorIQ/tree/fmp-final/runs)); Appendix F maps each figure to the code that computes it, and `docs/evaluation/recount.py` recomputes Tables 5.2 to 5.4 from the run files with no model.

## 5.1 Delivered scope and mitigations

Each milestone of the third phase had an operational purpose: GLM-OCR restored retrieval over scanned archives; BYOK answered "my hardware is too weak"; the hosted beta let outside evaluators in without an installation; AGPL made data sovereignty enforceable. {cyan}The write-path rewrite closed three defects found by manual testing by construction, since the components no longer exist: a silent material corruption in a pre-coded handler, non-deterministic tier routing, and an entity resolver that returned a default set.{/cyan}

## 5.2 Quantitative evaluation

| Result | Harness and corpus | Models, machine, date, repeats | Record | Artifacts |
|---|---|---|---|---|
| Table 5.2, Modify | [`benchmark_writeback`](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/src/writeback/management/commands/benchmark_writeback.py): 98 prompts, 95 scored, on `Ifc4_SampleHouse.ifc` | qwen2.5-coder:7b, claude-sonnet-4-6; RTX 4070 Laptop, 8 GB, no offload; 2026-09-15; worse of 2 runs | [2026-09-15-writeback-v3-bakeoff.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-09-15-writeback-v3-bakeoff.md) | `writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json`, `writeback-v3-2026-09-15-claude-sonnet-4-6.json` |
| Table 5.3, RAV | [`benchmark_rav`](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/src/writeback/management/commands/benchmark_rav.py): 26 planted cases over 15 entities, three documents; `--coverage` for retrieval | llama3.1:8b, qwen2.5-coder:7b; same machine; 2026-09-15; 3 runs, min–max; coverage 2026-09-16, no model | [2026-08-30-rav-benchmark.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-08-30-rav-benchmark.md), [2026-09-15-rav-retrieval-fix.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-09-15-rav-retrieval-fix.md), [2026-09-16-rav-retrieval-coverage.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-09-16-rav-retrieval-coverage.md) | `rav-2026-09-15-{before,after}-*-repeat3.json`, `rav-2026-09-15-ablate-qwen2.5-coder-7b.json`, `rav-2026-09-16-coverage.json` |
| Table 5.4, Ask | [`benchmark_ask`](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/src/chat/management/commands/benchmark_ask.py): 10 questions × 4 public IFC models, answers computed by IfcOpenShell | llama3.1:8b; same machine; 2026-09-15; 1 run | [2026-09-15-ask-benchmark.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-09-15-ask-benchmark.md) | `ask-2026-09-15.*.json` |
| Tiered pipeline, history | `benchmark_writeback` (V2): 92 prompts | llama3.1:8b; 2026-08-05; 1 run | [2026-08-05-writeback-nl-benchmark.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-08-05-writeback-nl-benchmark.md) | none survives |
| Section 5.4, testing | manual, IfcOpenShell and Solibri as ground truth, 138 logged findings | qwen2.5-coder:14b, 6 GB, for the rewritten write path; 2026-05-18 to 2026-09-16 | [2026-09-16-expert-testing-v3.md](https://github.com/CarloCogni/CastorIQ/blob/fmp-final/docs/evaluation/2026-09-16-expert-testing-v3.md) | [`testing-log/erez.csv`, `maria.csv`](https://github.com/CarloCogni/CastorIQ/tree/fmp-final/docs/evaluation/testing-log) |
| Table 5.5, tests | `pytest --collect-only` over `src/` and `tests/e2e` | 2026-09-16 | this section | none |

Table 5.1. Where every number in this section comes from. Records are dated and never edited; artifacts carry every case's outcome and live under `runs/`.

### 5.2.1 Modify: natural-language write-back

| | Local floor, qwen2.5-coder:7b (8 GB) | Frontier ceiling, claude-sonnet-4-6 (cloud) |
|---|---|---|
| passed (scored) | 41 / 95 | 65 / 95 |
| targets match (select() returned exactly the named entities) | 19 / 64 | 38 / 64 |
| diff match (the measured diff holds the expected rows and counts) | 18 / 66 | 39 / 66 |
| integrity (nothing outside the selection changed, no geometry moved; independent re-read of the written copy) | 46 / 46 | not measured on this row |
| reject / no-change handled correctly | 23 / 29 | 26 / 29 |
| repairs used (at most 2 per request) | 59 | 58 |
| latency median / p90 per request | 6.4 s / 14.0 s | 8.1 s / 21.5 s |
| tokens in / out (cost) | 509,066 / 26,081 (local) | 896,951 / 72,510 ($3.78) |

Table 5.2. Write-back bake-off. A refused change case scores false on targets and diff. The ceiling row was re-scored from its artifact after a same-day harness correction; the record keeps both figures.

How the rows fail matters more than the pass rate. Of the 56 failures in the 7B run measured earlier the same day, before a prompt change that moved five edge cases, 19 were proposals on the wrong entities (a subtype confused with its parent, a name fragment missed), 15 rejections after three code errors (a hallucinated helper, a wrong API keyword) and 10 rejections after three empty selections. None was a change outside the selection: every wrong attempt ended in a repair or a rejection, which is the property the design guarantees. Two-thirds of the ceiling row's 30 failures are corpus conventions, such as a Revit export suffix read as a STEP id. The blind explanation stated property, value and count, and nothing the diff did not show, on 10 of 10 randomly sampled passing cases. The tiered pipeline was measured once (Table 5.1): 64 / 91 on whether the router chose the corpus's tier and 53 / 53 on journal fidelity. Those figures score the choice of tier, Table 5.2 scores what the executed code did to the file; they are not comparable.

### 5.2.2 Retrieval-augmented verification: the conflict scanner

The conflict scanner applies the RAV layer to the whole model. Its first measured run (2026-08-30) quantified the weakness pilot testing had reported: precision 0.29, recall 0.20, recall on clear conflicts 1 of 11. Its diagnosis was that retrieval, not the model's comparison, was the bottleneck: with entities chosen per requirement chunk by embedding distance, identical external walls crowded each other out and no thermal chunk reached any wall or window, so whole conflict cases never reached the model. Ablating the four prompt-level mitigations moved results only within the variation between runs. The fix was therefore in retrieval: two exact lookup passes before the embedding pass, verification of a finding's claimed current value against the index, and attribution of a finding to the chunk that quotes the requirement.

| | before (mean [min–max], n=3) | after (mean [min–max], n=3) | delta vs. baseline spread |
|---|---|---|---|
| llama3.1:8b precision | 0.29 [0.26–0.31] | 0.71 [0.68–0.73] | +0.42, spread 0.05 |
| llama3.1:8b recall | 0.20 [0.20–0.20] | 0.77 [0.76–0.80] | +0.57, spread 0.00 |
| llama3.1:8b F1 | 0.24 [0.23–0.24] | 0.74 [0.72–0.75] | +0.50, spread 0.02 |
| qwen2.5-coder:7b precision | 0.60 [0.57–0.67] | 0.70 [0.67–0.74] | +0.10, equal to the spread |
| qwen2.5-coder:7b recall | 0.16 [0.16–0.16] | 0.65 [0.64–0.68] | +0.49, spread 0.00 |
| qwen2.5-coder:7b F1 | 0.25 [0.25–0.26] | 0.68 [0.65–0.71] | +0.42, spread 0.01 |
| recall on clear conflicts (worst of three) | 1 / 11 on both models | 9 / 11 (llama) · 6 / 11 (coder) | |
| aligned requirements left alone (worst of three) | 21 / 26 (llama) · 25 / 26 (coder) | 18 / 26 (llama) · 21 / 26 (coder) | the cost of the fix |
| key entities reached by any requirement chunk (no model) | 11 / 15 | 15 / 15 | deterministic |
| key entities reached by every document that constrains them (no model) | 5 / 15 | 15 / 15 | deterministic |

Table 5.3. Conflict scanner before and after the retrieval fix: same corpus, settings and day, three runs each, scored per entity over 25 conflict triples and 26 negatives.

A seven-arm ablation attributes the gain. With the retrieval passes off, recall falls from 0.72 to 0.28 and clear-conflict recall back to 1 of 11. With value verification off, recall falls to 0.56, because a model that copies the document's value into the "current value" field had its real conflicts dropped as already matching. With everything off, the baseline returns. What still fails is the model's reading: a marginal case (0.117 against 0.10) is never found, an absent property is read as "not applicable", and two to four more aligned requirements are flagged.

### 5.2.3 Ask: question answering over public models

| Fixture (schema) | Exact answers, passed / scored | Narrative answers, passed / scored | skipped | median latency |
|---|---|---|---|---|
| open-house (IFC4, 23 entities) | 4/4 | 2/3 | 3 | 3.3 s |
| duplex (IFC2X3, 241 entities) | 5/6 | 4/4 | 0 | 2.7 s |
| fzk-haus (IFC4, 51 entities) | 6/6 | 2/3 | 1 | 2.2 s |
| office-a (IFC2X3, 901 entities) | 5/6 | 3/4 | 0 | 2.0 s |
| total | 20 / 22 | 11 / 14 | 4 | |

Table 5.4. Ask benchmark, first published run. An answer passes when the exact count, storey names, schema string or an expected keyword appears and it is not a refusal.

Counts of doors, windows and walls, storey lists and the schema were right on every model. The five failures are two space counts answered from excerpts instead of the count recipe, two narrative answers that explained a method instead of answering, and one correct "no fire rating in this model" that the scorer counts as a refusal. {magenta}Retrieval quality is not measured directly: hit@k and MRR are future work.{/magenta}

### 5.2.4 What is not measured, and threats to validity

OCR accuracy is out of scope: GLM-OCR is a free, local convenience for scanned pages, not expected to match frontier multimodal models (Section 6.1). The tier-level correctness, schema-adherence and latency figures of the July draft traced to no record and are withdrawn. The 12 GB and 24 GB rows, an adversarial sandbox corpus and {magenta}an expert labelling scored against the harness with Cohen's κ (Cohen, 1960){/magenta} are open. Each row is one model on one machine and claims nothing beyond it. The Modify and planted-conflict corpora were written by the authors, and their disputed conventions are documented, not fixed after the fact. Samples are small, so every rate carries its denominator. Local models are non-deterministic at temperature 0.1: Modify rows keep the worse of two runs, RAV rows report the range of three, and a change counts as an improvement only beyond the baseline's range.

## 5.3 Test suite and quality assurance

The suite collects 2,676 tests plus 19 Playwright end-to-end tests, with `ruff` lint and format as a merge gate. Model, embedding, Git and file-system boundaries are mocked; only the harnesses call a model. The suite shows behaviour under specified inputs, not end-to-end efficacy.

| Area | Tests |
|---|---|
| Facilities (7D) | 473 |
| Quantities / 5D takeoff | 412 |
| Write-back (pipeline, Guardian, conflict scanner, RAV harness, Git, consumers) | 419 |
| Core platform (BYOK, budgets, LLM routing, staff) | 338 |
| IFC processing (parser, diff, sandbox, round-trip) | 295 |
| Scheduling (4D) | 263 |
| Environments and deployment | 162 |
| Ask / RAG and the Ask benchmark scorer | 109 |
| 5D cost | 66 |
| Documents and OCR | 37 |
| Classification | 35 |
| IFC viewer, embeddings, users, beta funnel, MetaCastor, model quality | 67 |
| Playwright end-to-end | 19 |

Table 5.5. Collected tests per application, `pytest --collect-only`, 2026-09-16.

## 5.4 External and independent validation

**Peer demonstration.** At Zigurat Student Week (June 2026), about forty colleagues and professors saw a live demonstration. Interest concentrated on the lifecycle surfaces; the most consistent criticism was the absence of hard numbers, which produced Section 5.2; usability was raised alongside capability; and attendees with Middle East experience named IFC round-trip friction inside cloud CDEs as a blocker.

{cyan}**Expert testing of the write path.** Two team members who did not write the write-path code logged structured testing chronologically, never revising an entry. They checked every claim against an independent reading of the same file: IfcOpenShell scripts on the buildingSMART Duplex model, and Solibri Model Checker on two further test sets. On the rewritten pipeline, the hand check corroborated what the harness measures. The blind explanation stated property, value and count and nothing else, and the diff shown was computed from the file (`erez.csv` row 86). A committed write on an IfcRoof changed only its own property-set entities (row 91). The check also found four open defects:{/cyan}

- {cyan}**Type resolution.** The exact IFC class is required, and a supertype or bare GlobalId is declined (row 85). Every decline is now a refusal, where the tiered pipeline had proposed changes to 200 walls.{/cyan}
- {cyan}**Materials.** Material assignments are compared by the diff but not offered to the model (row 87).{/cyan}
- {cyan}**Guardian verdicts.** Under control, the Guardian cited the applicable clause 6 of 6 times, where the tiered build had failed 4 of 4 (rows 83, 94, 97). But it confirms only an exact match: compliant values below a maximum are flagged and a non-compliant textual value passes, for 6 correct verdicts of 10 (rows 95, 97).{/cyan}
- {cyan}**Scan throughput.** The conflict scan checks pairs one at a time, about 106 s each on that machine (row 93).{/cyan}

{magenta}The log is narrative, not per-prompt, so no agreement statistic against the harness is reported.{/magenta}

{cyan}**Planted conflicts, three measurements.** Maria's Solibri-referenced scan detected 5 of 5 planted conflicts at class level, all missing-property, with no false positives against 7 aligned requirements (`maria.csv` row 14). Erez's numeric threshold violations were detected 0 of 3 on both pipelines (rows 79, 96). The 2026-08-30 harness showed the same split on a third model: missing-property recall 4 of 13 against clear numeric 1 of 11. Three evaluators, models and corpora agree that numeric comparison is the weakest conflict class. The retrieval fix raised it to 9 and 6 of 11 on the harness corpus, but the fixed scanner still found 0 of 3 on Erez's corpus, which the authors did not write: the gain has not yet been shown to transfer.{/cyan}
