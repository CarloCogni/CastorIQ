# Benchmarks & Evaluation

This page is the single point of truth for how Castor measures itself. Castor has **multiple independent benchmark harnesses** that share vocabulary ("benchmark", "corpus", "runs/") but nothing else — they answer different questions, run through different entry points, and live in different parts of the repo. If you only remember one thing from this page, remember the table below.

| | **IFC parser benchmark** | **NL writeback benchmark** |
|---|---|---|
| Question answered | How fast and correctly does Castor parse IFC files? | Does the Modify pipeline understand real sentences and land real writes? |
| Kind | Performance / correctness regression | Quality evaluation (LLM-dependent) |
| Entry point | `benchmarks/ifc_parser/benchmark.py` (standalone script) | `manage.py benchmark_writeback` (Django command) |
| Corpus | `fixtures/parser_corpus/` — public IFC files, downloaded on demand | `fixtures/benchmark/pipeline-test-prompts.txt` — 92 natural-language prompts |
| Needs | Python venv only (`uv sync`) | Docker + PostgreSQL + a live LLM + a processed project |
| Output | `runs/ifc_parser_bench/results.md` + `.csv` | Console report; `--json` snapshot under `runs/` |
| Full guide | [`benchmarks/ifc_parser/BENCHMARKING.md`](../benchmarks/ifc_parser/BENCHMARKING.md) | [`docs/testing.md` §Natural-Language Benchmark](testing.md#natural-language-benchmark) |

---

## Harness A — IFC parser benchmark

A regression harness for the IfcOpenShell-based parsing pipeline, built on the shared public corpus used by the open-source IFC community (originated by Dion Moult, adopted by ThatOpen's engine_web-ifc and LTplus-AG's ifc-lite). It times three phases per file — `open`, Castor's `IFCParser` extraction, and geometry iteration — each in a fresh subprocess for honest cold-start numbers, and emits the exact 7-column table schema of `engine_web-ifc/benchmark.md` so results are directly diffable against the web-ifc reference.

Entity counts are compared against the reference **exactly** — a mismatch is a potential correctness bug (`ENTITY_MISMATCH`), not a performance data point. Mesh counts and absolute timings are informational: different tessellation engines and different machines are not comparable; only ratios between files and regressions over time on the same machine are.

```bash
uv run python benchmarks/ifc_parser/fetch_fixtures.py   # download the corpus (once)
uv run python benchmarks/ifc_parser/benchmark.py        # full run
```

The corpus is fetched sha256-verified from the ifc-lite GitHub release into `fixtures/parser_corpus/` (gitignored — it is downloadable, so it is never committed). The fetcher reads the manifest from the `ifc-lite` submodule; reference counts come from a local checkout of ThatOpen's `engine_web-ifc` at the repo root (optional — the harness skips reference checks if absent).

Setup, flags, and result interpretation: [`benchmarks/ifc_parser/BENCHMARKING.md`](../benchmarks/ifc_parser/BENCHMARKING.md).

## Harness B — NL writeback benchmark

Every pytest layer mocks the LLM, so the suite can be green while the system fails to understand a sentence a real user would type. `manage.py benchmark_writeback` closes that gap: it runs 92 real prompts through the full Modify pipeline against a real model and a real IFC file, and scores two deliberately separate dimensions:

- **Understanding** — did the pipeline route the request the way the corpus says it should? This varies by model and is the benchmark dimension.
- **Fidelity** — did the journal it produced actually land in the file, verified by reading the file back? This should stay at 100%; a drop means a writer or executor bug, not a comprehension one.
- **Integrity** — did the file change *only* where the journal said? The written copy is diffed against the untouched source on entity population, per-product geometry hash, and every bystander property (`ifc_processor/services/ifc_diff.py`). Also expected at 100%; a drop means a save corrupted something the request never mentioned.

The corpus lives at `fixtures/benchmark/pipeline-test-prompts.txt` and is bound literally to `fixtures/benchmark/Ifc4_SampleHouse.ifc` (buildingSMART sample house). Assertions are encoded in `router:` comment lines above each prompt — executable grammar, not prose. Runs never touch the project's own IFC file: each case executes against a scratch copy in a temporary directory.

```bash
cd src
uv run manage.py benchmark_writeback --project <uuid> --json ../runs/baseline.json   # save a baseline
uv run manage.py benchmark_writeback --project <uuid> --baseline ../runs/baseline.json  # regression check
```

- How to run it, model comparison, `--repeat`, safety: [`docs/testing.md` §Natural-Language Benchmark](testing.md#natural-language-benchmark)
- Corpus grammar and the sample-model contract: [`fixtures/benchmark/README.md`](../fixtures/benchmark/README.md)

## Harness D — RAV / conflict-scan benchmark

The Guardian and the conflict scanner are the RAV (Retrieval-Augmented Verification) surface, and until this harness existed their accuracy was asserted, never measured. `manage.py benchmark_rav` scores `ConflictScanService` against a **planted-conflict corpus**: three consultant-style documents written for the sample house (`fixtures/benchmark/rav/`), every requirement labelled in `key.json` as a planted conflict (with a severity class — clear / marginal / missing-property) or an aligned requirement the scanner must leave alone.

Reported per entity: precision, recall overall and by severity, and "negatives held" (aligned requirements not flagged). `--ablate` sweeps production settings against each false-positive mitigation switched off (element-type gate, requirement-keyword filter, confidence threshold), which is the before/after-mitigation evidence.

```bash
cd src
uv run manage.py benchmark_rav --project <uuid> --setup                     # upload + process the corpus PDFs (once)
uv run manage.py benchmark_rav --project <uuid> --json ../runs/rav.json     # score, save artifact
uv run manage.py benchmark_rav --project <uuid> --ablate                    # mitigation ablation table
```

Corpus conventions and editing rules: [`fixtures/benchmark/rav/README.md`](../fixtures/benchmark/rav/README.md). Key parsing and the scoring maths are unit-tested without an LLM in `src/writeback/tests/test_benchmark_rav.py`.

### Related tools

- **`manage.py dry_run_v2_pipeline`** — the single-prompt debugger. It dumps every pipeline stage's output for **one** prompt, which the benchmark deliberately does not. Use it to dissect a failing case the benchmark surfaced; use `benchmark_writeback` for anything batch or scored.
- **`src/writeback/tests/test_benchmark_corpus.py` / `test_benchmark_verify.py` / `test_benchmark_integrity.py`** — pytest unit tests for the corpus parser, the file-readback verifiers, and the integrity diff. No LLM needed; they run in the normal fast suite.
- **`src/ifc_processor/tests/test_ifc_round_trip.py`** — standalone round-trip integrity suite: open → save → diff is empty; each Tier 1 writer op changes exactly its target and nothing else; geometry drift and entity loss are detected.

---

## The `fixtures/` directory, disambiguated

`fixtures/` holds three unrelated things. None of them are pytest fixtures — Castor's test suite uses factories, not fixture files (see [testing.md](testing.md)); the one committed pytest `.ifc` fixture lives under `src/ifc_processor/tests/fixtures/`.

| Path | What it is | In git? |
|---|---|---|
| `fixtures/benchmark/` | NL benchmark corpus + sample IFC + README (harness B) | Yes — a benchmark nobody can reproduce is not a benchmark |
| `fixtures/benchmark/rav/` | Planted-conflict documents + ground-truth key (harness D) | Yes — same reason |
| `fixtures/parser_corpus/` | Downloaded parser corpus (harness A) | No — fetched on demand, sha256-verified |
| `fixtures/sample-project/` | Demo project seed for `manage.py provision_sample_project` — unrelated to benchmarking | Yes |

## Results convention: `runs/`

Both harnesses write results under `runs/`, which is **gitignored** — benchmark output is machine- and moment-specific noise by default. Baselines are the exception: after a known-good run, commit one deliberately with `git add -f` (e.g. `runs/ifc_parser_bench/results.csv` or a `--json` snapshot from the writeback benchmark) so future runs have something to diff against. This convention is defined here; the harness docs point back to this page.

## Evaluation records: `docs/evaluation/`

Significant benchmark runs get a dated write-up in `docs/evaluation/`. These records are **immutable** — they describe what a specific run found on a specific date and are never updated as the system changes; later runs get their own file.

- [2026-08-05 — Natural-Language Write-Back Benchmark](evaluation/2026-08-05-writeback-nl-benchmark.md) — first full run of harness B; found a silent removal-intent corruption (success reported while writing the wrong value across five walls) and a `SET_ATTRIBUTE` crash.
- [2026-08-30 — RAV Conflict-Scan Benchmark](evaluation/2026-08-30-rav-benchmark.md) — first measured RAV run (harness D): P 0.29 / R 0.20 on the planted corpus, and the finding that the bottleneck is entity↔chunk retrieval (4/15 key entities never retrieved; two conflict cases structurally unreachable at top-K 5), not LLM comparison.

---

**Status note (2026-08-30):** harness code, the NL corpus, the sample IFC, and `docs/evaluation/` are all tracked; a clean clone can run harness B after processing the sample house into a project.
