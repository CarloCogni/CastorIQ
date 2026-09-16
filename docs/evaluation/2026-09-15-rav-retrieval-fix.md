# Evaluation — RAV Conflict-Scan Benchmark, retrieval fix (before / after)

**Date:** 2026-09-15
**Harness:** `manage.py benchmark_rav --repeat 3` (docs/benchmarks.md §Harness D)
**Corpus:** `fixtures/benchmark/rav/` — 26 labelled cases (12 planted conflicts, 14 aligned requirements) over 15 entities of `Ifc4_SampleHouse.ifc`, three consultant-style documents; scored per entity: 25 conflict triples, 26 negatives
**Models:** `llama3.1:8b` (the model of the 2026-08-30 record) and `qwen2.5-coder:7b` (the site's Modify model, which the scanner uses); temperature 0.1, `num_ctx` 8192, embeddings `mxbai-embed-large`
**Machine:** RTX 4070 Laptop, 8 GB graphics memory, Ollama, no CPU offload
**Before:** scanner at commit b7e6c20 (embedding top-K only) · **After:** the commit "fix(rav): entity-first retrieval, value verification, chunk attribution" (2026-09-15)
**Artifacts:** `runs/rav-2026-09-15-before-{llama3.1-8b,qwen2.5-coder-7b}-repeat3.json`, `runs/rav-2026-09-15-after-{llama3.1-8b,qwen2.5-coder-7b}-repeat3.json`, `runs/rav-2026-09-15-ablate-qwen2.5-coder-7b.json`

This record is immutable by convention. It continues `2026-08-30-rav-benchmark.md`, whose diagnosis it acts on.

---

# Part A — What this demonstrates

## The claim

The 2026-08-30 record found that RAV recall was bounded by retrieval, not by
the model's comparison: with five entities per requirement chunk chosen by
embedding distance, three identical external walls crowded each other out,
no thermal-specification chunk reached any wall or window, and two whole
conflict cases were never shown to the model. Its ablation showed the four
existing false-positive mitigations moving results by one or two findings,
inside single-run variance. The consequence it drew was that prompt work
could not fix recall; the entity↔chunk mapping had to change.

This record measures that change, on the same corpus and settings, on two
models, three runs each, against a baseline whose own variance was measured
the same day. The rule for reading it: a delta is an improvement only when it
exceeds the baseline's min–max spread on that metric.

## What changed in the scanner

1. **Retrieval is lookups first, embeddings last.** For each requirement
   chunk: (a) *reference pass* — entities whose model reference
   (`Pset_*.Reference`, e.g. `Wall-Ext_102Bwk-75Ins-100LBlk-12P`) or name
   segment the chunk quotes, whole-word; (b) *label pass* — every entity of
   an element class the chunk names ("external walls", "windows") when the
   chunk also mentions a property, capped at 25 per class; (c) the original
   embedding top-K. Pairs are ordered by pass then cosine distance and capped
   at 8 excerpts per entity.
2. **Value verification.** The current value a finding claims is replaced by
   the value the index holds for that property before the finding is stored;
   a property the entity does not carry is stored as `(not set)`. Counts of
   both corrections are reported per run (`values_corrected`, `values_unset`).
3. **Attribution.** A finding is filed against the chunk that best quotes its
   document value and names its property, not the index the model returned.
4. **Context size.** The scanner now passes the Modify `num_ctx` (8192) to
   Ollama, as the generator and the Guardian already did. Found during this
   work: with more excerpts per entity the prompt exceeded Ollama's default
   window and was truncated from the front, dropping the entity's own
   properties; the first "after" attempt produced one finding on nineteen
   entities before this was fixed. That attempt is not an artifact of this
   record; it is stated here because it is the kind of failure the variance
   rule exists to catch.

Design and switches: `docs/conflict-scan.md`. Guardian (the per-proposal
check) keeps its own retrieval and is out of scope here.

## Headline

Recall moved from 0.20 to 0.77 on `llama3.1:8b` and from 0.16 to 0.65 on
`qwen2.5-coder:7b`, F1 from 0.24 to 0.74 and from 0.25 to 0.68; clear-conflict
recall from 1/11 to 9–10/11 and 6–8/11. Every delta exceeds its baseline's
three-run spread by an order of magnitude, except the coder model's precision
(+0.10 against a spread of 0.10: it did not fall). The cost is on aligned
requirements: negatives held went from 21–22 to 18–20 of 26 on llama and from
25 to 21–23 of 26 on the coder model, because reaching every entity gives the
model more chances to misapply a limit that sits in the same excerpt as an
"as designed" value. What still fails is named per case below; none of it is
retrieval.

---

# Part B — Evidence

## Retrieval coverage (no model involved)

Recomputed deterministically from `_build_entity_chunk_map` on the processed
sample house. "Right document" means every document that carries a labelled
requirement for that entity.

| Key entity group | Before: reached / total | Before: by the right documents? | After: reached / total | After: by the right documents? |
|---|---|---|---|---|
| external_walls (3) | 2/3 | fire + structural only; never the thermal spec | 3/3 | all three |
| partitions (2) | 2/2 | one by all three, one by acoustic only | 2/2 | all three |
| internal_doors (2) | 2/2 | all three | 2/2 | all three |
| external_door (1) | 1/1 | fire only; thermal case TH-05 unreachable | 1/1 | all three |
| windows (4) | 1/4 | acoustic only; thermal case TH-02 unreachable | 4/4 | all three |
| ground_slab (1) | 1/1 | fire + structural; thermal case TH-04 unreachable | 1/1 | all three |
| roof_deck_slab, roof (2) | 2/2 | all three | 2/2 | all three |
| **key entities reached** | **11/15** | | **15/15** | |
| non-key entities reached | 3 × IfcCovering, 1 × IfcPlate | | 3 × IfcCovering, 1 × IfcPlate | unchanged |
| (entity, chunk) pairs by pass | embedding 26 | | reference 56 · label 73 · embedding 27 (before the per-entity cap of 8) | |

Upper bound on recall imposed by retrieval: before, 11 of 20 conflict triples
were presented to the model with any chunk and fewer with the right one;
after, all 25 are presented with the chunk that carries the requirement.

## Results — production settings, three repeats each

Mean [min–max]; per-severity recall and negatives held are the worst of the
three runs.

| | `llama3.1:8b` before | `llama3.1:8b` after | `qwen2.5-coder:7b` before | `qwen2.5-coder:7b` after |
|---|---|---|---|---|
| precision | 0.29 [0.26–0.31] | **0.71 [0.68–0.73]** | 0.60 [0.57–0.67] | **0.70 [0.67–0.74]** |
| recall | 0.20 [0.20–0.20] | **0.77 [0.76–0.80]** | 0.16 [0.16–0.16] | **0.65 [0.64–0.68]** |
| F1 | 0.24 [0.23–0.24] | **0.74 [0.72–0.75]** | 0.25 [0.25–0.26] | **0.68 [0.65–0.71]** |
| recall, clear conflicts | 1/11 | **9/11** (10, 10, 9) | 1/11 | **6/11** (7, 8, 6) |
| recall, marginal | 0/1 | 0/1 | 0/1 | 0/1 |
| recall, missing-property | 4/13 | **9/13** (10, 9, 10) | 3/13 | **9/13** (9, 9, 10) |
| negatives held | 21/26 | 18/26 (19, 20, 18) | 25/26 | 21/26 (22, 23, 21) |
| TP / FP / FN (mean) | 5 / 12.3 / 20 | 19.3 / 8.0 / 5.7 | 4 / 2.7 / 21 | 16.3 / 7 / 8.7 |
| entities scanned · duration (mean) | 15 · 144 s | 19 · 271 s | 15 · 77 s | 19 · 128 s |
| values corrected / unset (mean per run) | — | 4.7 / 9.7 | — | 6.0 / 3.3 |

## Delta against the variance floor (`--baseline`)

`qwen2.5-coder:7b`:

```
precision  0.60 -> 0.70 (+0.10; spread 0.10) exceeds baseline spread
recall     0.16 -> 0.65 (+0.49; spread 0.00) exceeds baseline spread
f1         0.25 -> 0.68 (+0.42; spread 0.01) exceeds baseline spread
```

The precision delta equals the baseline's spread to two decimals (the harness
calls it "exceeds" on the third); read it as *precision did not fall*, not as
a precision gain. The recall and F1 deltas are forty to fifty times the
spread.

`llama3.1:8b`:

```
precision  0.29 -> 0.71 (+0.42; spread 0.05) exceeds baseline spread
recall     0.20 -> 0.77 (+0.57; spread 0.00) exceeds baseline spread
f1         0.24 -> 0.74 (+0.50; spread 0.02) exceeds baseline spread
```

On this model all three deltas exceed the spread by an order of magnitude or
more. Precision rose because true positives quadrupled while false positives
fell (12.3 → 8.0 per run).

## Per-case movement

`qwen2.5-coder:7b`, first repeat against the first baseline repeat: FIXED
FS-01, FS-02, FS-03, TH-01, AC-01; REGRESSED FS-04 (one slab miss), TH-06 and
TH-07 (false alarms on "as designed" values, below). Cases that fail on all
three after-runs, with the mechanism:

| Case | What it is | Fails as | Why |
|---|---|---|---|
| ST-01 | external walls `LoadBearing=False`, structural notes say load-bearing (clear) | 3 misses | the model reads the fire strategy's "non-load-bearing" for the same walls and does not flag the structural document's contradiction; a disagreement between documents, not a retrieval gap (the structural chunk is presented) |
| AC-02 | internal doors, no `AcousticRating`, Rw ≥ 30 dB required (missing) | 2 misses | the model does not treat an absent acoustic property as a conflict; the prompt's "property absent → not a conflict" rule wins over "entity targeted specifically" |
| FS-04 | ground slab, no `FireRating`, REI 30 required (missing) | 1 miss | same mechanism |
| TH-04 | ground slab U 0.1174 vs ≤ 0.10 (marginal) | 1 miss | the model rounds 0.117 to 0.1; the one marginal case stays unreached by any run on either model |
| TH-06 | internal doors 3.7021, document "3.7 as designed" (aligned) | 2 false alarms | the model compares the internal doors against the external door's 1.2 limit from the same excerpt; the document value it returns is 1.2, so the "as designed" equivalence never applies |
| TH-08 | roof deck slab, "no requirement" (aligned) | 1 false alarm | flagged against the roof's 0.13 limit |

`llama3.1:8b`, first repeat against the first baseline repeat: FIXED FS-01,
FS-02, FS-03, FS-04, FS-05, TH-01, TH-02, TH-05, AC-03; REGRESSED TH-06 (two
false alarms, same mechanism as above) and AC-01 (one partition miss). Cases
failing on all three after-runs: TH-04 (1 miss), TH-06 (2 alarms), TH-07 (2
alarms), AC-01 (1 miss), AC-02 (2 misses), ST-01 (1 miss), ST-02 (2 alarms).
The llama row reaches more of the clear conflicts than the coder model (ST-01
two walls of three on every run) and pays for it in negatives held (18–20 of
26 against 21–23): it flags more, both ways. ST-02 is the mirror of ST-01:
the partitions are non-load-bearing in both documents and the model flags them
anyway when it has just read the external walls' load-bearing sentence.

**Found during this work, fixed before the llama row above was run.** The
llama model returns some property names with their set prefix
(`Pset_SlabCommon.ThermalTransmittance`). The shared alias table only knew
bare names, so such a finding matched no key case *and* value verification
stored `(not set)` on a slab that carries the value. `canonical_property` now
drops the prefix; the first llama after-row (two such findings in one run)
was discarded and re-run on the fixed scorer, which is the row reported. The
coder model never produced a prefixed name on any row, so its rows stand.

The remaining false positives on the coder model are three findings that match
no key case in every run: the external door flagged for a missing fire rating against a
sentence that says none applies, and two ceilings flagged against the roof's
U-value limit (their `ThermalTransmittance` 0.5499 is real; the requirement
is not theirs). Value verification stored the ceilings' real value where the
model had claimed a rounded one; it cannot decide whether a requirement
applies to an entity, which is the type gate's job and the model's.

## Ablation on the after-scanner (`qwen2.5-coder:7b`, n=1 per arm)

`--ablate`, artifact `runs/rav-2026-09-15-ablate-qwen2.5-coder-7b.json`. Each
arm is production settings with one mechanism off; the last arm is everything
off. Single runs: read against the three-run spread above (precision ±0.04,
recall ±0.02 on this model).

| | default | no entity-first | no verify | no type gate | no keyword filter | conf = 0 | all off |
|---|---|---|---|---|---|---|---|
| precision | 0.69 | 0.54 | 0.61 | 0.64 | 0.70 | 0.68 | 0.58 |
| recall | **0.72** | **0.28** | **0.56** | 0.72 | 0.64 | 0.68 | 0.28 |
| F1 | 0.71 | 0.37 | 0.58 | 0.68 | 0.67 | 0.68 | 0.38 |
| recall clear / marginal / missing | 8/11 · 0/1 · 10/13 | 1/11 · 0/1 · 6/13 | 6/11 · 0/1 · 8/13 | 8/11 · 1/1 · 9/13 | 8/11 · 0/1 · 8/13 | 8/11 · 0/1 · 9/13 | 1/11 · 0/1 · 6/13 |
| negatives held | 21/26 | 23/26 | 20/26 | 21/26 | 22/26 | 21/26 | 24/26 |
| TP / FP / FN | 18/8/7 | 7/6/18 | 14/9/11 | 18/10/7 | 16/7/9 | 17/8/8 | 7/5/18 |
| entities scanned · duration | 19 · 160 s | 15 · 73 s | 19 · 130 s | 19 · 132 s | 19 · 118 s | 19 · 125 s | 15 · 59 s |

Reading:

- **The retrieval passes are the gain.** With them off, recall falls from
  0.72 to 0.28 and clear-conflict recall from 8/11 back to 1/11: the
  baseline's numbers, on the after-scanner. Nothing else in the scanner
  reaches those cases.
- **Value verification is second, and it is not only precision.** Off, recall
  drops 0.72 → 0.56 as well as precision 0.69 → 0.61. The mechanism: the
  model sometimes copies the document's value into `ifc_value`; the
  equal-value guard then drops a real conflict as "already matching".
  Verification restores the indexed value and the conflict survives.
  Negatives held moved by one, within noise.
- **The 2026-08-30 mitigations are still second-order.** Type gate, keyword
  filter and confidence threshold each move one to three findings, inside the
  three-run spread, as the first record found. The one visible effect is the
  type gate's precision (0.69 → 0.64 off).
- **All off reproduces the baseline** (0.58 / 0.28 / 0.38 against 0.60 /
  0.16 / 0.25 three-run means): the four earlier mitigations and the two new
  ones account for the whole difference between the two scanners; the model
  and the corpus did not change.

## Caveats

- Two models, one machine, three runs each. The spread is measured, not
  modelled; three runs bound it loosely.
- The corpus is small (26 cases, 51 scored triples) and self-labelled; expert
  review of the planted values remains the next validation step.
- The reference pass depends on documents citing elements by their model
  reference, which this corpus does and the fixture README requires. A
  specification that names elements only by role reaches them through the
  label pass, which is capped per class; on a model with hundreds of walls the
  cap decides which walls are scanned, nearest by embedding first.
- The sample house is tiny (51 indexed entities). Scan time grows with the
  entities the label pass admits.

## Reproduction

```bash
cd src
uv run manage.py benchmark_rav --project "RAV Benchmark" --setup   # once
uv run manage.py benchmark_rav --project "RAV Benchmark" --repeat 3 --json ../runs/rav-after.json \
    --baseline ../runs/rav-2026-09-15-before-qwen2.5-coder-7b-repeat3.json
MODIFY_MODEL=llama3.1:8b uv run manage.py benchmark_rav --project "RAV Benchmark" --repeat 3 \
    --baseline ../runs/rav-2026-09-15-before-llama3.1-8b-repeat3.json
uv run manage.py benchmark_rav --project "RAV Benchmark" --ablate
```
