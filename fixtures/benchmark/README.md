# Writeback benchmark fixtures

> Overview of all Castor benchmarks: [docs/benchmarks.md](../../docs/benchmarks.md).

These two files let `manage.py benchmark_writeback` run the natural-language
corpus against a real model and score the result. Both are **tracked
deliberately** — `.gitignore` ignores `*.ifc` globally and negates these paths,
because a benchmark nobody else can reproduce is not a benchmark.

## `Ifc4_SampleHouse.ifc`

IFC4 sample building from the buildingSMART
[Sample-Test-Files](https://github.com/buildingSMART/Sample-Test-Files)
collection. Public, non-confidential, ~2.3 MB.

The corpus matches entity names in this model **literally**, so it cannot be
swapped for another model without rewriting the prompts. What the prompts rely on:

| | |
|---|---|
| Walls | `Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330` / `:285395` / `:285459` |
| Wall pset | `Pset_WallCommon` — `Reference`, `IsExternal`, `LoadBearing`, `ExtendToStructure`, `ThermalTransmittance`. **No `FireRating`** — this is what exercises the `SET_PROPERTY` → `ADD_PROPERTY` auto-fallback |
| Storeys | `Ground Floor`, `Roof` |

The absent `FireRating` is load-bearing for several cases. If you re-export or
replace this model, check that assumption before trusting a run.

## `pipeline-test-prompts.txt`

92 prompts across 19 sections, covering every routing branch and every reject
category. Each prompt is preceded by comment lines declaring what each pipeline
stage should produce; `benchmark_writeback` parses the `router:` line and turns
it into an assertion.

Keep the `router:` grammar intact when editing — it is executable, not prose:

```
router:  tier 1, SET_PROPERTY            → expect tier 1, operation SET_PROPERTY
router:  tier 0 REJECT  ... "geometry"   → expect rejection containing "geometry"
router:  tier 1, ... or T0 ...           → advisory; reported, never a failure
```

Trailing parenthetical commentary after the operation is ignored by the parser,
so `tier 1, SET_PROPERTY  (Tier1Validator escalates → ADD_PROPERTY)` asserts
only the tier and operation.

## Setting up the benchmark project

The corpus resolves entities from the **database index**, so the model has to be
uploaded and processed once:

1. Create a project and upload `Ifc4_SampleHouse.ifc` to it.
2. Let the IFC pipeline finish (status `completed`).
3. Pass that project's UUID as `--project`.

Runs never modify the project's file: each case executes against a fresh scratch
copy in a temporary directory, which is discarded afterwards.
