# Evaluation — Ask benchmark (first published run)

**Date:** 2026-09-15
**Harness:** `manage.py benchmark_ask` (docs/benchmarks.md §Harness C)
**Corpus:** 10 fixture-agnostic questions (6 Tier 1 with one right answer; 4 Tier 2 narrative) × 4 public IFC models from ThatOpen's `engine_web-ifc` test set, sha256-pinned: `IfcOpenHouse_IFC4.ifc`, `duplex.ifc`, `AC20-FZK-Haus.ifc`, `Office_A_20110811.ifc`. Every expected value is computed independently with IfcOpenShell from the fixture at run time.
**Model:** `llama3.1:8b` (the site's Ask model), embeddings `mxbai-embed-large`; RAG with the deterministic recipes on (`docs/rag-pipeline.md`)
**Machine:** RTX 4070 Laptop, 8 GB graphics memory, Ollama
**Artifacts:** `runs/ask-2026-09-15.{open-house,duplex,fzk-haus,office-a}.json` (every answer verbatim)

This record is immutable by convention.

---

# Part A — What this demonstrates

Until this run the Ask path was the one surface with a harness and no number.
Scoring is answer-level: a Tier 1 case passes when the exact count, at least
half the storey names, or the schema string appears in the answer; a Tier 2
case passes when a real material or space name, or an expected keyword,
appears and the answer is not a refusal. It does not measure retrieval
directly (no hit@k, no MRR) and it does not judge whether a passing answer is
*good*: one office-a answer passed the fire-rating keyword test with the
sentence "The fire rating of the doors and walls is 'Fire Rating'", which is
a keyword hit and a useless answer. N is 36 scored answers across four models
(4 of 40 skipped where a fixture has no ground truth for the case).

**Headline:** 20 of 22 Tier 1 answers and 11 of 14 Tier 2 answers correct. Counts of doors,
windows and walls were right on every model (the deterministic entity-count
recipe answers those from the index, not from the model's memory), as were the
storey lists and the schema. The five failures are two space counts on the
IFC2X3 models (17 answered for 21 on duplex, 24 for 99 on Office_A: the
answer came from retrieved excerpts rather than the count recipe), two
"which walls are external and on which storeys" answers that explained how
one would find out instead of answering (fzk-haus, Office_A), and one
fire-rating answer that said no information exists (open-house, whose walls
carry no FireRating: a correct statement the keyword scorer counts as a
refusal).

# Part B — Evidence

| Fixture | Tier 1 passed / scored | Tier 2 passed / scored | skipped | median latency |
|---|---|---|---|---|
| open-house (IFC4, 23 entities) | 4/4 | 2/3 | 3 | 3.3 s |
| duplex (IFC2X3, 241 entities) | 5/6 | 4/4 | 0 | 2.7 s |
| fzk-haus (IFC4, 51 entities) | 6/6 | 2/3 | 1 | 2.2 s |
| office-a (IFC2X3, 901 entities) | 5/6 | 3/4 | 0 | 2.0 s |
| **total** | **20 / 22** | **11 / 14** | 4 | |

Per-case verdicts, all fixtures (`.` pass, `F` fail, `s` skipped):

| case | open-house | duplex | fzk-haus | office-a |
|---|---|---|---|---|
| t1-count-doors | . | . | . | . |
| t1-count-windows | . | . | . | . |
| t1-count-walls | . | . | . | . |
| t1-count-spaces | s (no IfcSpace) | F (17 for 21) | . | F (24 for 99) |
| t1-storeys | s (no named storeys) | . | . | . |
| t1-schema | . | . | . | . |
| t2-materials | . | . | . | . |
| t2-spaces | s | . | s | . |
| t2-fire-rating | F (correct "none", scored as refusal) | . | . | . (keyword hit on a useless answer) |
| t2-external-walls | . | . | F (hedged) | F (hedged) |

## Caveats

- One model, one run per fixture; no variance estimate. Latencies are
  per-question wall clock on a warm model.
- Answer-level keyword scoring over-counts (the office-a fire-rating case)
  and under-counts (the open-house one); the record keeps both verdicts as
  scored rather than adjusting them by hand. A retrieval-level metric
  (hit@k / MRR over retrieved entities and chunks, ground truth computable
  from the fixture) is the open programme item.
- Two fixtures (`IfcOpenHouse_IFC4.ifc`, `duplex.ifc`) changed upstream since
  the harness was written; their hashes were re-pinned on 2026-09-15 with a
  dated note in `chat/services/ask_benchmark/fixtures.py`. Entity counts in
  this record are for the re-pinned files.

## Reproduction

```bash
cd src
uv run manage.py benchmark_ask --json ../runs/ask.json   # all four fixtures; parses them on first use
```
