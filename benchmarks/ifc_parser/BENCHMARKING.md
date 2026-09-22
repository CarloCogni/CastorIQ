# IFC Parser Benchmarking

> Overview of all Castor benchmarks: [docs/benchmarks.md](../../docs/benchmarks.md).
> This page is the full guide for the parser harness only.

A regression harness that measures Castor's IfcOpenShell-based IFC pipeline
against the shared public corpus used by the open-source IFC community
(originated by Dion Moult, adopted by ThatOpen's engine_web-ifc and
LTplus-AG's ifc-lite). Output is directly diffable against
`engine_web-ifc/benchmark.md`.

## What this measures

Each file is benchmarked in a **fresh subprocess per iteration** (cold
caches, honest cold-start timing, hard wall-clock timeout). Three phases:

| Phase | What runs | Column |
|---|---|---|
| open | `ifcopenshell.open(path)` | Time to open model (ms) |
| extract | Castor's `IFCParser` property/container/description extraction (no DB writes) | CSV only (`t_extract_median_ms`) |
| geom | `ifcopenshell.geom` iterator drained over all products | mesh count |
| open + extract + geom | | Time to execute all (ms) |

- **Total ifc entities** = total STEP instances in the file (`len(model)`),
  the same definition web-ifc uses — so entity counts should match the
  reference **exactly**. A mismatch is flagged `ENTITY_MISMATCH` and should
  be treated as a potential correctness bug, not a performance data point.
- **Total mesh objects** = shapes yielded by the geometry iterator. Parity
  with web-ifc is *not* expected (different tessellation engines, different
  instancing); deltas beyond ±10% are noted as informational `MESH_DELTA`.
- Database sync is deliberately excluded: web-ifc's numbers cover parse +
  geometry only, and Postgres I/O would make runs incomparable and noisy.
- Timings are only comparable **on the same machine**. Against the M1
  numbers in `engine_web-ifc/benchmark.md`, compare ratios between files
  and regressions over time, never absolute milliseconds.

## Setup

```bash
uv sync                                                # ifcopenshell comes from the project venv
uv run python benchmarks/ifc_parser/fetch_fixtures.py  # downloads the corpus
```

The fetcher reads `ifc-lite/tests/models/manifest.json` (submodule must be
checked out: `git submodule update --init ifc-lite`) and downloads the
sha256-verified fixtures from the ifc-lite GitHub release into
`fixtures/parser_corpus/`. Defaults: the `ara3d/*` subset (the files that
overlap web-ifc's benchmark table), skipping files over 100 MB. The corpus
is read-only and gitignored (the global `*.ifc` rule); re-running the
fetcher is a no-op for files already present with a matching hash.

Full default corpus is ~100 MB on disk. To include the giants:

```bash
uv run python benchmarks/ifc_parser/fetch_fixtures.py --skip-larger-than 0
```

## Running

```bash
# Smoke test (one small file):
uv run python benchmarks/ifc_parser/benchmark.py --only "AC20*" --iterations 3

# Full run (defaults: 3 iterations, 60s/file timeout, skip >100MB):
uv run python benchmarks/ifc_parser/benchmark.py

# Useful flags:
#   --iterations 5          more samples, steadier medians
#   --timeout 120           for big files / slow machines
#   --no-geom               parse-only run
#   --no-castor-extract     raw ifcopenshell only (no Django import needed)
#   --threads N             geometry iterator threads
#   --corpus / --output     custom paths
```

Results land in `runs/ifc_parser_bench/results.md` (the exact 7-column
web-ifc table schema) and `results.csv` (full stats: median/mean/min/max
per phase, reference deltas, variance flag).

## Adding test files

- **Any local file**: drop a `.ifc` into `fixtures/parser_corpus/` — the
  harness globs the directory, no registration needed.
- **More manifest fixtures**: widen the fetch filter, e.g.
  `--only "*"` for everything in the manifest (`.ifcx`/`.ifczip` are
  skipped automatically — ifcopenshell doesn't read them).
- **Reference counts**: cross-checks come from parsing
  `engine_web-ifc/benchmark.md` (a local checkout of ThatOpen's repo at the
  Castor root; harness warns and skips checks if absent). Files without a
  reference row simply get no delta columns.

## Interpreting results

- **Errors column empty** → clean pass.
- **`ENTITY_MISMATCH`** → investigate before trusting anything else; the
  entity count definition is engine-independent.
- **`MESH_DELTA`** → informational; only worry if it *changes* between runs
  of the same parser version.
- **`HIGH_VARIANCE †`** → spread across iterations exceeded 20% of the
  median. Close background apps, re-run with `--iterations 5`.
- **`timeout after Ns`** → the file's remaining iterations are skipped;
  raise `--timeout` or investigate a hang.
- **Regression tracking**: keep a baseline with
  `git add -f runs/ifc_parser_bench/results.csv` after a known-good run,
  then diff future runs against it — the `runs/` convention is defined in
  [docs/benchmarks.md](../../docs/benchmarks.md#results-convention-runs).

## Known limitations

- No DB-sync timing — the full Django pipeline (embeddings, Git, Postgres)
  is out of scope by design; see "What this measures".
- The `extract` phase drives the real `IFCParser` methods but only for
  `RELEVANT_TYPES` elements, matching what Castor indexes — it is a
  value-add layer on top of `open`, not a full re-parse.
- One machine's absolute numbers are not comparable to another's.
