# Benchmarks & Evaluation

This page is the single point of truth for how Castor measures itself. Castor has **multiple independent benchmark harnesses** that share vocabulary ("benchmark", "corpus", "runs/") but nothing else — they answer different questions, run through different entry points, and live in different parts of the repo. If you only remember one thing from this page, remember the table below.

| | **A · IFC parser** | **B · NL writeback** | **C · Ask** | **D · RAV conflict scan** |
|---|---|---|---|---|
| Question answered | How fast and correctly does Castor parse IFC files? | Does the Modify pipeline land the change a sentence asks for, and nothing else? | Does Ask answer real questions about real models correctly? | Does the conflict scanner find planted contradictions and leave aligned requirements alone? |
| Kind | Performance / correctness regression | Quality evaluation (LLM-dependent) | Quality evaluation (LLM-dependent) | Precision / recall (LLM-dependent) |
| Entry point | `benchmarks/ifc_parser/benchmark.py` | `manage.py benchmark_writeback` | `manage.py benchmark_ask` | `manage.py benchmark_rav` |
| Corpus | `fixtures/parser_corpus/` — public IFC files, downloaded on demand | `fixtures/benchmark/pipeline-test-prompts.txt` — 98 prompts | 10 fixture-agnostic questions × 4 public IFC models (downloaded on demand) | `fixtures/benchmark/rav/` — 3 planted documents + `key.json` |
| Needs | Python venv only (`uv sync`) | Docker + PostgreSQL + a live LLM + a processed project | Docker + PostgreSQL + a live LLM (fixtures parsed on first run) | Docker + PostgreSQL + a live LLM + the sample house processed |
| Output | `runs/ifc_parser_bench/results.md` + `.csv` | Console report; `--json` artifact | Console report; `--json` artifact per fixture | Console report; `--json` artifact; `--ablate` table |

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

Every pytest layer mocks the LLM, so the suite can be green while the system fails to understand a sentence a real user would type. `manage.py benchmark_writeback` closes that gap: it runs 98 real prompts through the V3 Modify pipeline (ground → generate → run → verify) against a real model on a scratch copy of the sample house, and scores:

- **Targets match** — did `select()` return exactly the entities the corpus names? Expectations are GlobalId-free (type, count, container, name, property filter) and resolved through the index at run time. This is the bake-off dimension.
- **Diff match** — does the measured before/after diff (`ifc_processor/services/ifc_diff.py`) contain the expected rows with the expected counts?
- **Integrity** — nothing changed outside the selection, no geometry moved. Measured by re-reading the scratch copy the child wrote and diffing it against the original, independently of the pipeline's own gate.
- **Reject / no-change** — requests that must be declined were, and requests that already hold say so.

Also reported: repairs used, latency median and p90, tokens, cost. The corpus lives at `fixtures/benchmark/pipeline-test-prompts.txt` and is bound literally to `fixtures/benchmark/Ifc4_SampleHouse.ifc`. Runs never touch the project's own IFC file and never create a proposal row.

```bash
cd src
uv run manage.py benchmark_writeback --project <uuid> --json ../runs/baseline.json   # save a baseline
uv run manage.py benchmark_writeback --project <uuid> --baseline ../runs/baseline.json  # regression check
uv run manage.py benchmark_writeback --project <uuid> --model ollama:qwen2.5-coder:7b \
    --model anthropic:claude-sonnet-4-6 --repeat 2 --note vram=8GB --json ../runs/bakeoff.json
```

- How to run it, the columns, `--repeat`, safety: [`docs/testing.md` §Natural-Language Benchmark](testing.md#natural-language-benchmark)
- Corpus grammar and the sample-model contract: [`fixtures/benchmark/README.md`](../fixtures/benchmark/README.md)
- The design it measures: [`docs/writeback_V3/`](writeback_V3/README.md)

## Harness C — Ask benchmark

`manage.py benchmark_ask` runs ten fixture-agnostic questions (six Tier 1 with one right answer — counts, storeys, schema — and four Tier 2 narrative ones — materials, spaces, fire rating, external walls) through `RAGService.generate_answer` against four public IFC models (`AC20-FZK-Haus`, `IfcOpenHouse_IFC4`, `Office_A`, `duplex`; sha256-resolved, downloaded to the gitignored `fixtures/ask_corpus/` on first use and parsed into `[bench-ask] <fixture>` projects). Nothing is mocked. Every expected value is computed independently with IfcOpenShell from the fixture at run time, so the corpus carries no hand-typed answers. Scoring is answer-level (does the count, name or keyword appear; was the question refused), not retrieval-level: there is no hit@k or MRR yet.

```bash
cd src
uv run manage.py benchmark_ask                                   # all fixtures, both tiers
uv run manage.py benchmark_ask --fixture duplex --tier 1 --json ../runs/ask.json
```

Corpus and scoring live in `src/chat/services/ask_benchmark/`; the scorer is unit-tested without an LLM in `src/chat/tests/test_ask_benchmark.py`.

## Harness D — RAV / conflict-scan benchmark

The Guardian and the conflict scanner are the RAV (Retrieval-Augmented Verification) surface, and until this harness existed their accuracy was asserted, never measured. `manage.py benchmark_rav` scores `ConflictScanService` against a **planted-conflict corpus**: three consultant-style documents written for the sample house (`fixtures/benchmark/rav/`), every requirement labelled in `key.json` as a planted conflict (with a severity class — clear / marginal / missing-property) or an aligned requirement the scanner must leave alone.

Reported per entity: precision, recall overall and by severity, and "negatives held" (aligned requirements not flagged). `--repeat N` runs every variant N times and adds a mean / min–max table: the spread is the run-to-run variance floor, and `--baseline` judges each delta against it ("exceeds baseline spread" or not). `--ablate` sweeps production settings against each mitigation switched off — the entity-first retrieval passes, value verification, the element-type gate, the requirement-keyword filter, the confidence threshold — which is the before/after-mitigation evidence.

The scanner resolves its model through the Modify purpose (`MODIFY_MODEL`), so the row's model is whatever the site's Modify model is; override per run with the environment variable (`MODIFY_MODEL=llama3.1:8b uv run manage.py benchmark_rav …`). The artifact records the model actually used.

```bash
cd src
uv run manage.py benchmark_rav --project <uuid> --setup                     # upload + process the corpus PDFs (once)
uv run manage.py benchmark_rav --project <uuid> --repeat 3 --json ../runs/rav.json   # score 3×, save artifact
uv run manage.py benchmark_rav --project <uuid> --repeat 3 --baseline ../runs/rav.json  # delta vs the variance floor
uv run manage.py benchmark_rav --project <uuid> --ablate                    # mitigation ablation table
```

Corpus conventions and editing rules: [`fixtures/benchmark/rav/README.md`](../fixtures/benchmark/rav/README.md). Key parsing and the scoring maths are unit-tested without an LLM in `src/writeback/tests/test_benchmark_rav.py`.

### Related tools

- **`manage.py time_snapshot`** — measures `ifcopenshell.open()` and the snapshot on the largest processed file (spec P-1); the numbers live in the evaluation record.
- **`src/writeback/tests/test_benchmark_corpus.py` / `test_benchmark_runner.py` / `test_benchmark_report.py`** — pytest unit tests for the corpus grammar, the index resolution and scoring, and the report columns. No LLM needed; they run in the normal fast suite.
- **`src/ifc_processor/tests/test_ifc_round_trip.py` / `test_ifc_diff_relationships.py`** — round-trip integrity (open → save → diff is empty) and the relationship-derived diff rows (container move, material, classification, group, typed creation and deletion).

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

All harnesses write results under `runs/`, which is **gitignored** — benchmark output is machine- and moment-specific noise by default. **Every artifact an evaluation record cites is the exception and is committed with `git add -f`**, so a reader of the record can open the raw run (every case's outcome, code, explanation, finding) and a future run has something to diff against. `runs/README.md` maps each committed artifact to its record. This convention is defined here; the harness docs point back to this page.

## Evaluation records: `docs/evaluation/`

Significant benchmark runs get a dated write-up in `docs/evaluation/`. These records are **immutable** — they describe what a specific run found on a specific date and are never updated as the system changes; later runs get their own file.

- [2026-08-05 — Natural-Language Write-Back Benchmark](evaluation/2026-08-05-writeback-nl-benchmark.md) — first full run of harness B; found a silent removal-intent corruption (success reported while writing the wrong value across five walls) and a `SET_ATTRIBUTE` crash.
- [2026-09-15 — Writeback V3 bake-off](evaluation/2026-09-15-writeback-v3-bakeoff.md) — first rows of the V3 harness: 7B coder 41/95 on 8 GB, Claude ceiling 65/95, integrity 46/46 by independent re-read, explainer prose 10/10; the 14B and 30B rows pending a larger machine.
- [2026-09-15 — RAV retrieval fix, before/after](evaluation/2026-09-15-rav-retrieval-fix.md) — harness D with `--repeat 3` on two models: recall 0.16 → 0.65 (`qwen2.5-coder:7b`) and 0.20 → 0.77 (`llama3.1:8b`), every key entity reached by its right document; ablation attributes the gain to the retrieval passes and value verification.
- [2026-09-15 — Ask benchmark](evaluation/2026-09-15-ask-benchmark.md) — first published run of harness C: Tier 1 20/22, Tier 2 11/14 on `llama3.1:8b` across four public models; failures named per case.
- [2026-08-30 — RAV Conflict-Scan Benchmark](evaluation/2026-08-30-rav-benchmark.md) — first measured RAV run (harness D): P 0.29 / R 0.20 on the planted corpus, and the finding that the bottleneck is entity↔chunk retrieval (4/15 key entities never retrieved; two conflict cases structurally unreachable at top-K 5), not LLM comparison.

---

**Status note (2026-08-30):** harness code, the NL corpus, the sample IFC, and `docs/evaluation/` are all tracked; a clean clone can run harness B after processing the sample house into a project.
