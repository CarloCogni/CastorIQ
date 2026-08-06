# Benchmarks & Evaluation

This page is the single point of truth for how Castor measures itself. Castor has **two independent benchmark harnesses** that share vocabulary ("benchmark", "corpus", "runs/") but nothing else — they answer different questions, run through different entry points, and live in different parts of the repo. If you only remember one thing from this page, remember the table below.

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

The corpus lives at `fixtures/benchmark/pipeline-test-prompts.txt` and is bound literally to `fixtures/benchmark/Ifc4_SampleHouse.ifc` (buildingSMART sample house). Assertions are encoded in `router:` comment lines above each prompt — executable grammar, not prose. Runs never touch the project's own IFC file: each case executes against a scratch copy in a temporary directory.

```bash
cd src
uv run manage.py benchmark_writeback --project <uuid> --json ../runs/baseline.json   # save a baseline
uv run manage.py benchmark_writeback --project <uuid> --baseline ../runs/baseline.json  # regression check
```

- How to run it, model comparison, `--repeat`, safety: [`docs/testing.md` §Natural-Language Benchmark](testing.md#natural-language-benchmark)
- Corpus grammar and the sample-model contract: [`fixtures/benchmark/README.md`](../fixtures/benchmark/README.md)

### Related tools

- **`manage.py dry_run_v2_pipeline`** — the single-prompt debugger. It dumps every pipeline stage's output for **one** prompt, which the benchmark deliberately does not. Use it to dissect a failing case the benchmark surfaced; use `benchmark_writeback` for anything batch or scored.
- **`src/writeback/tests/test_benchmark_corpus.py` / `test_benchmark_verify.py`** — pytest unit tests for the corpus parser and file-readback verifiers. No LLM needed; they run in the normal fast suite.

---

## The `fixtures/` directory, disambiguated

`fixtures/` holds three unrelated things. None of them are pytest fixtures — Castor's test suite uses factories, not fixture files (see [testing.md](testing.md)); the one committed pytest `.ifc` fixture lives under `src/ifc_processor/tests/fixtures/`.

| Path | What it is | In git? |
|---|---|---|
| `fixtures/benchmark/` | NL benchmark corpus + sample IFC + README (harness B) | Yes — a benchmark nobody can reproduce is not a benchmark |
| `fixtures/parser_corpus/` | Downloaded parser corpus (harness A) | No — fetched on demand, sha256-verified |
| `fixtures/sample-project/` | Demo project seed for `manage.py provision_sample_project` — unrelated to benchmarking | Yes |

## Results convention: `runs/`

Both harnesses write results under `runs/`, which is **gitignored** — benchmark output is machine- and moment-specific noise by default. Baselines are the exception: after a known-good run, commit one deliberately with `git add -f` (e.g. `runs/ifc_parser_bench/results.csv` or a `--json` snapshot from the writeback benchmark) so future runs have something to diff against. This convention is defined here; the harness docs point back to this page.

## Evaluation records: `docs/evaluation/`

Significant benchmark runs get a dated write-up in `docs/evaluation/`. These records are **immutable** — they describe what a specific run found on a specific date and are never updated as the system changes; later runs get their own file.

- [2026-08-05 — Natural-Language Write-Back Benchmark](evaluation/2026-08-05-writeback-nl-benchmark.md) — first full run of harness B; found a silent removal-intent corruption (success reported while writing the wrong value across five walls) and a `SET_ATTRIBUTE` crash.

---

**Status note (2026-08):** the harness code, the NL corpus, the sample IFC, and `docs/evaluation/` are not yet committed — a clean clone currently cannot run either harness. Committing these assets is a pending git-hygiene pass, tracked separately from this documentation.
