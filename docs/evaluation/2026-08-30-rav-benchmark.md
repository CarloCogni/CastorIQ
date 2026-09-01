# Evaluation — RAV Conflict-Scan Benchmark (first measured run)

**Date:** 2026-08-30
**Harness:** `manage.py benchmark_rav` (docs/benchmarks.md §Harness D)
**Corpus:** `fixtures/benchmark/rav/` — 24 labelled cases (12 planted conflicts, 12 aligned requirements) over 15 entities of `Ifc4_SampleHouse.ifc`, three consultant-style documents
**Model:** `llama3.1:8b`, temp 0.1, embeddings as configured in the dev stack
**Artifacts:** `runs/rav-2026-08-30-default.json`, `runs/rav-2026-08-30-ablate.json`

---

# Part A — What this demonstrates

## The gap this closes

Until this run, RAV accuracy was a described limitation, not a number. The
conflict scanner shipped with four false-positive mitigations (keyword
prefilter, EXTRACT→GATE→COMPARE prompt, element-type gate, confidence
threshold) — none of them measured. This benchmark plants known conflicts of
three severities (clear / marginal / missing-property) plus aligned
requirements into realistic documents and scores the scanner per entity.

## The headline finding

**The bottleneck is retrieval, not LLM comparison.** With production settings
the scanner reaches only 11 of 15 key entities at all, and reaches several of
them from the wrong documents' chunks. Two entire conflict cases — external
wall U-values (3 entities) and window U-values (4 entities) — are
*structurally invisible*: no thermal-specification chunk retrieves those
entities within `ENTITY_TOP_K = 5`, so no LLM call ever sees the mismatch.
That accounts for most of the weak "clear" recall (1/11). The LLM comparison
step, when it is actually shown the right chunk and entity, performs
noticeably better (missing-property recall 4/13, and the negatives hold at
22/26).

Corollary: prompt-side work (few-shot, better instructions) cannot fix the
recall problem. The fix has to be in the entity↔chunk mapping — higher top-K,
a per-entity (entity-first) retrieval pass for numeric properties, or
type-aware quotas so three near-identical walls don't crowd each other out.

## Secondary findings

1. **Chunk misattribution.** A finding's source document comes from the LLM's
   own `source_chunk_index`, which is sometimes wrong (a real fire-rating
   conflict cited to the thermal spec). Strict scoring charges this twice
   (miss + false positive). Document-relaxed scoring isolates the cost:
   F1 0.24 → 0.29. Real, but minor next to the retrieval gap.
2. **Hallucinated IFC values.** Two findings invented `EI60` as the current
   IFC value on entities that carry no FireRating at all. The equal-value
   guard cannot catch an invented value; only value verification against the
   entity's actual properties could.
3. **Bystander entities.** Three `IfcCovering`s and one `IfcPlate` — never
   mentioned in any document — were retrieved and generated most of the false
   positives (e.g. covering flagged against the roof's U-value requirement).
   The element-type gate does not fire because "covering" is a mapped label
   and the LLM labelled the requirement generically.
4. **Identical entities, inconsistent treatment.** Of three literally
   identical external walls, two were reached and one was not; of four
   identical windows, one. Per-chunk top-K makes retrieval a lottery among
   near-duplicate embeddings.

---

# Part B — Evidence

## Method

Setup: `benchmark_rav --setup` uploads the three corpus PDFs and runs the
document pipeline (13 chunks, all passing the requirement-keyword filter).
Run: `benchmark_rav --project "RAV Benchmark" --json …` executes a real
`ConflictScanService.full_scan()` and scores the resulting `Conflict` rows
against `key.json` per entity: a case spanning three walls is three hits or
misses. Findings match a case on (GlobalId, canonical property, source
document); property aliases ("U-value" → `ThermalTransmittance`) are applied
before matching.

## Results — production settings (strict scoring)

| Metric | Value |
|---|---|
| Precision | **0.29** (5 TP / 12 FP) |
| Recall | **0.20** (5 TP / 20 FN) |
| F1 | 0.24 |
| Recall, clear conflicts | 1/11 |
| Recall, marginal conflicts | 0/1 |
| Recall, missing-property conflicts | 4/13 |
| Aligned requirements left alone | 22/26 |
| Entities scanned | 15 · duration 184 s |

Document-relaxed scoring (misattribution forgiven): P 0.35, R 0.24, F1 0.29 —
the strict/relaxed gap is the cost of finding-to-chunk misattribution.

## Retrieval coverage (no LLM involved)

Reproduced deterministically from `_build_entity_chunk_map`:

| Key entity group | Reached / total | Reached by the right document? |
|---|---|---|
| external_walls | 2/3 | fire + structural docs only — **never** by the thermal spec |
| partitions | 2/2 | one wall by all three docs, one by acoustic only |
| internal_doors | 2/2 | all three docs |
| external_door | 1/1 | fire doc only — thermal case TH-05 unreachable |
| windows | 1/4 | acoustic doc only — thermal case TH-02 unreachable |
| ground_slab | 1/1 | fire + structural — thermal case TH-04 unreachable |
| roof_deck_slab, roof | 2/2 | all three docs |
| *(non-key)* | 3 × IfcCovering, 1 × IfcPlate also retrieved | source of most FPs |

Upper bound on recall imposed by retrieval alone: 11/20 conflict triples were
even *presented* to the LLM with any chunk, and far fewer with the chunk that
carries the relevant requirement.

## Ablation — what each mitigation buys

Sweep: `--ablate` (`runs/rav-2026-08-30-ablate.json`).

| | default | no type gate | no keyword filter | conf = 0 | all off |
|---|---|---|---|---|---|
| Precision | 0.31 | 0.29 | 0.33 | 0.21 | 0.28 |
| Recall | 0.20 | 0.20 | 0.24 | 0.12 | 0.20 |
| F1 | 0.24 | 0.24 | 0.28 | 0.15 | 0.23 |
| Recall clear / marginal / missing | 1/11 · 0/1 · 4/13 | 1/11 · 0/1 · 4/13 | 1/11 · 0/1 · 5/13 | 0/11 · 0/1 · 3/13 | 1/11 · 0/1 · 4/13 |
| Negatives held | 22/26 | 22/26 | 21/26 | 22/26 | 21/26 |
| TP / FP / FN | 5/11/20 | 5/12/20 | 6/12/19 | 3/11/22 | 5/13/20 |
| Findings · duration | 16 · 162 s | 17 · 187 s | 19 · 175 s | 14 · 177 s | 18 · 170 s |

Reading: on this corpus the mitigations' measured effect is **marginal — the
differences (±1–2 findings) are within single-run LLM variance**, and dropping
the confidence threshold *hurt* rather than flooded (a variance artifact, not a
mechanism). This is consistent with the headline finding: the gates rarely get
the chance to matter because the right (entity, chunk) pairs mostly never form
at retrieval. The honest claim for the report is therefore *not* "our
mitigations rescued precision" but "the mitigations are second-order; the
retrieval stage dominates both error types" — and note that the type gate's
value was established separately by its regression tests
(`test_evaluate_entity_drops_wall_requirement_on_beam`), on inputs this small
corpus does not generate. Note also that the default column here (P 0.31)
differs slightly from the standalone run above (P 0.29): same settings, two
runs — that gap *is* the run-to-run variance floor, and any claimed
improvement must exceed it.

## Consequences

1. Raise `ENTITY_TOP_K` and/or dedupe near-identical entities before top-K so
   identical walls stop crowding each other out; re-run and diff via
   `--baseline`.
2. Consider an entity-first pass for entities carrying properties named in
   requirement chunks (exact property-name index lookup, no embeddings).
3. Verify `ifc_value` against the entity's actual properties before persisting
   a finding — kills hallucinated current values.
4. Attribute findings to the chunk containing the requirement text (string
   match on property/value) instead of trusting `source_chunk_index`.

## Caveats

- One model (`llama3.1:8b`), one run — no variance estimate yet (`--repeat` is
  not implemented for this harness).
- The corpus is small (24 cases, ~46 scored triples) and self-labelled;
  expert review of the planted values is the next validation step.
- The sample house is tiny; retrieval crowding may behave differently on a
  model with thousands of entities (likely worse, which strengthens rather
  than weakens the retrieval finding).

## Reproduction

```bash
cd src
uv run manage.py benchmark_rav --project "RAV Benchmark" --setup
uv run manage.py benchmark_rav --project "RAV Benchmark" --json ../runs/rav.json
uv run manage.py benchmark_rav --project "RAV Benchmark" --ablate
```
